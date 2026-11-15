import numpy as np
import pandas as pd

from kis_trader.backtest import Costs, backtest
from kis_trader.strategies.base import Strategy


class Always(Strategy):
    """매일 신호. 체결·청산 규칙 검증용."""
    def prepare(self, f, m=None):
        return f["close"] > 0, f["close"] * 0 + 1, f["close"] < 0


def _data(o, h, l, c):
    idx = pd.bdate_range("2024-01-01", periods=len(c))
    d = pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": 1e6, "value": 1e10}, index=idx, dtype=float)
    return {"000010": d}, pd.Series(1000.0, index=idx)


FREE = Costs(fee=0, tax=0, slip_stop=0, slip_close=0)


def test_limit_needs_trade_through():
    # 전일 종가 10000 → 지정가 9970. 저가가 9970에 닿기만 하면 미체결, 9960이면 체결
    for low, filled in ((9970, 0), (9960, 1)):
        data, idx = _data([10000, 10000, 10000], [10000, 10050, 10000], [10000, low, 10000], [10000, 10000, 10000])
        s = Always(name="t", ladder_ticks=(-3,), ladder_weights=(1.0,), max_positions=1, pos_weight=0.5,
                   stop_pct=0.5, max_hold=1)
        r = backtest(data, [s], idx, cash=1_000_000, costs=FREE)
        assert (len(r.trades) > 0) == bool(filled)
        if filled:
            assert r.trades.iloc[0].entry == 9970


def test_gap_down_fills_at_open():
    data, idx = _data([10000, 9800, 9800], [10000, 9900, 9800], [10000, 9700, 9800], [10000, 9850, 9800])
    s = Always(name="t", ladder_ticks=(-3,), ladder_weights=(1.0,), max_positions=1, pos_weight=0.5,
               gap_min=-0.1, stop_pct=0.5, max_hold=1)
    r = backtest(data, [s], idx, cash=1_000_000, costs=FREE)
    assert r.trades.iloc[0].entry == 9800


def test_stop_then_costs():
    data, idx = _data([10000, 10000, 9900, 9900], [10000, 10000, 9900, 9900], [10000, 9950, 9000, 9900],
                      [10000, 9990, 9100, 9900])
    s = Always(name="t", ladder_ticks=(-3,), ladder_weights=(1.0,), max_positions=1, pos_weight=0.5,
               stop_pct=0.05, max_hold=9)
    r = backtest(data, [s], idx, cash=1_000_000, costs=Costs(fee=0.001, tax=0.002, slip_stop=0, slip_close=0))
    t = r.trades.iloc[0]
    assert t.why == "손절" and abs(t.exit - 9970 * 0.95) < 1e-6
    qty = int(500_000 // (9970 * 1.001))
    expect = qty * t.exit * (1 - 0.003) - qty * 9970 * 1.001
    assert abs(t.pnl - expect) < 1e-6


def test_take_profit_not_on_entry_day_and_time_exit():
    data, idx = _data([10000, 10000, 10000, 10000], [10000, 12000, 10100, 10000], [10000, 9900, 9950, 10000],
                      [10000, 10000, 10050, 10000])
    s = Always(name="t", ladder_ticks=(-3,), ladder_weights=(1.0,), max_positions=1, pos_weight=0.5,
               stop_pct=0.5, tp_pct=0.05, max_hold=1)
    r = backtest(data, [s], idx, cash=1_000_000, costs=FREE)
    t = r.trades.iloc[0]
    assert t.why == "기간만료" and t.exit == 10050        # 진입일 고가 12000은 익절로 치지 않음


def test_close_entry_exits_next_open():
    data, idx = _data([10000, 10000, 10300, 10300], [10000, 10100, 10400, 10300], [10000, 9900, 10200, 10300],
                      [10000, 10100, 10250, 10300])
    s = Always(name="t", entry="close", exit_next_open=True, stop_pct=None, max_positions=1, pos_weight=0.5, max_hold=1)
    r = backtest(data, [s], idx, cash=1_000_000, costs=FREE)
    t = r.trades.iloc[0]
    assert (t.entry, t.exit, t.days) == (10100, 10300, 1)


def test_equity_never_uses_more_than_cash():
    rng = np.random.default_rng(1)
    c = 10000 * np.exp(np.cumsum(rng.normal(0, 0.03, 200)))
    data, idx = _data(c, c * 1.03, c * 0.97, c)
    s = Always(name="t", ladder_ticks=(-1,), ladder_weights=(1.0,), max_positions=1, pos_weight=5.0, max_hold=2)
    r = backtest(data, [s], idx, cash=1_000_000)
    assert r.equity.min() > 0 and np.isfinite(r.equity).all()
