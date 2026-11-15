from datetime import datetime, time
from zoneinfo import ZoneInfo

from kis_trader.indicators import IndicatorSet, Ichimoku, compute_all
from kis_trader.safety.models import Position
from kis_trader.strategy.base import Snapshot
from kis_trader.strategy.composite import CompositeStrategy, exit_reason, score

KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 11, 11, 10, 0, tzinfo=KST)


def bullish(**over) -> IndicatorSet:
    """만점 근처의 상승 추세 지표."""
    ind = IndicatorSet(
        last_close=103, sma5=102, sma20=100, sma60=95, sma120=None,
        rsi14=55, rsi14_prev=50, sma20_prev5=98, bars=100,
        ichimoku=Ichimoku(tenkan=102, kijun=99, span_a_now=97, span_b_now=94,
                          span_a_future=100, span_b_future=96, chikou_vs_price=8,
                          cloud_top=97, cloud_bottom=94),
    )
    for k, v in over.items():
        setattr(ind, k, v)
    return ind


def snap(ind, positions=None, now=NOW, price=103, cash=10_000_000, orders_today=None):
    return Snapshot(now=now, prices={"005930": price}, indicators={"005930": ind},
                    cash=cash, equity=cash, positions=positions or {},
                    orders_today=orders_today or set())


def test_full_score_is_100():
    sc = score(bullish())
    assert sc.ready and sc.total == 100
    assert (sc.ma, sc.ichimoku, sc.rsi) == (35, 40, 25)


def test_not_ready_with_few_bars():
    assert not score(bullish(bars=60)).ready


def test_bearish_scores_low():
    ind = bullish(last_close=90, sma5=92, sma20=96, sma60=100, sma20_prev5=98, rsi14=32, rsi14_prev=35)
    ind.ichimoku.tenkan, ind.ichimoku.kijun = 93, 97
    ind.ichimoku.chikou_vs_price = -5
    ind.ichimoku.span_a_future, ind.ichimoku.span_b_future = 94, 98
    assert score(ind).total < 40


def test_buy_full_budget_once_per_day():
    st = CompositeStrategy(buy_budget_krw=1_000_000)
    out = st.on_tick(snap(bullish()))
    assert len(out) == 1 and out[0].side == "buy" and out[0].qty == 1_000_000 // 103
    assert st.on_tick(snap(bullish())) == []  # 같은 날 반복 매수 없음


def test_half_budget_between_70_and_85():
    ind = bullish(rsi14_prev=60)  # RSI 반등 아님 -10 -> 90
    ind.sma20_prev5 = 101         # 20일선 하락 -5 -> 85
    ind.last_close = 106          # SMA20 대비 +6% (눌림 구간 밖) -5 -> 80
    sc = score(ind)
    assert 70 <= sc.total < 85
    out = CompositeStrategy(buy_budget_krw=1_000_000).on_tick(snap(ind, price=106))
    assert out[0].qty == 500_000 // 106


def test_no_buy_before_entry_time_or_when_recorded():
    st = CompositeStrategy(buy_budget_krw=1_000_000, entry_after=time(9, 10))
    assert st.on_tick(snap(bullish(), now=NOW.replace(hour=9, minute=5))) == []
    assert st.on_tick(snap(bullish(), orders_today={("005930", "buy")})) == []


def test_no_buy_below_cloud_even_if_score_high():
    ind = bullish()
    ind.ichimoku.cloud_top = 104  # 종가 103 이 구름 안
    assert CompositeStrategy(buy_budget_krw=1_000_000, buy_score=0).on_tick(snap(ind)) == []


def test_no_buy_when_overheated():
    assert CompositeStrategy(buy_budget_krw=1_000_000, buy_score=0).on_tick(snap(bullish(rsi14=78))) == []


def test_exit_on_cloud_break():
    ind = bullish(last_close=93)
    pos = {"005930": Position("005930", 10, 100.0, 93)}
    out = CompositeStrategy(buy_budget_krw=1_000_000).on_tick(snap(ind, positions=pos, price=93))
    assert len(out) == 1 and out[0].side == "sell" and out[0].qty == 10


def test_take_profit_half():
    ind = bullish(rsi14=78)
    assert exit_reason(ind, score(ind), pnl_pct=12.0, sell_score=40) == (
        "과열 익절 RSI 78.0, 수익 12.0%", 0.5)
    assert exit_reason(ind, score(ind), pnl_pct=5.0, sell_score=40) is None


def test_hold_in_healthy_trend():
    pos = {"005930": Position("005930", 10, 100.0, 103)}
    assert CompositeStrategy(buy_budget_krw=1_000_000).on_tick(snap(bullish(), positions=pos)) == []


def test_compute_all_fills_new_fields():
    n = 100
    closes = [100 + (i % 7) - 3 + i * 0.3 for i in range(n)]
    ind = compute_all([c + 2 for c in closes], [c - 2 for c in closes], closes)
    assert ind.rsi14_prev is not None and ind.sma20_prev5 is not None
    assert score(ind).ready
