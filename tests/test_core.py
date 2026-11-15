import numpy as np
import pandas as pd

from kis_trader import ticks
from kis_trader.indicators import add_features, rsi
from kis_trader.regime import regime_series, market_features
from kis_trader.strategies import Reversal, Surge, build
from kis_trader.universe import parse_mst, filter_universe, _W, _N, _POS


def test_tick_bands():
    assert [ticks.tick_size(p) for p in (1999, 2000, 4999, 5000, 19990, 20000, 49950, 50000, 199900, 200000, 499500, 500000)] \
        == [1, 5, 5, 10, 10, 50, 50, 100, 100, 500, 500, 1000]
    assert ticks.tick_size(30000, etf=True) == 5


def test_shift_crosses_band():
    assert ticks.shift(10000, -3) == 9970
    assert ticks.shift(5000, -1) == 4995          # 경계 아래는 5원 단위
    assert ticks.shift(5000, -3) == 4985
    assert ticks.shift(49950, 2) == 50100         # 50원 → 100원 단위
    assert ticks.shift(10003, -1) == 9990         # 호가에 맞춘 뒤 이동
    assert ticks.shift(10003, 0) == 10000
    assert ticks.shift(200000, -10) == 199000


def _bars(close, vol=None, spread=0.01):
    close = np.asarray(close, float)
    idx = pd.bdate_range("2024-01-01", periods=len(close))
    vol = np.full(len(close), 1e6) if vol is None else np.asarray(vol, float)
    return pd.DataFrame({"open": close, "high": close * (1 + spread), "low": close * (1 - spread),
                         "close": close, "volume": vol, "value": close * vol}, index=idx)


def test_rsi_bounds_and_direction():
    up = rsi(pd.Series(np.arange(1, 60, dtype=float)), 14)
    dn = rsi(pd.Series(np.arange(60, 1, -1, dtype=float)), 14)
    assert up.iloc[-1] == 100 and dn.iloc[-1] < 1


def test_features_do_not_look_ahead():
    rng = np.random.default_rng(0)
    c = 10000 * np.exp(np.cumsum(rng.normal(0, 0.02, 300)))
    full = add_features(_bars(c))
    part = add_features(_bars(c[:200]))
    cols = ["ma20", "atr", "rsi2", "vol_ratio", "hh20", "clv"]
    pd.testing.assert_frame_equal(full[cols].iloc[:200], part[cols])


def test_regime():
    up = pd.Series(np.linspace(100, 200, 200))
    dn = pd.Series(np.linspace(200, 100, 200))
    assert regime_series(up).iloc[-1] == "BULL" and regime_series(dn).iloc[-1] == "BEAR"


def test_reversal_needs_market_panic():
    c = np.r_[np.full(150, 10000.0), 10000 * np.cumprod(np.full(6, 0.95))]   # 6일 연속 -5%
    f = add_features(_bars(c))
    calm = market_features(pd.Series(np.full(len(c), 1000.0), index=f.index))
    panic = market_features(pd.Series(np.r_[np.full(150, 1000.0), 1000 * np.cumprod(np.full(6, 0.98))], index=f.index))
    s = Reversal()
    assert not s.prepare(f, calm)[0].iloc[-1]       # 혼자 빠진 종목은 사지 않는다
    assert s.prepare(f, panic)[0].iloc[-1]          # 시장이 같이 빠질 때만 산다


def test_surge_signal_and_quick_filter():
    c = np.r_[np.full(100, 10000.0), 11000.0]
    v = np.r_[np.full(100, 1e6), 8e6]
    b = _bars(c, v)
    b.iloc[-1, b.columns.get_loc("open")] = 10100
    b.iloc[-1, b.columns.get_loc("low")] = 10050
    b.iloc[-1, b.columns.get_loc("high")] = 11050
    s = Surge()
    assert s.prepare(add_features(b))[0].iloc[-1]
    assert s.quick({"price": 11000, "prev": 10000, "value": 8.8e10})
    assert not s.quick({"price": 10200, "prev": 10000, "value": 8.8e10})


def test_build_from_config():
    st = build({"reversal": {"max_positions": 2, "ladder_ticks": [-1, -2, -3]}, "surge": {"enabled": False}})
    assert len(st) == 1 and st[0].ladder_ticks == (-1, -2, -3)
    assert [s.name for s in build()] == ["reversal", "surge"]


def _mst_row(code, name, **kw):
    tail = [" "] * 227
    def put(k, v):
        a, w = _POS[k]
        tail[a:a + w] = list(str(v).ljust(w)[:w])
    put("그룹코드", "ST"); put("SPAC", "N"); put("거래정지", "N"); put("정리매매", "N"); put("관리종목", "N")
    put("시장경고", "00"); put("우선주", "0"); put("기준가", "000050000"); put("시가총액", "000012345")
    for k, v in kw.items():
        put(k, v)
    return code.ljust(9) + "KR7000000000" + name + "".join(tail)


def test_master_parse_and_filter():
    assert sum(_W) == 227 and len(_W) == len(_N)
    text = "\n".join([
        _mst_row("005930", "삼성전자"), _mst_row("005935", "삼성전자우"),
        _mst_row("069500", "KODEX 200", 그룹코드="EF"), _mst_row("000020", "관리종목", 관리종목="Y"),
        _mst_row("000030", "동전주", 기준가="000000500"),
    ])
    df = parse_mst(text)
    assert len(df) == 5 and df.loc[0, "name"] == "삼성전자" and df.loc[0, "base_price"] == 50000
    assert list(filter_universe(df)["code"]) == ["005930"]
