from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from kis_trader.config import Settings
from kis_trader.safety.calendar import MarketCalendar
from kis_trader.safety.circuit_breaker import CircuitBreaker
from kis_trader.safety.guard import Guard, stop_loss_intents
from kis_trader.safety.models import OrderIntent, Position, RiskContext
from kis_trader.state.db import Database

KST = ZoneInfo("Asia/Seoul")
WED_OPEN = datetime(2026, 11, 11, 10, 0, tzinfo=KST)   # 수요일 장중
WED_AFTER = datetime(2026, 11, 11, 16, 0, tzinfo=KST)
SAT = datetime(2026, 11, 14, 10, 0, tzinfo=KST)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("WATCHLIST", "005930")
    s = Settings(data_dir=tmp_path)
    db = Database(tmp_path / "t.db")
    cal = MarketCalendar(s, db, fetcher=None)
    # 평일은 개장으로 캐시
    db.set_holiday(WED_OPEN.date(), True)
    br = CircuitBreaker(tmp_path, 3)
    return s, Guard(s, cal, br), br, db


def ctx(cash=10_000_000, equity=10_000_000, start=10_000_000, positions=None):
    return RiskContext(cash=cash, equity=equity, day_start_equity=start,
                       positions=positions or {}, prices={"005930": 70_000})


def buy(qty, price=None):
    return OrderIntent("005930", "buy", qty, price, "test")


def test_buy_ok(env):
    _, g, _, _ = env
    assert g.check(buy(5), ctx(), WED_OPEN).approved


def test_amount_and_qty_limits(env):
    _, g, _, _ = env
    assert not g.check(buy(10), ctx(), WED_OPEN).approved      # 700,000 > 500,000
    assert not g.check(buy(101, 1000), ctx(), WED_OPEN).approved


def test_outside_hours_and_weekend(env):
    _, g, _, _ = env
    assert not g.check(buy(1), ctx(), WED_AFTER).approved
    assert not g.check(buy(1), ctx(), SAT).approved


def test_holiday_unknown_fails_closed(env):
    _, g, _, db = env
    unknown = datetime(2026, 11, 12, 10, 0, tzinfo=KST)  # 캐시 없음, fetcher 없음
    assert not g.check(buy(1), ctx(), unknown).approved


def _unsupported_fetcher(_d):
    from kis_trader.kis.errors import KisApiError
    raise KisApiError("모의투자 TR 이 아닙니다", "EGW02006", 500)


def test_paper_falls_back_to_weekday_when_holiday_api_unsupported(tmp_path):
    s = Settings(data_dir=tmp_path, trading_mode="paper")
    cal = MarketCalendar(s, Database(tmp_path / "p.db"), fetcher=_unsupported_fetcher)
    assert cal.can_order(WED_OPEN)[0] is True          # 평일 장중 -> 허용
    assert cal.can_order(SAT)[0] is False              # 주말은 여전히 거부
    assert cal.can_order(WED_AFTER)[0] is False        # 시간 외는 거부


def test_paper_extra_holiday_still_blocks(tmp_path):
    s = Settings(data_dir=tmp_path, trading_mode="paper", extra_holidays="2026-11-11")
    cal = MarketCalendar(s, Database(tmp_path / "p2.db"), fetcher=_unsupported_fetcher)
    assert cal.can_order(WED_OPEN)[0] is False


def test_live_stays_fail_closed_when_holiday_api_unsupported(tmp_path):
    s = Settings(data_dir=tmp_path, trading_mode="live")
    cal = MarketCalendar(s, Database(tmp_path / "l.db"), fetcher=_unsupported_fetcher)
    assert cal.can_order(WED_OPEN) == (False, "휴장 여부 확인 불가")


def test_symbol_not_in_watchlist(env):
    _, g, _, _ = env
    d = g.check(OrderIntent("000660", "buy", 1, 1000, ""), ctx(), WED_OPEN)
    assert not d.approved


def test_daily_loss_blocks(env):
    _, g, _, _ = env
    assert not g.check(buy(1), ctx(equity=9_800_000), WED_OPEN).approved


def test_position_limits(env):
    s, g, _, _ = env
    held = {"005930": Position("005930", 14, 70_000, 70_000)}  # 980,000
    assert not g.check(buy(1), ctx(positions=held), WED_OPEN).approved  # 종목 한도 1,000,000 초과


def test_sell_exceeding_holdings(env):
    _, g, _, _ = env
    held = {"005930": Position("005930", 3, 70_000, 70_000)}
    assert g.check(OrderIntent("005930", "sell", 3, None, ""), ctx(positions=held), WED_OPEN).approved
    assert not g.check(OrderIntent("005930", "sell", 4, None, ""), ctx(positions=held), WED_OPEN).approved


def test_halt_blocks_everything(env):
    _, g, br, _ = env
    br.halt("test")
    assert not g.check(buy(1), ctx(), WED_OPEN).approved


def test_breaker_consecutive_errors(tmp_path):
    br = CircuitBreaker(tmp_path, 3)
    br.record_error("a"); br.record_error("b")
    assert not br.halted
    br.record_error("c")
    assert br.halted
    br.record_success()  # 성공해도 HALT 파일은 수동 삭제 전까지 유지
    assert br.halted


def test_stop_loss():
    pos = {"005930": Position("005930", 10, 100_000, 94_000)}
    assert len(stop_loss_intents(pos, 5.0)) == 1
    assert stop_loss_intents({"005930": Position("005930", 10, 100_000, 96_000)}, 5.0) == []
