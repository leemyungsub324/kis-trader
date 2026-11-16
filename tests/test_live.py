import json
from datetime import datetime

import numpy as np
import pandas as pd
import pytest

from kis_trader.engine import Engine
from kis_trader.kis_api import KisClient, KisError
from kis_trader.strategies import Reversal, Surge


# ------------------------------------------------------------ API 클라이언트
class Resp:
    def __init__(self, body, headers=None):
        self._b, self.headers = body, headers or {}

    def json(self):
        return self._b


class FakeHTTP:
    def __init__(self):
        self.calls, self.queue = [], []

    def post(self, url, **kw):
        self.calls.append(("POST", url, kw))
        if url.endswith("/oauth2/tokenP"):
            return Resp({"access_token": "T", "expires_in": 86400})
        return self.queue.pop(0)

    def get(self, url, **kw):
        self.calls.append(("GET", url, kw))
        return self.queue.pop(0)


def client(tmp_path, env="mock"):
    h = FakeHTTP()
    return KisClient("k" * 36, "s", "12345678-01", env, token_dir=str(tmp_path), session=h, sleep=lambda s: None), h


def test_order_uses_mock_tr_and_string_fields(tmp_path):
    c, h = client(tmp_path)
    h.queue.append(Resp({"rt_cd": "0", "output": {"ODNO": "0000012345", "KRX_FWDG_ORD_ORGNO": "91252"}}))
    r = c.order("005930", "buy", 3, 70000)
    kw = h.calls[-1][2]
    assert h.calls[-1][1].startswith("https://openapivts.koreainvestment.com:29443")
    assert kw["headers"]["tr_id"] == "VTTC0012U"
    assert kw["json"]["ORD_DVSN"] == "00" and kw["json"]["ORD_QTY"] == "3" and kw["json"]["ORD_UNPR"] == "70000"
    assert kw["json"]["CANO"] == "12345678" and kw["json"]["ACNT_PRDT_CD"] == "01"
    assert r == {"odno": "12345", "orgno": "91252"}


def test_real_env_tr_ids_and_market_order(tmp_path):
    c, h = client(tmp_path, "real")
    h.queue.append(Resp({"rt_cd": "0", "output": {"ODNO": "1", "KRX_FWDG_ORD_ORGNO": "9"}}))
    c.order("005930", "sell", 1)
    kw = h.calls[-1][2]
    assert kw["headers"]["tr_id"] == "TTTC0011U" and kw["json"]["ORD_DVSN"] == "01" and kw["json"]["SLL_TYPE"] == "01"


def test_token_is_cached_and_rate_limit_retried(tmp_path):
    c, h = client(tmp_path)
    ok = {"rt_cd": "0", "output": {"stck_prpr": "100", "stck_sdpr": "90"}}
    h.queue += [Resp({"rt_cd": "1", "msg_cd": "EGW00201", "msg1": "초당 거래건수를 초과하였습니다."}), Resp(ok), Resp(ok)]
    assert c.price("005930")["price"] == 100
    c.price("005930")
    assert sum(1 for m, u, _ in h.calls if u.endswith("tokenP")) == 1
    c2, h2 = client(tmp_path)          # 새 프로세스도 파일 캐시의 토큰을 재사용
    h2.queue.append(Resp(ok))
    c2.price("005930")
    assert not any(u.endswith("tokenP") for _, u, _ in h2.calls)


def test_order_error_is_not_retried(tmp_path):
    c, h = client(tmp_path)
    h.queue.append(Resp({"rt_cd": "1", "msg_cd": "APBK0919", "msg1": "주문가능금액을 초과"}))
    with pytest.raises(KisError):
        c.order("005930", "buy", 1, 100)
    assert sum(1 for m, u, _ in h.calls if "order-cash" in u) == 1


def test_daily_parsing(tmp_path):
    c, h = client(tmp_path)
    rows = [{"stck_bsop_date": "20261112", "stck_oprc": "10", "stck_hgpr": "12", "stck_lwpr": "9", "stck_clpr": "11",
             "acml_vol": "100", "acml_tr_pbmn": "1100"},
            {"stck_bsop_date": "20261111", "stck_oprc": "9", "stck_hgpr": "10", "stck_lwpr": "8", "stck_clpr": "10",
             "acml_vol": "0", "acml_tr_pbmn": "0"}]
    h.queue.append(Resp({"rt_cd": "0", "output2": rows}))
    d = c.daily("005930", "20261101", "20261112")
    assert len(d) == 1 and d["close"].iloc[0] == 11 and d.index[0] == pd.Timestamp("2026-11-12")


# ------------------------------------------------------------ 엔진
class FakeBroker:
    def __init__(self):
        self.q, self.sent, self.book, self.n, self.cancelled = {}, [], {}, 0, []
        self.cash, self.equity = 10_000_000, 10_000_000

    def set(self, code, price, prev=None, open_=None, volume=1e6, value=1e10, **kw):
        prev = prev or price
        self.q[code] = dict(price=price, open=open_ or price, high=max(price, open_ or price),
                            low=min(price, open_ or price), prev=prev, volume=volume, value=value,
                            upper=prev * 1.3, lower=prev * 0.7, warn=False, halted=False, **kw)

    def price(self, code):
        return self.q[code]

    def order(self, code, side, qty, price=0):
        self.n += 1
        o = {"odno": str(self.n), "orgno": "1"}
        self.sent.append((code, side, qty, price))
        self.book[o["odno"]] = {"code": code, "side": side, "qty": qty, "filled": 0, "avg": 0.0,
                                "remain": qty, "cancelled": False, "price": price}
        return o

    def fill(self, odno, px=None):
        b = self.book[odno]
        b.update(filled=b["qty"], remain=0, avg=px or b["price"])

    def cancel(self, odno, orgno):
        self.cancelled.append(odno)
        self.book[odno]["cancelled"] = True
        return True

    def orders_today(self):
        return self.book

    def balance(self):
        return {"cash": self.cash, "equity": self.equity, "holdings": {}}

    def daily_history(self, code, days=200):
        idx = pd.bdate_range(end="2026-11-13", periods=days)
        return pd.DataFrame({"open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0, "volume": 1e6, "value": 1e9}, index=idx)


class Clock:
    def __init__(self, s):
        self.t = datetime.fromisoformat(s)

    def __call__(self):
        return self.t


def make(tmp_path, when="2026-11-16 09:01:00"):
    b, clk = FakeBroker(), Clock(when)
    b.set("069500", 30000)
    e = Engine(b, [Reversal(), Surge()], {"index_code": "069500"}, data_dir=str(tmp_path), now=clk)
    return e, b, clk


def test_ladder_entry_then_fill_stop_tp_and_exit(tmp_path):
    e, b, clk = make(tmp_path)
    e.st.update(plans=[{"strat": "reversal", "code": "000010", "score": 1, "close": 10000, "atr": 300, "range": 400}],
                regime="BEAR")
    b.set("000010", 9950, prev=10000, open_=9950)
    e.open()
    buys = [s for s in b.sent if s[1] == "buy"]
    assert [p for _, _, _, p in buys] == [9970, 9940, 9900]          # 전일 종가 -3/-6/-10틱
    assert [q for _, _, q, _ in buys] == [80, 60, 60]                 # 1000만×20%를 4:3:3으로
    b.fill("1")
    e.tick()
    p = e.st["positions"]["000010"]
    assert p["qty"] == 80 and p["entry"] == 9970 and abs(p["stop"] - 9970 * 0.85) < 1e-6
    assert b.sent[-1] == ("000010", "sell", 80, 11160)                # 익절 지정가 +12% (호가 내림)
    b.set("000010", 8400, prev=10000)                                 # 손절가 아래
    e.tick()
    assert b.sent[-1] == ("000010", "sell", 80, 0)                    # 시장가 청산
    assert {"2", "3", "4"} <= set(b.cancelled)                        # 남은 매수·익절 주문 취소
    b.fill("5", 8390)
    e.tick()
    assert "000010" not in e.st["positions"]
    rows = (tmp_path / "trades.csv").read_text().strip().splitlines()
    assert len(rows) == 3 and rows[-1].endswith("손절")
    # 재시작해도 상태가 이어진다
    e2 = Engine(b, [Reversal(), Surge()], {}, data_dir=str(tmp_path), now=clk)
    assert e2.st["done"] == ["open"] and e2.st["positions"] == {}


def test_gap_and_stale_plan_are_skipped(tmp_path):
    e, b, clk = make(tmp_path)
    e.st.update(plans=[{"strat": "reversal", "code": "000010", "score": 2, "close": 10000, "atr": 1, "range": 1},
                       {"strat": "reversal", "code": "000020", "score": 1, "close": 10000, "atr": 1, "range": 1}],
                regime="BEAR")
    b.set("000010", 10500, prev=10000, open_=10500)       # +5% 갭 → 포기
    b.set("000020", 9000, prev=9000, open_=9000)          # 전일 종가가 스캔값과 다름 → 포기
    e.open()
    assert not [s for s in b.sent if s[1] == "buy"]


def test_holiday_halts(tmp_path):
    e, b, clk = make(tmp_path)
    b.set("069500", 30000, volume=0)
    e.st["plans"] = [{"strat": "reversal", "code": "000010", "score": 1, "close": 10000, "atr": 1, "range": 1}]
    e.open()
    assert e.st["halt_entries"] and not b.sent


def test_close_bet_scan_buys_and_sells_next_open(tmp_path):
    e, b, clk = make(tmp_path, "2026-11-16 15:05:00")
    e.st["regime"] = "BULL"
    e.st["date"] = "2026-11-16"
    idx = pd.bdate_range(end="2026-11-13", periods=150)
    for code in ("000010", "000020", "069500"):
        pd.DataFrame({"open": 10000.0, "high": 10100.0, "low": 9900.0, "close": 10000.0, "volume": 1e6,
                      "value": 1e10}, index=idx).rename_axis("date").to_csv(tmp_path / "daily" / f"{code}.csv")
    b.q["000010"] = dict(price=11000, open=10100, high=11050, low=10050, prev=10000, volume=8e6, value=8.5e10,
                         upper=13000, lower=7000, warn=False, halted=False)
    b.set("000020", 10100, prev=10000)                    # 급등 아님
    b.daily_history = lambda code, days=200: pd.DataFrame(
        {"open": 30000.0, "high": 30100.0, "low": 29900.0, "close": np.linspace(25000, 30000, 150), "volume": 1e6,
         "value": 1e10}, index=idx)
    uni = pd.DataFrame({"code": ["000010", "000020"]})
    e.closing(uni)
    assert b.sent == [("000010", "buy", 135, 11030)]      # 1000만×15% ÷ (현재가+3틱)
    b.fill("1", 11010)
    e.sync_fills()
    assert e.st["positions"]["000010"]["qty"] == 135
    clk.t = datetime.fromisoformat("2026-11-17 08:51:00")
    e.preopen()
    assert b.sent[-1] == ("000010", "sell", 135, 0)       # 다음날 장전 시장가 매도
    e.preopen()
    assert len(b.sent) == 2                               # 중복 주문 없음


def test_time_exit_at_close(tmp_path):
    e, b, clk = make(tmp_path, "2026-11-16 15:00:00")
    e.st.update(date="2026-11-16", regime="BEAR")
    e.st["positions"]["000010"] = {"strat": "reversal", "qty": 10, "entry": 10000.0, "days": 10, "atr": 1.0,
                                   "date_in": "2026-11-02", "high": 10000, "tp_done": False, "tp_order": None,
                                   "exiting": False, "stop": 8500.0}
    b.set("000010", 10100)
    e.closing(None)
    assert b.sent == [("000010", "sell", 10, 0)]


def test_park_idle_cash_in_index_etf(tmp_path):
    b, clk = FakeBroker(), Clock("2026-11-16 09:01:00")
    b.set("069500", 30000)
    cfg = {"index_code": "069500", "park": {"frac": 0.5, "regimes": ["BULL"]}}
    e = Engine(b, [Reversal(), Surge()], cfg, data_dir=str(tmp_path), now=clk)
    e.st["regime"] = "BULL"
    e.open()
    assert b.sent == [("069500", "buy", 166, 30010)]      # 1000만×50% ÷ (현재가+2틱, ETF 5원 단위)
    b.fill("1", 30005)
    e.tick()
    assert e.st["park"]["qty"] == 166 and e.st["positions"] == {}
    # 다음날 하락 국면 + 투매 반등 후보 → ETF 전량 매도 후 매수 주문
    clk.t = datetime.fromisoformat("2026-11-17 09:01:00")
    e.st.update(regime="BEAR", plans=[{"strat": "reversal", "code": "000010", "score": 1, "close": 10000,
                                       "atr": 300, "range": 400}])
    b.set("000010", 9950, prev=10000, open_=9950)
    e.open()
    assert b.sent[1] == ("069500", "sell", 166, 0)
    assert [x[0] for x in b.sent[2:]] == ["000010"] * 3
    b.fill("2", 29900)
    e.tick()
    assert e.st["park"]["qty"] == 0


def test_atr_ladder_and_daily_entry_cap(tmp_path):
    from dataclasses import replace
    b, clk = FakeBroker(), Clock("2026-11-16 09:01:00")
    b.set("069500", 30000)
    s = replace(Reversal(), ladder_atr=(-0.5, -1.0, -1.5), max_new_per_day=1)
    e = Engine(b, [s], {"index_code": "069500"}, data_dir=str(tmp_path), now=clk)
    e.st.update(regime="BEAR", plans=[
        {"strat": "reversal", "code": "000010", "score": 2, "close": 10000, "atr": 400, "range": 400},
        {"strat": "reversal", "code": "000020", "score": 1, "close": 10000, "atr": 400, "range": 400}])
    b.set("000010", 9950, prev=10000, open_=9950)
    b.set("000020", 9950, prev=10000, open_=9950)
    e.open()
    assert [(c, p) for c, _, _, p in b.sent] == [("000010", 9800), ("000010", 9600), ("000010", 9400)]


def test_phase_schedule_runs_closing_only_in_the_afternoon(tmp_path):
    e, b, clk = make(tmp_path)
    assert e.phase("08:45") == "idle"          # 예전에는 여기서 closing 이 돌았다
    assert e.phase("08:49") == "idle"
    assert e.phase("08:50") == "preopen"
    assert e.phase("09:00") == "idle"
    assert e.phase("09:01") == "intraday"
    assert e.phase("14:57") == "intraday"
    assert e.phase("14:58") == "closing"
    assert e.phase("15:19") == "closing"
    assert e.phase("15:20") == "post"
    assert e.phase("15:40") == "eod"
    e.cfg["close_scan_start"] = "15:00"
    assert e.phase("14:58") == "intraday" and e.phase("15:00") == "closing"


def test_position_is_not_sold_on_entry_day_or_before_max_hold(tmp_path):
    e, b, clk = make(tmp_path, "2026-11-16 15:00:00")
    e.st.update(date="2026-11-16", regime="BEAR")
    pos = {"strat": "reversal", "qty": 10, "entry": 10000.0, "days": 0, "atr": 1.0, "date_in": "2026-11-16",
           "high": 10000, "tp_done": False, "tp_order": None, "exiting": False, "stop": 8500.0}
    e.st["positions"]["000010"] = dict(pos)
    b.set("000010", 10100)
    e.closing(None)
    assert b.sent == [] and "000010" in e.st["positions"]   # 당일 매수분은 그대로 보유


def test_holiday_skips_time_exit(tmp_path):
    e, b, clk = make(tmp_path, "2026-11-16 09:01:00")
    e.st["positions"]["000010"] = {"strat": "reversal", "qty": 10, "entry": 10000.0, "days": 11, "atr": 1.0,
                                   "date_in": "2026-11-02", "high": 10000, "tp_done": False, "tp_order": None,
                                   "exiting": False, "stop": 8500.0}
    b.set("069500", 30000, volume=0)
    b.set("000010", 10100)
    e.open()
    clk.t = datetime.fromisoformat("2026-11-16 15:00:00")
    e.closing(None)
    assert not any(s[1] == "sell" for s in b.sent)


def _flat_cache(tmp_path, codes, close=10000.0):
    idx = pd.bdate_range(end="2026-11-13", periods=150)
    for code in codes:
        pd.DataFrame({"open": close, "high": close + 100, "low": close - 100, "close": close, "volume": 1e6,
                      "value": 1e10}, index=idx).rename_axis("date").to_csv(tmp_path / "daily" / f"{code}.csv")


@pytest.mark.parametrize("days,sold", [(0, False), (1, True)])
def test_connors_ma5_exit_from_next_day(tmp_path, days, sold):
    e, b, clk = make(tmp_path, "2026-11-16 15:00:00")
    e.S["reversal"] = Reversal(exit_rule="ma5", max_hold=20)
    e.st.update(date="2026-11-16", regime="BEAR")
    _flat_cache(tmp_path, ["000010", "069500"])
    e.st["positions"]["000010"] = {"strat": "reversal", "qty": 10, "entry": 9500.0, "days": days, "atr": 1.0,
                                   "date_in": "2026-11-16", "high": 9500, "tp_done": False, "tp_order": None,
                                   "exiting": False, "stop": 8075.0}
    b.set("000010", 10500, prev=10000)                      # 종가가 5일선(≈10,100) 위
    e.closing(None)
    assert (b.sent == [("000010", "sell", 10, 0)]) is sold


def test_connors_ma5_exit_waits_below_ma5_until_max_hold(tmp_path):
    e, b, clk = make(tmp_path, "2026-11-16 15:00:00")
    e.S["reversal"] = Reversal(exit_rule="ma5", max_hold=20)
    e.st.update(date="2026-11-16", regime="BEAR")
    _flat_cache(tmp_path, ["000010", "069500"])
    pos = {"strat": "reversal", "qty": 10, "entry": 9500.0, "days": 12, "atr": 1.0, "date_in": "2026-10-29",
           "high": 9500, "tp_done": False, "tp_order": None, "exiting": False, "stop": 8075.0}
    e.st["positions"]["000010"] = dict(pos)
    b.set("000010", 9600, prev=9700)                        # 5일선 아래, 20일 전 → 보유 유지
    e.closing(None)
    assert b.sent == []


def _crash_frames(mkt_ret5=-0.05):
    from kis_trader.indicators import add_features
    idx = pd.bdate_range(end="2026-11-13", periods=150)
    close = np.full(150, 10000.0)
    close[-3:] = [8500, 8300, 8200]                          # 3일 전 -15% 폭락 후 추가 하락
    vol = np.full(150, 1e6)
    vol[-3] = 5e6                                            # 폭락일 거래량 5배 → 악재성
    df = pd.DataFrame({"open": close, "high": close * 1.01, "low": close * 0.99, "close": close,
                       "volume": vol, "value": 1e10}, index=idx)
    m = pd.DataFrame({"close": 100.0, "ret5": mkt_ret5, "dd20": -0.08, "regime": "BEAR"}, index=idx)
    return add_features(df), m


def test_crash_lookback_excludes_signal_after_idiosyncratic_crash():
    f, m = _crash_frames()
    assert bool(Reversal().prepare(f, m)[0].iloc[-1])                 # 기존: 신호일만 보면 통과
    assert not bool(Reversal(crash_days=5).prepare(f, m)[0].iloc[-1])  # 최근 5일 폭락 → 제외


def test_stale_panic_filter_requires_falling_market():
    f, m = _crash_frames(mkt_ret5=0.05)                               # 지수는 이미 5일 +5% 반등
    assert bool(Reversal().prepare(f, m)[0].iloc[-1])                  # dd20 만으로 통과하던 경우
    assert not bool(Reversal(mkt_ret5_max=0.0).prepare(f, m)[0].iloc[-1])


def test_ladder_uses_actual_previous_close(tmp_path):
    e, b, clk = make(tmp_path)
    e.st.update(plans=[{"strat": "reversal", "code": "000010", "score": 1, "close": 10000, "atr": 300, "range": 400}],
                regime="BEAR")
    b.set("000010", 10040, prev=10040, open_=10040)                  # 스캔 종가와 0.4% 차이
    e.open()
    assert [p for _, _, _, p in b.sent if True] == [10010, 9980, 9940]   # 실제 전일 종가 기준 -3/-6/-10틱
