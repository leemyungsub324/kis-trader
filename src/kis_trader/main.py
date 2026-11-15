"""진입점: 복구 -> 주기 루프(관찰/전략 -> Guard -> 주문)."""
from __future__ import annotations

import logging
import signal
import sys
import time
from datetime import date, datetime
from zoneinfo import ZoneInfo

import httpx
from pydantic import ValidationError

from .config import ConfigError, Settings
from .indicators import IndicatorSet, compute_all
from .kis.account import Account
from .kis.auth import TokenManager
from .kis.client import KisClient
from .kis.errors import KisApiError, KisAuthError, KisNetworkError
from .kis.market import Market
from .kis.orders import OrderService
from .logging_setup import register_secrets, setup_logging
from .notify.telegram import Notifier
from .recovery import RecoveryError, restore_state
from .safety.calendar import MarketCalendar
from .safety.circuit_breaker import CircuitBreaker
from .safety.guard import Guard, stop_loss_intents
from .safety.models import OrderIntent, RiskContext
from .state.db import Database
from .strategy.base import Snapshot, Strategy
from .strategy.composite import CompositeStrategy
from .strategy.observe import ObserveStrategy

log = logging.getLogger("kis_trader")
KST = ZoneInfo("Asia/Seoul")
MARKET_CLOSE_HHMM = (15, 30)


class Bot:
    def __init__(self, s: Settings, strategy: Strategy):
        self.s = s
        self.strategy = strategy
        self.stop = False
        s.data_dir.mkdir(parents=True, exist_ok=True)

        self.http = httpx.Client()
        self.db = Database(s.data_dir / s.db_filename)
        self.notifier = Notifier(s)
        self.breaker = CircuitBreaker(s.data_dir, s.max_consecutive_errors, self.notifier.send)
        tokens = TokenManager(s, self.http, s.data_dir / s.token_filename)
        self.client = KisClient(s, self.http, tokens)
        self.market = Market(self.client, s)
        self.account = Account(self.client, s)
        self.calendar = MarketCalendar(s, self.db, self.market.fetch_holidays)
        self.guard = Guard(s, self.calendar, self.breaker)
        self.orders = OrderService(self.client, s, self.db, self.breaker, self.notifier.send)
        self._ind_cache: dict[str, tuple[date, IndicatorSet]] = {}
        self._halt_logged = 0.0
        self._sync_fail_streak = 0

    # ── 루프 보조 ──
    def heartbeat(self) -> None:
        try:
            (self.s.data_dir / "heartbeat").write_text(str(time.time()))
        except OSError:
            log.warning("heartbeat 기록 실패")

    def sleep(self, seconds: float) -> None:
        end = time.monotonic() + seconds
        while not self.stop and time.monotonic() < end:
            self.heartbeat()
            time.sleep(min(10, max(0.0, end - time.monotonic())))

    # ── 지표 ──
    def indicators_for(self, symbol: str, today: date, now: datetime) -> IndicatorSet:
        cached = self._ind_cache.get(symbol)
        if cached and cached[0] == today:
            return cached[1]
        candles = self.market.get_daily_candles(symbol)
        closed_after = (now.hour, now.minute) >= MARKET_CLOSE_HHMM
        if candles and candles[-1].day == today and not closed_after:
            candles = candles[:-1]  # 장중 미완성 봉 제외 (마감된 봉만 사용)
        ind = compute_all([c.high for c in candles], [c.low for c in candles], [c.close for c in candles])
        self._ind_cache[symbol] = (today, ind)
        return ind

    # ── 주문 처리 ──
    def process(self, intent: OrderIntent, ctx: RiskContext, now: datetime) -> bool:
        """Guard 를 통과해 주문 처리(전송 또는 DRY-RUN 기록)까지 갔으면 True."""
        d = self.guard.check(intent, ctx, now)
        if not d.approved:
            log.warning("안전장치가 주문을 거부", extra={
                "event": "order_blocked", "symbol": intent.symbol, "side": intent.side,
                "qty": intent.qty, "reasons": d.reasons})
            self.db.event("order_blocked", {"intent": intent.__dict__, "reasons": d.reasons})
            return False
        assert d.order is not None
        self.orders.submit(d.order)
        if intent.side == "buy":  # 같은 주기의 다음 매수가 한도를 다시 쓰지 않도록 즉시 반영
            amount = intent.qty * d.order.ref_price
            ctx.bought_today_krw += amount
            ctx.cash -= amount
        return True

    # ── 1회 주기 ──
    def tick(self) -> None:
        now = datetime.now(KST)
        today = now.date()
        active, why = self.calendar.can_order(now)
        if not active:
            self.maybe_daily_summary(now)
            return

        # 1) 잔고만으로 청산(손절·장마감 청산)을 먼저 처리한다. 체결조회/시세 조회가 실패해도 청산은 나가야 한다.
        bal = self.account.get_balance()
        ctx = RiskContext(
            cash=bal.cash, equity=bal.equity,
            day_start_equity=self.db.day_start_equity(today, bal.equity),
            positions=bal.positions, prices={s: p.last_price for s, p in bal.positions.items()},
            bought_today_krw=self.db.buy_amount_on(today),
        )
        if self.guard.loss_limit_hit(ctx):
            self.breaker.record_fatal(f"일일 최대 손실 도달: {self.guard.daily_loss(ctx):,}원 (한도 {self.s.daily_max_loss_krw:,}원)")
            return
        # 손절은 전략과 무관하게 먼저 평가
        self.process_all(stop_loss_intents(bal.positions, self.s.stop_loss_pct), ctx, now)
        self.exit_phase(ctx, now)

        # 2) 체결 반영: 읽기 전용 조회라 실패해도 매매 중단 사유가 아니다 (다음 주기 재시도)
        self.sync_fills_safe()

        # 3) 시세 -> 전략
        prices = {sym: self.market.get_price(sym) for sym in self.s.symbols}
        for sym, pos in bal.positions.items():
            if pos.last_price <= 0 and sym in prices:
                pos.last_price = prices[sym]
        ctx.prices.update({s: p for s, p in prices.items() if s not in ctx.prices or ctx.prices[s] <= 0})
        self.run_strategy(ctx, bal, prices, now)

    SYNC_FAIL_NOTIFY_AT = 10  # 연속 실패가 이 횟수(약 10분)가 되면 한 번 알림

    def sync_fills_safe(self) -> None:
        """체결 확인(inquire-daily-ccld) 실패는 HALT 사유가 아니다.

        2026-11-13: 이 조회가 일시적으로 MCA00124 를 내서 연속 오류 3회 -> HALT -> 장마감 청산이 막혔다.
        주문 자체는 DB 에 전송 전에 기록되고 하루 1회 제한은 DB 기준이라, 체결 반영이 늦어져도 중복 주문은 없다.
        """
        try:
            self.orders.sync_fills(self.account.get_today_orders())
        except KisAuthError:
            raise
        except KisApiError as e:
            self._sync_fail_streak += 1
            log.warning("체결 조회 실패(%d회 연속), 다음 주기에 재시도: %s", self._sync_fail_streak, e,
                        extra={"event": "sync_fills_failed", "streak": self._sync_fail_streak})
            if self._sync_fail_streak == self.SYNC_FAIL_NOTIFY_AT:
                self.notifier.send(f"⚠️ 체결 조회가 {self._sync_fail_streak}회 연속 실패 중입니다: {e}\n"
                                   "매매는 계속하지만 체결 알림이 지연될 수 있습니다.")
        else:
            self._sync_fail_streak = 0

    def exit_phase(self, ctx: RiskContext, now: datetime) -> None:
        """손절 다음, 체결조회/시세 조회 전에 실행할 청산 훅 (기본: 없음). 데모가 장마감 청산을 여기에 건다."""

    def process_all(self, intents: list[OrderIntent], ctx: RiskContext, now: datetime) -> None:
        """청산 주문 묶음 처리. 한 종목이 거부돼도 나머지는 계속 내고, 첫 오류는 마지막에 다시 던진다."""
        first: KisApiError | None = None
        for intent in intents:
            try:
                self.process(intent, ctx, now)
            except KisApiError as e:
                log.error("청산 주문 실패 %s: %s", intent.symbol, e, extra={"event": "exit_failed", "symbol": intent.symbol})
                first = first or e
        if first:
            raise first

    def spendable_cash(self, ctx: RiskContext) -> int:
        """전략이 매수 수량 계산에 쓸 현금: 예수금과 일일 매수 한도 잔여분 중 작은 값."""
        if self.s.daily_max_buy_krw:
            return max(0, min(ctx.cash, self.s.daily_max_buy_krw - ctx.bought_today_krw))
        return ctx.cash

    def run_strategy(self, ctx: RiskContext, bal, prices: dict[str, int], now: datetime) -> None:
        today = now.date()
        indicators = {sym: self.indicators_for(sym, today, now) for sym in self.s.symbols}
        snap = Snapshot(now=now, prices=prices, indicators=indicators, cash=self.spendable_cash(ctx),
                        equity=bal.equity, positions=bal.positions,
                        orders_today=self.db.order_sides_on(today))  # 손절 주문 기록 이후에 조회
        for intent in self.strategy.on_tick(snap):
            self.process(intent, ctx, now)

    def maybe_daily_summary(self, now: datetime) -> None:
        today = now.date()
        key = f"summary_sent:{today.isoformat()}"
        if now.time() < self.s.summary_time or self.db.kv_get(key):
            return
        if self.calendar.is_open_day(today) is not True:
            return
        bal = self.account.get_balance()
        start = self.db.day_start_equity(today, bal.equity)
        counts = self.db.count_orders_on(today)
        self.notifier.send(
            f"📊 일일 요약 {today}\n평가금액 {bal.equity:,}원 (당일 {bal.equity - start:+,}원)\n"
            f"예수금 {bal.cash:,}원 / 보유 {len(bal.positions)}종목\n주문 기록: {counts or '없음'}\n"
            f"모드: {self.s.mode_label}, DRY_RUN={self.s.dry_run}"
        )
        self.db.kv_set(key, "1")

    def safe_tick(self) -> None:
        try:
            self.tick()
            self.breaker.record_success()
        except KisAuthError as e:
            self.breaker.record_fatal(f"인증 실패: {e}")
        except KisNetworkError as e:
            log.warning("네트워크 오류, 다음 주기에 재시도: %s", e)  # 중단 사유 아님 (Wi-Fi 일시 끊김 등)
        except KisApiError as e:
            self.breaker.record_error(str(e))
        except Exception as e:  # noqa: BLE001 - 어떤 예외도 루프를 죽이지 않고 연속 오류로 집계
            log.exception("예상치 못한 오류")
            self.breaker.record_error(f"{type(e).__name__}")

    # ── 실행 ──
    def run(self) -> None:
        log.info("시작", extra={"event": "start", "mode": self.s.trading_mode, "dry_run": self.s.dry_run,
                              "strategy": self.strategy.name, "symbols": self.s.symbols})
        banner = "⚠️ 실전투자" if self.s.is_live else "모의투자"
        self.notifier.send(f"🚀 kis-trader 시작 ({banner}, DRY_RUN={self.s.dry_run}, 전략={self.strategy.name})")
        if self.s.is_live:
            log.warning("실전투자 모드로 실행 중", extra={"event": "live_mode", "dry_run": self.s.dry_run})

        while not self.stop:
            try:
                restore_state(self.account, self.db)
                break
            except KisAuthError as e:
                self.breaker.record_fatal(f"시작 시 인증 실패: {e}")
                break
            except (RecoveryError, KisNetworkError) as e:
                log.error("상태 복구 실패, 5분 후 재시도: %s", e)
                self.notifier.send(f"⚠️ 상태 복구 실패: {e}\n5분 후 재시도합니다. (복구 전에는 매매하지 않음)")
                self.sleep(300)
        else:
            return

        while not self.stop:
            self.heartbeat()
            if self.breaker.halted:
                if time.monotonic() - self._halt_logged > 600:
                    log.error("매매 중단 상태 유지 중: %s", self.breaker.reason())
                    self._halt_logged = time.monotonic()
            else:
                self.safe_tick()
            self.sleep(self.s.poll_interval_sec)
        log.info("종료")


def build_strategy(s: Settings) -> Strategy:
    if s.strategy == "composite":
        return CompositeStrategy(
            buy_budget_krw=s.strategy_buy_krw or s.max_order_amount_krw,
            buy_score=s.strategy_buy_score, sell_score=s.strategy_sell_score,
            entry_after=s.strategy_entry_after_time,
        )
    return ObserveStrategy()


def main() -> int:
    try:
        settings = Settings()
        settings.require_credentials()
    except (ValidationError, ConfigError) as e:
        # 비밀값이 메시지에 섞이지 않도록 필드명/사유만 출력
        if isinstance(e, ValidationError):
            for err in e.errors():
                print(f"설정 오류: {'.'.join(str(x) for x in err['loc'])}: {err['msg']}", file=sys.stderr)
        else:
            print(f"설정 오류: {e}", file=sys.stderr)
        return 2

    register_secrets(settings.secret_values())
    setup_logging(settings.log_dir, settings.log_level)

    if settings.strategy == "multi":
        from .multi_bot import MultiBot
        bot: Bot = MultiBot(settings)
    elif settings.demo_hourly:
        from .demo_hourly import HourlyBot, build_hourly_strategy
        bot = HourlyBot(settings, build_hourly_strategy(settings))
    else:
        bot = Bot(settings, build_strategy(settings))

    def _stop(signum, frame):  # noqa: ARG001
        bot.stop = True
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    bot.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
