from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from kis_trader.demo_hourly import (
    HourlyBot, HourlyCompositeStrategy, bucket_closed, hour_bucket, to_hourly,
)
from kis_trader.kis.market import MinuteBar
from kis_trader.safety.models import RiskContext

KST = ZoneInfo("Asia/Seoul")
DAY = date(2026, 11, 13)


def test_hour_bucket():
    assert hour_bucket("090000") == 9
    assert hour_bucket("090100") == 9
    assert hour_bucket("100000") == 9   # 09:59~10:00 봉
    assert hour_bucket("100100") == 10
    assert hour_bucket("151900") == 15
    assert hour_bucket("153000") == 15  # 종가 단일가


def test_bucket_closed():
    assert not bucket_closed(9, datetime(2026, 11, 13, 9, 59, tzinfo=KST))
    assert bucket_closed(9, datetime(2026, 11, 13, 10, 0, tzinfo=KST))
    assert not bucket_closed(15, datetime(2026, 11, 13, 15, 20, tzinfo=KST))
    assert bucket_closed(15, datetime(2026, 11, 13, 15, 30, tzinfo=KST))


def _minutes(day, start="090100", end="153000", base=100.0, step=0.01):
    out = []
    t = datetime(2000, 1, 1, int(start[:2]), int(start[2:4]))
    stop = datetime(2000, 1, 1, int(end[:2]), int(end[2:4]))
    i = 0
    while t <= stop:
        if not (t.hour == 15 and 20 < t.minute < 30):  # 동시호가 구간 봉 없음
            p = base + i * step
            out.append(MinuteBar(day, t.strftime("%H%M%S"), p, p + 0.5, p - 0.5, p, 10))
            i += 1
        t += timedelta(minutes=1)
    return out


def test_to_hourly_ohlc():
    hb = to_hourly(_minutes(DAY))
    assert [h[1] for h in hb] == [9, 10, 11, 12, 13, 14, 15]
    d, h, o, hi, lo, c = hb[0]
    assert o == 100.0 and c == 100.0 + 59 * 0.01  # 09:01 ~ 10:00 의 60개
    assert hi == max(100.0 + i * 0.01 for i in range(60)) + 0.5


def test_hourly_strategy_period_changes_each_hour():
    st = HourlyCompositeStrategy(buy_budget_krw=1_000_000)

    class S:
        now = datetime(2026, 11, 13, 10, 5, tzinfo=KST)
    assert st.period(S) == (DAY, 10)


class FakeMarket:
    def __init__(self):
        self.calls = []

    def get_minute_bars(self, sym, day, until="153000", stop="090000", today=False):
        self.calls.append((day, until, stop, today))
        return [b for b in _minutes(day) if stop <= b.hhmmss <= until]


def _bot():
    bot = HourlyBot.__new__(HourlyBot)
    bot.market = FakeMarket()
    bot._hist = {"005930": sum((_minutes(DAY - timedelta(days=k), base=100 + k) for k in range(13, 0, -1)), [])}
    bot._hist_day = DAY
    bot._today_min = {}
    bot._hourly_cache = {}
    return bot


def test_indicators_use_only_closed_hour_bars():
    bot = _bot()
    at_0905 = bot.indicators_for("005930", DAY, datetime(2026, 11, 13, 9, 5, tzinfo=KST))
    assert at_0905.bars == 13 * 7 and bot.market.calls == []  # 09시대: 과거 봉만
    at_1001 = bot.indicators_for("005930", DAY, datetime(2026, 11, 13, 10, 1, tzinfo=KST))
    assert at_1001.bars == 13 * 7 + 1  # 09시봉 추가, 진행 중인 10시봉 제외
    assert at_1001.ichimoku.cloud_top is not None and at_1001.rsi14 is not None
    # 같은 시간대에는 다시 조회하지 않음
    n = len(bot.market.calls)
    bot.indicators_for("005930", DAY, datetime(2026, 11, 13, 10, 30, tzinfo=KST))
    assert len(bot.market.calls) == n
    at_1101 = bot.indicators_for("005930", DAY, datetime(2026, 11, 13, 11, 1, tzinfo=KST))
    assert at_1101.bars == 13 * 7 + 2
    # 증분 조회: 마지막으로 받은 봉(10:01, 받을 때 미완성일 수 있음)부터 다시 받아 덮어씀
    assert bot.market.calls[-1][2] == "100100"


def test_daily_buy_cap_in_spendable_cash():
    bot = HourlyBot.__new__(HourlyBot)

    class S:
        daily_max_buy_krw = 3_000_000
    bot.s = S
    ctx = RiskContext(cash=30_000_000, equity=30_000_000, day_start_equity=30_000_000,
                      bought_today_krw=2_400_000)
    assert bot.spendable_cash(ctx) == 600_000
    ctx.bought_today_krw = 3_100_000
    assert bot.spendable_cash(ctx) == 0


def test_liquidation_intents_sell_all_watchlist_once():
    from kis_trader.safety.models import Position
    bot = HourlyBot.__new__(HourlyBot)

    class S:
        symbols = ["005930", "000660"]
    bot.s = S
    pos = {"005930": Position("005930", 7, 100.0, 101), "000660": Position("000660", 3, 100.0, 99),
           "035720": Position("035720", 5, 100.0, 99), "005380": Position("005380", 0, 0.0, 0)}
    out = bot.liquidation_intents(pos, already_sold={("000660", "sell")})
    assert [(i.symbol, i.side, i.qty, i.limit_price) for i in out] == [("005930", "sell", 7, None)]


def test_no_new_buy_from_1500():
    from kis_trader.demo_hourly import NO_NEW_BUY_FROM
    from test_composite import bullish, snap
    st = HourlyCompositeStrategy(buy_budget_krw=1_000_000, entry_before=NO_NEW_BUY_FROM)
    assert st.on_tick(snap(bullish(), now=datetime(2026, 11, 13, 15, 1, tzinfo=KST))) == []
    assert len(st.on_tick(snap(bullish(), now=datetime(2026, 11, 13, 14, 1, tzinfo=KST)))) == 1
