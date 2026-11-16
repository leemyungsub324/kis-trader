"""실매매 엔진. 하루를 네 단계로 나눠 돈다.

  preopen (08:50)  전일 종가베팅 물량을 시장가 매도로 접수 → 시가(동시호가)에 체결
  open    (09:01~) 전일 스캔 결과로 지정가 분할 매수 주문, 보유 종목 익절 지정가 주문
  tick    (장중)   체결 반영, 손절 감시, 변동성 돌파 감시
  closing (14:58~) 미체결 매수 취소, 기간만료·규칙 청산, 종가베팅 스캔·매수
  eod     (15:40~) 일봉 갱신 후 다음날 매수 후보 계산

상태는 state.json에 저장하므로 중간에 프로그램을 껐다 켜도 이어서 동작한다.
이 엔진이 직접 매수한 종목만 관리하고, 계좌의 다른 보유 종목은 건드리지 않는다.
"""
from __future__ import annotations

import csv
import json
import logging
import os
import time
from datetime import datetime

import pandas as pd

from .indicators import add_features
from .notify import NullNotifier
from .regime import market_features
from .ticks import shift

log = logging.getLogger("engine")
COLS = ["open", "high", "low", "close", "volume", "value"]


class DryBroker:
    """주문만 가로채는 래퍼. 시세·잔고 조회는 실제 API를 쓰고 주문은 로그만 남긴다."""

    def __init__(self, inner):
        self._in, self._n = inner, 0

    def __getattr__(self, k):
        return getattr(self._in, k)

    def order(self, code, side, qty, price=0):
        self._n += 1
        log.info("[모의실행] 주문 생략 %s %s %d주 @%s", side, code, qty, price or "시장가")
        return {"odno": f"DRY{self._n}", "orgno": ""}

    def cancel(self, odno, orgno):
        return True

    def orders_today(self):
        return {}


class Engine:
    def __init__(self, broker, strategies, cfg: dict, data_dir: str = "data", now=datetime.now, notify=None):
        self.b, self.cfg, self.now = broker, cfg, now
        self.notify = notify or NullNotifier()
        self.S = {s.name: s for s in strategies}
        self.data_dir = data_dir
        self.daily_dir = os.path.join(data_dir, "daily")
        os.makedirs(self.daily_dir, exist_ok=True)
        self.state_path = os.path.join(data_dir, "state.json")
        self.trades_path = os.path.join(data_dir, "trades.csv")
        self.index_code = cfg.get("index_code", "069500")
        self.st = self._load_state()

    # ------------------------------------------------------------------ 상태
    def _load_state(self) -> dict:
        if os.path.exists(self.state_path):
            with open(self.state_path) as f:
                return json.load(f)
        return {"date": "", "positions": {}, "orders": [], "plans": [], "vb": {}, "done": [],
                "regime": "NEUTRAL", "equity0": 0, "halt_entries": False}

    def save(self):
        tmp = self.state_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.st, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.state_path)

    def _trade(self, strat, code, side, qty, price, why):
        new = not os.path.exists(self.trades_path)
        with open(self.trades_path, "a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["time", "strat", "code", "side", "qty", "price", "why"])
            w.writerow([self.now().strftime("%Y-%m-%d %H:%M:%S"), strat, code, side, qty, round(price, 2), why])
        log.info("체결 %s %s %s %d주 @%.0f (%s)", strat, code, side, qty, price, why)
        self.notify.send(f"[체결] {strat} {code} {'매수' if side == 'buy' else '매도'} {qty}주 @{price:,.0f} ({why})")

    def _roll_day(self):
        """거래일이 바뀌면 보유일수를 늘리고 전일 주문 기록을 비운다."""
        today = self.now().strftime("%Y-%m-%d")
        if self.st["date"] == today:
            return
        for p in self.st["positions"].values():
            p["days"] += 1
            p["tp_order"] = None
            p["exiting"] = False
        self.st.update(date=today, orders=[], vb={}, done=[], halt_entries=False, equity0=0, holiday=False)
        self.save()

    def _once(self, step: str) -> bool:
        if step in self.st["done"]:
            return False
        self.st["done"].append(step)
        return True

    # ------------------------------------------------------------------ 데이터
    def _cache(self, code: str) -> pd.DataFrame | None:
        path = os.path.join(self.daily_dir, f"{code}.csv")
        if not os.path.exists(path):
            return None
        return pd.read_csv(path, index_col="date", parse_dates=True)[COLS]

    def update_cache(self, code: str, days: int = 200) -> pd.DataFrame:
        old = self._cache(code)
        if old is None or len(old) < 130:
            df = self.b.daily_history(code, days)
        else:
            new = self.b.daily_history(code, 100)
            # 수정주가가 바뀌었으면(권리락) 겹치는 구간 종가가 달라진다 → 전체 재수신
            common = old.index.intersection(new.index)
            if len(common) and abs(old.loc[common[0], "close"] / new.loc[common[0], "close"] - 1) > 0.005:
                df = self.b.daily_history(code, days)
            else:
                df = pd.concat([old[~old.index.isin(new.index)], new]).sort_index().tail(days + 60)
        df.index.name = "date"
        df.to_csv(os.path.join(self.daily_dir, f"{code}.csv"))
        return df

    def _market(self) -> pd.DataFrame:
        idx = self.update_cache(self.index_code)
        return market_features(idx["close"])

    def _with_today(self, hist: pd.DataFrame, q: dict) -> pd.DataFrame:
        """캐시 일봉 뒤에 장중 현재가로 만든 오늘 봉을 붙인다."""
        today = pd.Timestamp(self.now().date())
        hist = hist[hist.index < today]
        row = pd.DataFrame([{"open": q["open"], "high": q["high"], "low": q["low"], "close": q["price"],
                             "volume": q["volume"], "value": q["value"]}], index=[today])
        return pd.concat([hist, row])

    # ------------------------------------------------------------------ 자금
    def _equity(self, bal: dict) -> float:
        cap = self.cfg.get("capital")
        return float(cap) if cap else bal["equity"]

    def _free_cash(self, bal: dict) -> float:
        hold = sum((o["qty"] - o["filled"]) * o["price"] for o in self.st["orders"]
                   if o["side"] == "buy" and o["open"])
        cash = bal["cash"] - hold
        cap = self.cfg.get("capital")
        if cap:     # 운용 한도를 정했으면 이미 들어간 금액을 뺀 만큼만 쓴다
            used = sum(p["qty"] * p["entry"] for p in self.st["positions"].values()) + hold
            used += self.st.get("park", {}).get("qty", 0) * self.st.get("park", {}).get("px", 0)
            cash = min(cash, float(cap) - used)
        return max(cash, 0.0)

    def _slots(self, s) -> int:
        n = sum(1 for p in self.st["positions"].values() if p["strat"] == s.name)
        n += len({o["code"] for o in self.st["orders"] if o["strat"] == s.name and o["kind"] == "entry"
                  and o["open"] and o["code"] not in self.st["positions"]})
        return s.max_positions - n

    def _place(self, code, strat, side, qty, price, kind, why=""):
        r = self.b.order(code, side, qty, int(price))
        self.st["orders"].append({"odno": r["odno"], "orgno": r["orgno"], "code": code, "strat": strat,
                                  "side": side, "qty": int(qty), "price": int(price), "filled": 0,
                                  "kind": kind, "why": why, "open": True})
        log.info("주문 %s %s %s %d주 @%s [%s]", strat, side, code, qty, int(price) or "시장가", kind)
        self.save()
        return r

    # ------------------------------------------------------------------ 장후 스캔
    def eod_scan(self, universe: pd.DataFrame):
        """일봉을 갱신하고 다음 거래일 매수 후보(plans)를 만든다."""
        mkt = self._market()
        regime = mkt["regime"].iloc[-1]
        strats = [s for s in self.S.values() if s.entry != "close"]
        plans, n = [], len(universe)
        for i, code in enumerate(universe["code"]):
            try:
                df = self.update_cache(code)
            except Exception as e:
                log.warning("일봉 수신 실패 %s: %s", code, e)
                continue
            if i % 100 == 0:
                log.info("일봉 갱신 %d/%d", i, n)
            if len(df) < 130 or df.index[-1] != mkt.index[-1]:
                continue
            f = add_features(df)
            m = mkt.reindex(f.index)
            for s in strats:
                if regime not in s.regimes:
                    continue
                sig, score, _ = s.prepare(f, m)
                if bool(sig.iloc[-1]):
                    plans.append({"strat": s.name, "code": code, "score": float(score.iloc[-1]),
                                  "close": float(f["close"].iloc[-1]), "atr": float(f["atr"].iloc[-1]),
                                  "range": float(f["range"].iloc[-1])})
        plans.sort(key=lambda p: -p["score"])
        self.st.update(plans=plans, regime=regime, plan_date=str(mkt.index[-1].date()))
        self.save()
        log.info("스캔 완료: 국면 %s, 후보 %d건 %s", regime, len(plans),
                 {s.name: sum(1 for p in plans if p["strat"] == s.name) for s in strats})
        return plans

    # ------------------------------------------------------------------ 장전
    def preopen(self):
        """종가베팅 물량 시가 청산: 시장가 매도를 장 시작 전에 접수한다."""
        self._roll_day()
        if not self._once("preopen"):
            return
        self._exit_overnight()
        self.save()

    def _exit_overnight(self):
        for code, p in list(self.st["positions"].items()):
            if self.S[p["strat"]].exit_next_open and p["days"] >= 1 and not p.get("exiting"):
                try:
                    self._exit(code, "시가청산")
                except Exception as e:      # 휴장일 등으로 주문이 거부되면 개장 후 다시 시도
                    log.warning("시가청산 주문 실패 %s: %s", code, e)

    # ------------------------------------------------------------------ 개장
    def open(self):
        self._roll_day()
        if not self._once("open"):
            return
        idx = self.b.price(self.index_code)
        if idx["volume"] <= 0:
            log.info("지수 ETF 거래량 0 → 휴장으로 판단, 오늘은 쉼")
            self.st["halt_entries"] = True
            self.st["holiday"] = True                   # 마감 단계의 기간만료 매도도 건너뛴다
            for p in self.st["positions"].values():     # 휴장일은 보유일수에서 제외
                p["days"] = max(p["days"] - 1, 0)
            self.save()
            return
        bal = self.b.balance()
        self.st["equity0"] = self._equity(bal)
        self._exit_overnight()                          # 장전 단계를 놓쳤을 때 대비
        if self._park(bal):
            bal = self.b.balance()                      # 지수 ETF를 팔았으면 현금이 늘었으니 다시 조회
        # 1) 보유 종목 익절 지정가
        for code, p in self.st["positions"].items():
            self._place_tp(code, p)
        # 2) 신규 진입
        if os.path.exists(os.path.join(self.data_dir, "STOP")):
            log.warning("STOP 파일 존재 → 신규 매수 중단")
            self.st["halt_entries"] = True
        if not self.st["halt_entries"]:
            self._enter_plans(bal)
        self.save()

    def _park(self, bal) -> bool:
        """남는 현금 일부를 지수 ETF에 넣어 둔다(설정 park). 매도 주문을 냈으면 True.

        목표 금액 = frac × (자산 − 보유 종목 − 오늘 매수 예정액), 허용 국면이 아니면 0.
        목표와 2%p 이상 벌어졌을 때만 주문해 잦은 매매를 피한다.
        """
        pk = self.cfg.get("park")
        cur = self.st.setdefault("park", {"qty": 0})
        if not pk and cur["qty"] <= 0:
            return False
        pk = pk or {}
        code = pk.get("code", self.index_code)
        px = self.b.price(code)["price"]
        if px <= 0:
            return False
        cur["px"] = px
        equity = self._equity(bal)
        held = sum(p["qty"] * p["entry"] for p in self.st["positions"].values())
        need = 0.0
        for s in self.S.values():
            n = sum(1 for pl in self.st.get("plans", []) if pl["strat"] == s.name)
            need += min(max(self._slots(s), 0), n) * equity * s.pos_weight
        ok = bool(pk) and self.st["regime"] in pk.get("regimes", ["BULL"]) and not self.st["halt_entries"]
        target = pk.get("frac", 0.5) * max(equity - held - need, 0) if ok else 0.0
        have = cur["qty"] * px
        band = 0.02 * equity
        if have - target > band or (target == 0 and cur["qty"] > 0):
            qty = cur["qty"] if target == 0 else int((have - target) // px)
            if qty >= 1:
                self._place(code, "park", "sell", qty, 0, "park", "지수ETF 축소")
                return True
        elif target - have > band:
            lim = shift(px, 2, etf=True)
            qty = int(min(target - have, max(self._free_cash(bal) - need, 0)) // lim)
            if qty >= 1:
                self._place(code, "park", "buy", qty, lim, "park", "지수ETF 편입")
        return False

    def _enter_plans(self, bal):
        equity = self._equity(bal)
        new_today = {}
        for plan in self.st.get("plans", []):
            s = self.S.get(plan["strat"])
            code = plan["code"]
            if s is None or code in self.st["positions"] or self._slots(s) <= 0:
                continue
            if new_today.get(s.name, 0) >= s.max_new_per_day:
                continue
            new_today[s.name] = new_today.get(s.name, 0) + 1
            if any(o["code"] == code and o["kind"] == "entry" for o in self.st["orders"]):
                continue
            q = self.b.price(code)
            if q["warn"] or q["halted"] or q["open"] <= 0:
                continue
            if abs(q["prev"] / plan["close"] - 1) > 0.005:
                log.info("전일 종가 불일치 → %s 건너뜀(오래된 스캔 결과이거나 권리락)", code)
                continue
            prev = q["prev"]                    # 스캔 때 받은 종가와 0.5% 안에서 다를 수 있어 실제 전일 종가를 쓴다
            gap = q["open"] / prev - 1
            if gap < s.gap_min or gap > s.gap_max:
                log.info("갭 %.1f%% → %s 진입 포기", gap * 100, code)
                continue
            budget = min(equity * s.weight(plan["close"], plan["atr"]) * s.regime_scale.get(self.st["regime"], 1.0),
                         self._free_cash(bal))
            if s.entry == "vb":
                trig = q["open"] + s.vb_k * plan["range"]
                self.st["vb"][code] = {"strat": s.name, "trigger": trig, "budget": budget, "atr": plan["atr"]}
                continue
            if s.entry == "open":
                px = min(shift(q["price"], 2), int(q["upper"]) or 10**9)
                if budget // px >= 1:
                    self._place(code, s.name, "buy", budget // px, px, "entry")
                continue
            ref = prev if s.ladder_ref == "close" else q["price"]
            for lim, w in zip(s.ladder_prices(ref, plan["atr"]), s.ladder_weights):
                px = max(lim, int(q["lower"]))
                qty = int(budget * w // px)
                if qty >= 1:
                    self._place(code, s.name, "buy", qty, px, "entry")
            self._meta(code, plan)

    def _meta(self, code, plan):
        self.st.setdefault("meta", {})[code] = {"atr": plan.get("atr", float("nan"))}

    def _place_tp(self, code, p):
        s = self.S[p["strat"]]
        if not s.tp_pct or p.get("tp_done") or p.get("exiting") or s.exit_next_open or p.get("tp_order"):
            return
        qty = p["qty"] if s.tp_frac >= 1 else max(1, int(p["qty"] * s.tp_frac))
        px = shift(p["entry"] * (1 + s.tp_pct), 0)
        try:
            q = self.b.price(code)
            if q["upper"] and px > q["upper"]:
                return                          # 상한가보다 높으면 주문 불가
            r = self._place(code, p["strat"], "sell", qty, px, "tp", "익절")
            p["tp_order"] = r["odno"]
        except Exception as e:
            log.warning("익절 주문 실패 %s: %s", code, e)

    # ------------------------------------------------------------------ 장중
    def sync_fills(self):
        if not any(o["open"] for o in self.st["orders"]):
            return
        od = self.b.orders_today()
        for o in list(self.st["orders"]):
            r = od.get(o["odno"])
            if r is None:
                continue
            d = r["filled"] - o["filled"]
            if d > 0:
                self._apply_fill(o, d, r["avg"] or o["price"])
                o["filled"] = r["filled"]
            if r["cancelled"] or r["filled"] >= o["qty"]:
                o["open"] = False
        self.save()

    def _apply_fill(self, o, qty, avg):
        code, pos = o["code"], self.st["positions"]
        self._trade(o["strat"], code, o["side"], qty, avg, o["why"] or o["kind"])
        if o["kind"] == "park":
            pk = self.st.setdefault("park", {"qty": 0})
            pk["qty"] = max(pk["qty"] + (qty if o["side"] == "buy" else -qty), 0)
            return
        s = self.S[o["strat"]]
        if o["side"] == "buy":
            p = pos.get(code)
            atr = self.st.get("meta", {}).get(code, {}).get("atr", float("nan"))
            if p is None:
                p = pos[code] = {"strat": o["strat"], "qty": 0, "entry": 0.0, "days": 0, "atr": atr,
                                 "date_in": self.st["date"], "high": avg, "tp_done": False,
                                 "tp_order": None, "exiting": False}
            p["entry"] = (p["entry"] * p["qty"] + avg * qty) / (p["qty"] + qty)
            p["qty"] += qty
            p["stop"] = s.stop_price(p["entry"], p["atr"])
            if p["tp_order"]:                       # 수량·평단이 바뀌었으니 익절 주문 다시
                self._cancel(p["tp_order"])
                p["tp_order"] = None
            self._place_tp(code, p)
        else:
            p = pos.get(code)
            if p is None:
                return
            p["qty"] -= qty
            if o["kind"] == "tp":
                p["tp_done"] = True
            if p["qty"] <= 0:
                del pos[code]

    def _cancel(self, odno):
        for o in self.st["orders"]:
            if o["odno"] == odno and o["open"]:
                self.b.cancel(o["odno"], o["orgno"])
                o["open"] = False

    def _exit(self, code, why):
        """포지션 전량 시장가 청산. 걸려 있는 익절·매수 주문은 먼저 취소한다."""
        p = self.st["positions"][code]
        for o in self.st["orders"]:
            if o["code"] == code and o["open"] and o["kind"] in ("tp", "entry"):
                self.b.cancel(o["odno"], o["orgno"])
                o["open"] = False
        p["tp_order"] = None
        self.sync_fills()                           # 취소 직전 체결분 반영
        p = self.st["positions"].get(code)
        if p is None or p["qty"] <= 0:
            return
        self._place(code, p["strat"], "sell", p["qty"], 0, "exit", why)
        p["exiting"] = True

    def tick(self):
        self._roll_day()
        self.sync_fills()
        for code, p in list(self.st["positions"].items()):
            if p.get("exiting"):
                continue
            s = self.S[p["strat"]]
            try:
                px = self.b.price(code)["price"]
            except Exception as e:
                log.warning("시세 조회 실패 %s: %s", code, e)
                continue
            if px <= 0:
                continue
            p["high"] = max(p["high"], px)
            if s.trail_atr and p["atr"] == p["atr"]:
                p["stop"] = max(p["stop"], p["high"] - s.trail_atr * p["atr"])
            if s.stop_pct or s.stop_atr:
                if px <= p["stop"]:
                    self._exit(code, "손절")
        for code, v in list(self.st["vb"].items()):
            s = self.S[v["strat"]]
            if code in self.st["positions"] or self._slots(s) <= 0 or self.st["halt_entries"]:
                continue
            q = self.b.price(code)
            if q["price"] >= v["trigger"]:
                px = shift(q["price"], 2)
                if v["budget"] // px >= 1:
                    self._meta(code, v)
                    self._place(code, s.name, "buy", int(v["budget"] // px), px, "entry")
                del self.st["vb"][code]
        self._loss_guard()
        self.save()

    def _loss_guard(self):
        """당일 손실이 한도를 넘으면 신규 매수를 멈춘다(5분에 한 번 확인)."""
        lim = self.cfg.get("daily_loss_limit")
        if not lim or self.st["halt_entries"] or not self.st["equity0"] or self.cfg.get("capital"):
            return
        t = time.time()
        if t - getattr(self, "_guard_t", 0) < 300:
            return
        self._guard_t = t
        eq = self.b.balance()["equity"]
        if eq < self.st["equity0"] * (1 - lim):
            log.warning("당일 손실 한도(%.0f%%) 초과 → 신규 매수 중단", lim * 100)
            self.notify.send(f"[경고] 당일 손실 한도 {lim:.0%} 초과 → 오늘 신규 매수 중단")
            self.st["halt_entries"] = True
            for o in self.st["orders"]:
                if o["open"] and o["kind"] == "entry":
                    self.b.cancel(o["odno"], o["orgno"])
                    o["open"] = False

    # ------------------------------------------------------------------ 마감
    def closing(self, universe: pd.DataFrame | None = None, deadline: str = "15:18"):
        self._roll_day()
        if not self._once("closing"):
            return
        if self.st.get("holiday"):
            log.info("휴장일 → 마감 단계(기간만료·규칙 청산, 종가 진입) 생략")
            return
        # 1) 미체결 매수 취소
        for o in self.st["orders"]:
            if o["open"] and o["kind"] == "entry":
                self.b.cancel(o["odno"], o["orgno"])
                o["open"] = False
        self.sync_fills()
        self.st["vb"] = {}
        # 2) 기간만료·규칙 청산
        mkt = None
        for code, p in list(self.st["positions"].items()):
            s = self.S[p["strat"]]
            if p.get("exiting") or s.exit_next_open:
                continue
            if p["days"] >= s.max_hold:
                self._exit(code, "기간만료")
                continue
            hist = self._cache(code)
            if hist is None:
                continue
            f = add_features(self._with_today(hist, self.b.price(code)))
            mkt = mkt if mkt is not None else self._market()
            _, _, ex = s.prepare(f, mkt.reindex(f.index).ffill())
            if p["days"] >= 1 and bool(ex.iloc[-1]):    # 규칙 청산은 매수 다음 날부터(백테스트와 같음)
                self._exit(code, "규칙청산")
        # 3) 종가 진입 전략(종가베팅)
        closers = [s for s in self.S.values() if s.entry == "close" and self.st["regime"] in s.regimes]
        if closers and universe is not None and not self.st["halt_entries"]:
            self._close_entries(closers, universe, deadline)
        self.save()

    def _close_entries(self, strats, universe, deadline):
        mkt = self._market()
        cands = []
        for code in universe["code"]:
            if self.now().strftime("%H:%M") >= deadline:
                log.warning("마감 스캔 시간 초과 → 여기까지만 반영")
                break
            if code in self.st["positions"]:
                continue
            hist = self._cache(code)
            if hist is None or len(hist) < 60 or hist["value"].tail(20).mean() < self.cfg.get("scan_min_value", 1e9):
                continue
            try:
                q = self.b.price(code)
            except Exception:
                continue
            if q["warn"] or q["halted"] or q["prev"] <= 0:
                continue
            live = [s for s in strats if s.quick(q)]
            if not live:
                continue
            f = add_features(self._with_today(hist, q))
            m = mkt.reindex(f.index).ffill()
            for s in live:
                sig, score, _ = s.prepare(f, m)
                if bool(sig.iloc[-1]):
                    cands.append((float(score.iloc[-1]), s.name, code, q))
        cands.sort(key=lambda c: -c[0])
        bal = self.b.balance()
        equity = self._equity(bal)
        for _, name, code, q in cands:
            s = self.S[name]
            if self._slots(s) <= 0 or code in self.st["positions"]:
                continue
            budget = min(equity * s.pos_weight * s.regime_scale.get(self.st["regime"], 1.0), self._free_cash(bal))
            px = min(shift(q["price"], 3), int(q["upper"]) or 10**9)     # 3틱 위 지정가 = 사실상 시장가
            if budget // px >= 1:
                self._place(code, name, "buy", int(budget // px), px, "entry")
        log.info("종가 진입 후보 %d건", len(cands))

    # ------------------------------------------------------------------ 루프
    def brief(self, t: str) -> bool:
        """매시 정각(설정 briefing.hours) 시황 브리핑을 텔레그램으로 보낸다. 시각당 한 번, 정각 후 30분까지 허용
        (마감 스캔 등으로 루프가 늦어져도 보내도록). 실패해도 매매 루프에는 영향을 주지 않는다."""
        bc = self.cfg.get("briefing")
        if not bc or not bc.get("enabled", True) or not self.notify.enabled:
            return False
        hh, mm = t[:2], t[3:5]
        hours = [str(h).zfill(2) for h in bc.get("hours", range(9, 16))]
        if hh not in hours or mm >= "30":
            return False
        self._roll_day()
        if self.st.get("holiday") or not self._once(f"brief{hh}"):
            return False
        from .briefing import collect, render
        try:
            d = collect(self.b, self.st["positions"], bc.get("min_value", 3e9), bc.get("top", 5))
            if not d["idx"] or all(i["value"] <= 0 for i in d["idx"].values()):
                log.info("지수 거래대금 0 → 휴장으로 보고 브리핑 생략")
                return False
            at, prev = getattr(self, "_brief_prev", (None, {}))
            if at is None or (self.now() - at).total_seconds() > 5400:
                prev = {}                       # 1시간 변화는 같은 날 직전 시각 브리핑이 있을 때만
            self.notify.send(render(d, self.now().strftime("%m/%d %H:%M"), prev, self.st.get("regime", "")))
            self._brief_prev = (self.now(), {k: v["price"] for k, v in d["idx"].items()})
            log.info("시황 브리핑 전송 %s시", hh)
            return True
        except Exception:
            log.exception("시황 브리핑 실패")
            return False
        finally:
            self.save()

    def phase(self, t: str) -> str:
        """HH:MM → 그 시각에 할 일. 구간 사이(08:45~08:50, 09:00~09:01)는 아무것도 하지 않는다.
        (예전에는 이 틈이 마감 분기로 떨어져 08:45에 closing 이 돌고, 정작 14:58에는 건너뛰었다.)"""
        cs = self.cfg.get("close_scan_start", "14:58")
        if "08:50" <= t < "09:00":
            return "preopen"
        if "09:01" <= t < cs:
            return "intraday"
        if cs <= t < "15:20":
            return "closing"
        if "15:20" <= t < "15:40":
            return "post"
        if "15:40" <= t < "18:00":
            return "eod"
        return "idle"

    def run(self, universe_fn, poll: int = 20):
        """하루 종일 도는 메인 루프. universe_fn()은 종목 목록 DataFrame을 돌려준다."""
        uni, fails = None, 0
        while True:
            self._beat()
            t = self.now().strftime("%H:%M")
            wd = self.now().weekday() < 5
            try:
                if not wd or t < "08:45" or t >= "18:00":
                    time.sleep(60)
                    continue
                if uni is None:
                    uni = universe_fn()
                self.brief(t)
                step = self.phase(t)
                if step == "preopen":
                    self.preopen()
                elif step == "intraday":
                    self.open()
                    if not (self.st["halt_entries"] and not self.st["positions"]):
                        self.tick()
                elif step == "closing":
                    self.closing(uni)
                    self.sync_fills()
                elif step == "post":
                    self.sync_fills()
                elif step == "eod" and self._once("eod"):
                    self.sync_fills()
                    self.eod_scan(uni)
                    uni = None
                    self._daily_summary()
                fails = 0
            except Exception:
                fails += 1
                log.exception("루프 오류 (%d회 연속)", fails)
                if fails == 1:
                    self.notify.send("[오류] 매매 루프 오류 발생. 로그를 확인하세요 (같은 오류가 이어져도 10회째까지 다시 알리지 않음).")
                if fails >= 10:
                    log.error("연속 오류 10회 → 종료. 미체결 주문과 잔고를 직접 확인하세요.")
                    self.notify.send("[중단] 연속 오류 10회 → 프로그램 종료. 미체결 주문과 잔고를 직접 확인하세요.")
                    raise
            time.sleep(poll)

    # ------------------------------------------------------------------ 운영 (v1 에서 이식)
    def _beat(self):
        """Docker 헬스체크용 heartbeat (kis_trader.healthcheck 가 파일 신선도를 본다)."""
        try:
            with open(os.path.join(self.data_dir, "heartbeat"), "w") as f:
                f.write(str(time.time()))
        except OSError:
            log.warning("heartbeat 기록 실패")

    def _daily_summary(self):
        """장후 텔레그램 일일 요약: 평가금액, 보유, 오늘 체결 건수, 내일 후보 수."""
        if not self.notify.enabled:
            return
        today = self.now().strftime("%Y-%m-%d")
        n = 0
        if os.path.exists(self.trades_path):
            with open(self.trades_path, newline="") as f:
                n = sum(1 for r in csv.DictReader(f) if r["time"].startswith(today))
        try:
            eq = f"{self.b.balance()['equity']:,.0f}원"
        except Exception:
            eq = "조회 실패"
        pos = ", ".join(f"{c}({p['strat']} {p['qty']}주)" for c, p in self.st["positions"].items()) or "없음"
        park = self.st.get("park", {}).get("qty", 0)
        self.notify.send(f"[일일 요약 {today}] 국면 {self.st['regime']} | 평가금액 {eq}\n"
                         f"보유: {pos} | 지수ETF {park}주\n오늘 체결 {n}건 | 내일 후보 {len(self.st.get('plans', []))}건")
