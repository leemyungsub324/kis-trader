"""일봉 기반 포트폴리오 백테스터.

체결 가정(일봉만으로 판단하므로 불리한 쪽으로 잡는다)
- 지정가 매수: 당일 저가가 주문가보다 1틱 더 내려가야 체결(주문가에 닿기만 한 경우는 미체결).
  시가가 주문가보다 낮게 시작하면 시가에 체결.
- 손절: 저가가 손절가 이하이면 min(손절가, 시가)에서 슬리피지를 더 빼고 체결.
- 익절 지정가: 진입 당일은 체결로 보지 않는다(고가가 진입 전인지 후인지 알 수 없음).
- 같은 날 손절·익절이 모두 닿으면 손절로 처리.
- 비용: 매수·매도 수수료, 매도 시 증권거래세.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from .indicators import add_features
from .regime import market_features
from .ticks import shift, tick_size


@dataclass
class Costs:
    fee: float = 0.00015        # 편도 수수료 0.015%
    tax: float = 0.0020         # 매도 시 증권거래세 0.20% (2026년 기준)
    slip_stop: float = 0.002    # 손절·시장성 주문 슬리피지
    slip_close: float = 0.001   # 종가 부근 청산 슬리피지
    strict_fill: bool = True    # 지정가는 1틱 뚫려야 체결


@dataclass
class Pos:
    code: str
    strat: str
    qty: int
    entry: float
    day_in: int
    stop: float
    tp: float | None
    atr: float
    high: float
    tp_done: bool = False


@dataclass
class Result:
    equity: pd.Series
    trades: pd.DataFrame
    orders: dict = field(default_factory=dict)   # 전략별 (주문 건수, 체결 건수)
    exposure: pd.Series | None = None            # 일별 주식 비중(0~1)

    def summary(self) -> dict:
        eq = self.equity
        years = max((eq.index[-1] - eq.index[0]).days / 365.25, 1e-9)
        total = eq.iloc[-1] / eq.iloc[0] - 1
        dd = (eq / eq.cummax() - 1).min()
        m = eq.resample("ME").last().pct_change().dropna()
        return {
            "총수익률": total, "연환산": (1 + total) ** (1 / years) - 1, "MDD": dd,
            "월평균": m.mean(), "월중앙값": m.median(), "수익월비율": (m > 0).mean(),
            "최악의달": m.min(), "최고의달": m.max(), "거래수": len(self.trades),
        }

    def by_strategy(self) -> pd.DataFrame:
        t = self.trades
        if t.empty:
            return pd.DataFrame()
        g = t.groupby("strat")
        win = t[t.ret > 0].groupby("strat").pnl.sum()
        loss = -t[t.ret <= 0].groupby("strat").pnl.sum()
        out = pd.DataFrame({
            "거래수": g.size(), "승률": g.ret.apply(lambda x: (x > 0).mean()),
            "평균수익": g.ret.mean(), "평균이익": g.ret.apply(lambda x: x[x > 0].mean()),
            "평균손실": g.ret.apply(lambda x: x[x <= 0].mean()),
            "손익비PF": win / loss.replace(0, np.nan), "평균보유일": g.days.mean(),
            "손익합(원)": g.pnl.sum(),
        })
        if self.orders:
            out["체결률"] = pd.Series({k: (v[1] / v[0] if v[0] else np.nan) for k, v in self.orders.items()})
        return out


class Panel:
    """종목×날짜 행렬 묶음. 전략 신호를 미리 계산해 둔다."""

    def __init__(self, data: dict, strategies: list, index_close: pd.Series):
        self.dates = index_close.index
        self.codes = list(data)
        self.market = market_features(index_close)
        self.regime = self.market["regime"].to_numpy()
        n, m = len(self.dates), len(self.codes)
        self.O, self.H, self.L, self.C, self.ATR, self.RNG = (np.full((n, m), np.nan) for _ in range(6))
        self.sig = {s.name: np.zeros((n, m), bool) for s in strategies}
        self.score = {s.name: np.full((n, m), np.nan) for s in strategies}
        self.exit = {s.name: np.zeros((n, m), bool) for s in strategies}
        for j, code in enumerate(self.codes):
            f = add_features(data[code])
            mk = self.market.reindex(f.index)
            ix = self.dates.get_indexer(f.index)
            ok = ix >= 0
            ix = ix[ok]
            for arr, col in ((self.O, "open"), (self.H, "high"), (self.L, "low"),
                             (self.C, "close"), (self.ATR, "atr"), (self.RNG, "range")):
                arr[ix, j] = f[col].to_numpy()[ok]
            for s in strategies:
                sig, score, ex = s.prepare(f, mk)
                self.sig[s.name][ix, j] = sig.fillna(False).to_numpy(bool)[ok]
                self.score[s.name][ix, j] = score.to_numpy(float)[ok]
                self.exit[s.name][ix, j] = ex.fillna(False).to_numpy(bool)[ok]


def run(panel: Panel, strategies: list, cash: float = 5_000_000, costs: Costs = Costs(),
        start=None, end=None) -> Result:
    P, c = panel, costs
    dates = P.dates
    i0 = 0 if start is None else int(dates.searchsorted(pd.Timestamp(start)))
    i1 = len(dates) if end is None else int(dates.searchsorted(pd.Timestamp(end), side="right"))
    S = {s.name: s for s in strategies}
    pos: list[Pos] = []
    trades, eq, expo = [], [], []
    orders = {s.name: [0, 0] for s in strategies}
    last_px: dict[str, float] = {}

    def sell(p: Pos, qty: int, px: float, i: int, why: str):
        nonlocal cash
        cash += qty * px * (1 - c.fee - c.tax)
        cost = qty * p.entry * (1 + c.fee)
        pnl = qty * px * (1 - c.fee - c.tax) - cost
        trades.append((p.strat, p.code, dates[p.day_in], dates[i], i - p.day_in, p.entry, px, pnl / cost, pnl, why))
        p.qty -= qty

    for i in range(max(i0, 1), i1):
        equity_prev = cash + sum(p.qty * last_px.get(p.code, p.entry) for p in pos)
        # ---------- 1) 보유 포지션 청산 ----------
        for p in list(pos):
            j = code_idx[p.code]
            o, h, l, cl = P.O[i, j], P.H[i, j], P.L[i, j], P.C[i, j]
            if o != o:      # 거래정지
                continue
            s = S[p.strat]
            if s.exit_next_open:
                sell(p, p.qty, o * (1 - c.slip_close), i, "시가청산")
            elif l <= p.stop:
                sell(p, p.qty, min(p.stop, o) * (1 - c.slip_stop), i, "손절")
            else:
                if p.tp and not p.tp_done and h >= p.tp:
                    q = p.qty if s.tp_frac >= 1 else max(1, int(p.qty * s.tp_frac))
                    sell(p, q, max(p.tp, o), i, "익절")
                    p.tp_done = True
                if p.qty > 0:
                    held = i - p.day_in
                    if P.exit[p.strat][i, j]:
                        sell(p, p.qty, cl * (1 - c.slip_close), i, "규칙청산")
                    elif held >= s.max_hold:
                        sell(p, p.qty, cl * (1 - c.slip_close), i, "기간만료")
                    else:
                        p.high = max(p.high, h)
                        if s.trail_atr and p.atr == p.atr:
                            p.stop = max(p.stop, p.high - s.trail_atr * p.atr)
            if p.qty <= 0:
                pos.remove(p)
        # ---------- 2) 전일 신호로 신규 진입 ----------
        held_codes = {p.code for p in pos}
        for s in strategies:
            k = i if s.entry == "close" else i - 1      # 신호일(종가 진입은 당일 신호)
            reg = P.regime[k]
            if reg not in s.regimes:
                continue
            slots = s.max_positions - sum(1 for p in pos if p.strat == s.name)
            if slots <= 0:
                continue
            cand = np.flatnonzero(P.sig[s.name][k])
            if cand.size == 0:
                continue
            cand = cand[np.argsort(-np.nan_to_num(P.score[s.name][k, cand], nan=-1e18))]
            slots = min(slots, s.max_new_per_day)
            for j in cand:
                if slots <= 0:
                    break
                budget = equity_prev * s.weight(P.C[k, j], P.ATR[k, j]) * s.regime_scale.get(reg, 1.0)
                code = P.codes[j]
                o, h, l, cl, pc = P.O[i, j], P.H[i, j], P.L[i, j], P.C[i, j], P.C[i - 1, j]
                if code in held_codes or o != o or pc != pc:
                    continue
                slots -= 1                      # 주문을 냈으면 체결 여부와 무관하게 슬롯 사용
                orders[s.name][0] += 1
                gap = o / pc - 1
                if s.entry != "close" and (gap < s.gap_min or gap > s.gap_max):
                    continue
                qty, amt = 0, 0.0
                if s.entry in ("close", "open"):
                    px = (cl if s.entry == "close" else o) * (1 + c.slip_close)
                    qty = int(min(budget, cash) // (px * (1 + c.fee)))
                    amt = qty * px
                elif s.entry == "vb":
                    trig = o + s.vb_k * P.RNG[i - 1, j]
                    if h >= trig and trig > o:
                        px = trig * (1 + c.slip_stop)
                        qty = int(min(budget, cash) // (px * (1 + c.fee)))
                        amt = qty * px
                else:
                    ref = pc if s.ladder_ref == "close" else o
                    for lim, w in zip(s.ladder_prices(ref, P.ATR[k, j]), s.ladder_weights):
                        need = lim - tick_size(lim - 1) if c.strict_fill else lim
                        if o <= lim:
                            px = o          # 시가가 주문가 아래 → 시가 체결
                        elif l <= need:
                            px = lim
                        else:
                            continue
                        q = int(min(budget * w, cash - amt * (1 + c.fee)) // (px * (1 + c.fee)))
                        if q > 0:
                            qty += q
                            amt += q * px
                if qty <= 0:
                    continue
                orders[s.name][1] += 1
                entry = amt / qty
                cash -= amt * (1 + c.fee)
                a = P.ATR[k, j]
                p = Pos(code, s.name, qty, entry, i, s.stop_price(entry, a),
                        entry * (1 + s.tp_pct) if s.tp_pct else None, a, cl)
                held_codes.add(code)
                if s.entry in ("ladder", "open") and l <= p.stop:      # 진입 당일 손절
                    sell(p, p.qty, p.stop * (1 - c.slip_stop), i, "손절")
                else:
                    pos.append(p)
        # ---------- 3) 평가 ----------
        for p in pos:
            v = P.C[i, code_idx[p.code]]
            if v == v:
                last_px[p.code] = v
        eq.append(cash + sum(p.qty * last_px.get(p.code, p.entry) for p in pos))
        expo.append(1 - cash / eq[-1])

    cols = ["strat", "code", "date_in", "date_out", "days", "entry", "exit", "ret", "pnl", "why"]
    return Result(pd.Series(eq, index=dates[max(i0, 1):i1]), pd.DataFrame(trades, columns=cols),
                  {k: tuple(v) for k, v in orders.items()},
                  pd.Series(expo, index=dates[max(i0, 1):i1]))


code_idx: dict[str, int] = {}


def backtest(data: dict, strategies: list, index_close: pd.Series, **kw) -> Result:
    panel = Panel(data, strategies, index_close)
    return run_panel(panel, strategies, **kw)


def run_panel(panel: Panel, strategies: list, **kw) -> Result:
    global code_idx
    code_idx = {c: j for j, c in enumerate(panel.codes)}
    return run(panel, strategies, **kw)
