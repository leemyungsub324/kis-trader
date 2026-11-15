"""2026-11-13 사고 재발 방지: 체결조회 실패가 HALT/청산 차단으로 이어지지 않아야 한다."""
import logging
from datetime import date, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import kis_trader.demo_hourly as demo_mod
import kis_trader.main as main_mod
from kis_trader.config import Settings
from kis_trader.demo_hourly import HourlyBot
from kis_trader.kis.account import Balance
from kis_trader.kis.errors import KisApiError, KisAuthError
from kis_trader.main import Bot
from kis_trader.safety.models import ApprovedOrder, Decision, Position
from kis_trader.strategy.composite import CompositeStrategy, buy_blockers

KST = ZoneInfo("Asia/Seoul")


# ── DEMO_DATE 기간 ──
def _rng(raw):
    return Settings.demo_range.fget(SimpleNamespace(demo_date=raw))


def test_demo_range_parsing():
    assert _rng("") is None
    assert _rng("2026-11-13") == (date(2026, 11, 13), date(2026, 11, 13))
    assert _rng("2026-11-16~2026-11-20") == (date(2026, 11, 16), date(2026, 11, 20))
    assert _rng(" 2026-11-16 ~ 2026-11-20 ") == (date(2026, 11, 16), date(2026, 11, 20))
    for bad in ("2026-11-20~2026-11-16", "2026-11-16~", "2026-11-16~2026-11-17~2026-11-18", "내일"):
        try:
            _rng(bad)
        except ValueError:
            continue
        raise AssertionError(f"오류여야 함: {bad!r}")


def test_is_demo_day_inclusive():
    s = SimpleNamespace(demo_date="2026-11-16~2026-11-20")
    s.demo_range = Settings.demo_range.fget(s)
    chk = lambda d: Settings.is_demo_day(s, d)
    assert not chk(date(2026, 11, 13)) and chk(date(2026, 11, 16)) and chk(date(2026, 11, 20))
    assert not chk(date(2026, 11, 21))


# ── 체결조회 실패 ──
class _Notifier:
    def __init__(self):
        self.sent = []

    def send(self, msg):
        self.sent.append(msg)


class _Orders:
    def __init__(self):
        self.synced = 0
        self.submitted = []

    def sync_fills(self, rows):
        self.synced += 1

    def submit(self, order):
        self.submitted.append(order.intent)


def _bot(cls=Bot, fail=None, now_demo=True):
    bot = cls.__new__(cls)
    bot.s = SimpleNamespace(
        symbols=["005930"], stop_loss_pct=5.0, daily_max_loss_krw=10**9, daily_max_buy_krw=0,
        is_demo_day=lambda d: now_demo, demo_date="2026-11-16~2026-11-20",
    )
    bot._sync_fail_streak = 0
    bot.notifier = _Notifier()
    bot.orders = _Orders()
    state = {"fail": fail}

    def get_today_orders():
        if state["fail"]:
            raise state["fail"]
        return []
    bot.account = SimpleNamespace(
        get_today_orders=get_today_orders,
        get_balance=lambda: Balance(cash=3_000_000, equity=3_800_000,
                                    positions={"005930": Position("005930", 4, 213250.0, 206000)}),
    )
    bot.market = SimpleNamespace(get_price=lambda s: 206000)
    bot.calendar = SimpleNamespace(can_order=lambda now: (True, ""))
    bot.breaker = SimpleNamespace(record_fatal=lambda m: (_ for _ in ()).throw(AssertionError(m)))
    bot.db = SimpleNamespace(day_start_equity=lambda d, e: e, buy_amount_on=lambda d: 0,
                             order_sides_since=lambda t: set(), order_sides_on=lambda d: set())
    bot.guard = SimpleNamespace(
        loss_limit_hit=lambda ctx: False, daily_loss=lambda ctx: 0,
        check=lambda it, ctx, now: Decision(True, [], ApprovedOrder(it, 206000)),
    )
    bot.strategy = SimpleNamespace(on_tick=lambda snap: [])
    bot.load_history = lambda d: True
    bot._off_day_logged = None
    bot._hourly_cache = {}
    bot._ind_cache = {}
    bot.indicators_for = lambda sym, today, now: None  # 전략은 on_tick 스텁이라 지표 불필요
    bot.state = state
    return bot


def test_sync_failure_does_not_raise_and_notifies_once():
    bot = _bot(fail=KisApiError("서비스 없음", "MCA00124"))
    for _ in range(12):
        bot.sync_fills_safe()
    assert bot._sync_fail_streak == 12
    assert len(bot.notifier.sent) == 1  # 10회째에 한 번만
    bot.state["fail"] = None
    bot.sync_fills_safe()
    assert bot._sync_fail_streak == 0 and bot.orders.synced == 1


def test_sync_auth_error_still_propagates():
    bot = _bot(fail=KisAuthError("토큰"))
    try:
        bot.sync_fills_safe()
    except KisAuthError:
        return
    raise AssertionError("인증 오류는 전파되어야 함")


class _FakeDT(datetime):
    fixed = None

    @classmethod
    def now(cls, tz=None):
        return cls.fixed


def _tick_at(bot, when):
    _FakeDT.fixed = when
    orig = (main_mod.datetime, demo_mod.datetime)
    main_mod.datetime = demo_mod.datetime = _FakeDT
    try:
        bot.tick()
    finally:
        main_mod.datetime, demo_mod.datetime = orig


def test_liquidation_goes_out_even_when_fills_query_fails():
    """11/13 재현: 체결조회가 계속 실패해도 15:15 청산은 나가야 한다."""
    bot = _bot(HourlyBot, fail=KisApiError("서비스 없음", "MCA00124"))
    _tick_at(bot, datetime(2026, 11, 16, 15, 16, tzinfo=KST))
    assert [(i.symbol, i.side, i.qty, i.limit_price) for i in bot.orders.submitted] == [("005930", "sell", 4, None)]
    assert bot._sync_fail_streak == 1


def test_no_liquidation_before_1515_or_outside_demo():
    bot = _bot(HourlyBot)
    _tick_at(bot, datetime(2026, 11, 16, 15, 10, tzinfo=KST))
    assert bot.orders.submitted == []
    bot = _bot(HourlyBot, now_demo=False)
    _tick_at(bot, datetime(2026, 11, 16, 15, 16, tzinfo=KST))
    assert bot.orders.submitted == []


def test_stop_loss_goes_out_before_fills_query():
    bot = _bot(fail=KisApiError("서비스 없음", "MCA00124"))
    bot.account.get_balance = lambda: Balance(
        cash=3_000_000, equity=3_700_000,
        positions={"005930": Position("005930", 4, 213250.0, 200000)})  # -6.2%
    _tick_at(bot, datetime(2026, 11, 16, 11, 0, tzinfo=KST))
    assert [(i.side, i.qty) for i in bot.orders.submitted] == [("sell", 4)]


def test_one_failed_exit_does_not_block_the_rest():
    bot = _bot(HourlyBot)
    bot.s.symbols = ["005930", "000660"]
    pos = {"005930": Position("005930", 4, 100.0, 90), "000660": Position("000660", 2, 100.0, 90)}
    bot.account.get_balance = lambda: Balance(cash=1, equity=1, positions=pos)

    def check(it, ctx, now):
        if it.symbol == "005930":
            raise KisApiError("주문 거부", "X")
        return Decision(True, [], ApprovedOrder(it, 90))
    bot.guard.check = check
    try:
        _tick_at(bot, datetime(2026, 11, 16, 15, 16, tzinfo=KST))
    except KisApiError:
        pass
    else:
        raise AssertionError("첫 오류는 마지막에 다시 던져 연속 오류로 집계되어야 함")
    assert [i.symbol for i in bot.orders.submitted] == ["000660"]


# ── 매수 보류 사유 로그 ──
class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


def test_buy_blocked_is_logged_once_with_reasons():
    from test_composite import bullish, snap
    ind = bullish()
    ind.ichimoku.cloud_top = 104  # 종가 103 이 구름 안
    assert buy_blockers(ind) == ["종가<=구름상단"]
    cap = _Capture()
    lg = logging.getLogger("kis_trader.strategy.composite")
    lg.addHandler(cap)
    lg.setLevel(logging.INFO)
    try:
        st = CompositeStrategy(buy_budget_krw=1_000_000)
        assert st.on_tick(snap(ind)) == [] and st.on_tick(snap(ind)) == []
    finally:
        lg.removeHandler(cap)
    blocked = [r for r in cap.records if getattr(r, "event", "") == "buy_blocked"]
    assert len(blocked) == 1 and blocked[0].reasons == ["종가<=구름상단"] and blocked[0].score >= 65
    scores = [r for r in cap.records if getattr(r, "event", "") == "composite_score"]
    assert scores[0].cloud_top == 104 and scores[0].sma60 == 95


def test_buy_blockers_variants():
    from test_composite import bullish
    assert buy_blockers(bullish()) == []
    below = bullish(last_close=90)
    assert "종가<구름하단" in buy_blockers(below) and "종가<=SMA60" in buy_blockers(below)
    assert buy_blockers(bullish(rsi14=76)) == ["RSI>=75"]
