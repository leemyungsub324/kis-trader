"""1시간봉 데모: composite 전략(MA + 일목 + RSI 종합점수)을 1시간봉으로 매시 판단한다.

켜는 법(.env): DEMO_HOURLY=true, DEMO_DATE=YYYY-MM-DD, STRATEGY=composite
- DEMO_DATE(하루 또는 'YYYY-MM-DD~YYYY-MM-DD' 기간) 날짜에만 전략 주문을 낸다. 다른 날에는 손절 감시만 한다.
- 1시간봉 = 1분봉을 시간 단위로 묶은 것. 09시봉(09:00~10:00) ... 15시봉(15:00~15:30, 종가 단일가 포함).
- 판단 시점: 09시대(전일까지의 봉, 매수는 STRATEGY_ENTRY_AFTER 이후), 10:00 이후(09시봉까지),
  11:00 이후(10시봉까지) ... 15:00 이후(14시봉까지). 하루 최대 7번. 매 시간대 종목·방향별 1회만 주문.
- 진행 중인 시간봉은 쓰지 않는다(룩어헤드 방지). 일목 선행스팬2(52)+이동(26)=78봉이 필요해
  장 시작 전(08:00 이후 첫 주기)에 최근 13거래일 1분봉을 받아 둔다(약 91개 시간봉, 종목당 50여 회 호출).
- 장마감 청산(데모 전용): 15:00 이후 신규 매수 없음, 15:15 에 WATCHLIST 보유분 전량 시장가 매도.
  (ORDER_END 15:20 전, 15:20~15:30 종가 동시호가 전에 정리해 데모 결과를 당일 손익으로 확정)
"""
from __future__ import annotations

import logging
import time as _time
from datetime import date, datetime, time

from .indicators import IndicatorSet, compute_all
from .kis.errors import KisError
from .kis.market import MinuteBar
from .main import Bot
from .safety.models import OrderIntent, RiskContext
from .strategy.base import Snapshot
from .strategy.composite import CompositeStrategy

log = logging.getLogger("kis_trader.demo_hourly")

HIST_DAYS = 13
WARMUP_FROM = time(8, 0)
WARMUP_RETRY_SEC = 300
NO_NEW_BUY_FROM = time(15, 0)   # 청산 직전 매수 방지
LIQUIDATE_AT = time(15, 15)     # 장마감 청산 시각 (ORDER_END 15:20 이전)


def hour_bucket(hhmmss: str) -> int:
    """1분봉 체결시각(봉 종료 시각) -> 시간봉 번호. 09:00:00 단일가와 15:30 종가는 각각 9, 15시봉."""
    minutes = int(hhmmss[:2]) * 60 + int(hhmmss[2:4])
    if minutes <= 9 * 60:
        return 9
    return min(15, (minutes - 1) // 60)


def bucket_closed(h: int, now: datetime) -> bool:
    end = time(15, 30) if h >= 15 else time(h + 1, 0)
    return now.time() >= end


def to_hourly(bars: list[MinuteBar]) -> list[tuple[date, int, float, float, float, float]]:
    """(날짜, 시, 시가, 고가, 저가, 종가) 오래된 -> 최신."""
    groups: dict[tuple[date, int], list[MinuteBar]] = {}
    for b in sorted(bars, key=lambda x: (x.day, x.hhmmss)):
        groups.setdefault((b.day, hour_bucket(b.hhmmss)), []).append(b)
    out = []
    for (d, h), g in sorted(groups.items()):
        out.append((d, h, g[0].open, max(x.high for x in g), min(x.low for x in g), g[-1].close))
    return out


class HourlyCompositeStrategy(CompositeStrategy):
    name = "composite-1h"

    def period(self, snap: Snapshot) -> object:
        return (snap.now.date(), snap.now.hour)


def build_hourly_strategy(s) -> HourlyCompositeStrategy:
    return HourlyCompositeStrategy(
        buy_budget_krw=s.strategy_buy_krw or s.max_order_amount_krw,
        buy_score=s.strategy_buy_score, sell_score=s.strategy_sell_score,
        entry_after=s.strategy_entry_after_time, entry_before=NO_NEW_BUY_FROM,
    )


class HourlyBot(Bot):
    def __init__(self, s, strategy):
        super().__init__(s, strategy)
        self._hist: dict[str, list[MinuteBar]] = {}        # 과거 거래일 1분봉 (고정)
        self._hist_day: date | None = None
        self._hist_fail_at = 0.0
        self._today_min: dict[str, dict[str, MinuteBar]] = {}  # 당일 1분봉 (시각 -> 봉)
        self._hourly_cache: dict[str, tuple[tuple[date, int], IndicatorSet]] = {}
        self._off_day_logged: date | None = None

    # ── 과거 1분봉 준비 ──
    def load_history(self, today: date) -> bool:
        if self._hist_day == today:
            return True
        if _time.monotonic() - self._hist_fail_at < WARMUP_RETRY_SEC and self._hist_fail_at:
            return False
        try:
            hist: dict[str, list[MinuteBar]] = {}
            for sym in self.s.symbols:
                days = [c.day for c in self.market.get_daily_candles(sym) if c.day < today][-HIST_DAYS:]
                bars: list[MinuteBar] = []
                for d in days:
                    bars += self.market.get_minute_bars(sym, d)
                    self.heartbeat()  # 준비에 수 분 걸리므로 헬스체크가 멈춤으로 오인하지 않게
                hist[sym] = bars
                log.info("1시간봉 과거 데이터 준비", extra={
                    "event": "hourly_warmup", "symbol": sym, "days": len(days),
                    "minute_bars": len(bars), "hour_bars": len(to_hourly(bars))})
        except KisError as e:
            self._hist_fail_at = _time.monotonic()
            log.warning("1시간봉 과거 데이터 준비 실패, 5분 후 재시도: %s", e)
            return False
        self._hist, self._hist_day = hist, today
        self._today_min = {}
        self._hourly_cache = {}
        return True

    # ── 당일 1분봉 (증분 조회) ──
    def refresh_today(self, sym: str, now: datetime) -> list[MinuteBar]:
        cache = self._today_min.setdefault(sym, {})
        stop = max(cache) if cache else "090000"
        until = min(now.strftime("%H%M%S"), "153000")
        if until > stop:
            for b in self.market.get_minute_bars(sym, now.date(), until=until, stop=stop, today=True):
                cache[b.hhmmss] = b
        return list(cache.values())

    # ── 지표: 마감된 1시간봉 기준 ──
    def indicators_for(self, symbol: str, today: date, now: datetime) -> IndicatorSet:
        key = (today, now.hour)
        cached = self._hourly_cache.get(symbol)
        if cached and cached[0] == key:
            return cached[1]
        bars = list(self._hist.get(symbol, []))
        if now.time() >= time(10, 0):  # 09시봉이 마감된 뒤부터 당일 데이터 사용
            bars += self.refresh_today(symbol, now)
        hourly = [h for h in to_hourly(bars) if h[0] < today or bucket_closed(h[1], now)]
        ind = compute_all([h[3] for h in hourly], [h[4] for h in hourly], [h[5] for h in hourly])
        last = hourly[-1] if hourly else None
        log.info("1시간봉 지표 갱신", extra={
            "event": "hourly_indicators", "symbol": symbol, "bars": ind.bars,
            "last_bar": f"{last[0]} {last[1]:02d}시" if last else None, "close": ind.last_close,
            "rsi14": ind.rsi14, "sma20": ind.sma20})
        self._hourly_cache[symbol] = (key, ind)
        return ind

    # ── 주기 ──
    def tick(self) -> None:
        now = datetime.now(self._kst())
        if self.s.is_demo_day(now.date()) and now.time() >= WARMUP_FROM:
            self.load_history(now.date())
        super().tick()

    def run_strategy(self, ctx: RiskContext, bal, prices: dict[str, int], now: datetime) -> None:
        today = now.date()
        if not self.s.is_demo_day(today):
            if self._off_day_logged != today:
                log.info("데모 날짜가 아니므로 전략 주문 없음 (손절 감시만)", extra={
                    "event": "demo_off_day", "today": str(today), "demo_date": self.s.demo_date})
                self._off_day_logged = today
            return
        if now.time() >= LIQUIDATE_AT:
            return  # 청산은 exit_phase 에서 처리 (체결조회/시세 조회보다 먼저)
        if not self.load_history(today):
            return  # 과거 데이터 없이는 판단하지 않음
        indicators = {sym: self.indicators_for(sym, today, now) for sym in self.s.symbols}
        hour_start = now.replace(minute=0, second=0, microsecond=0)
        snap = Snapshot(now=now, prices=prices, indicators=indicators, cash=self.spendable_cash(ctx),
                        equity=bal.equity, positions=bal.positions,
                        orders_today=self.db.order_sides_since(hour_start))
        for intent in self.strategy.on_tick(snap):
            self.process(intent, ctx, now)

    def liquidation_intents(self, positions, already_sold: set[tuple[str, str]]) -> list[OrderIntent]:
        return [
            OrderIntent(sym, "sell", p.qty, None, "데모 장마감 청산")
            for sym, p in positions.items()
            if p.qty > 0 and sym in self.s.symbols and (sym, "sell") not in already_sold
        ]

    def exit_phase(self, ctx: RiskContext, now: datetime) -> None:
        """15:15 이후 WATCHLIST 보유분 전량 시장가 매도 (데모 기간에만). 종목당 1회(DB 기록 기준), 거부 시 다음 주기 재시도."""
        if not self.s.is_demo_day(now.date()) or now.time() < LIQUIDATE_AT:
            return
        since = now.replace(hour=LIQUIDATE_AT.hour, minute=LIQUIDATE_AT.minute, second=0, microsecond=0)
        intents = self.liquidation_intents(ctx.positions, self.db.order_sides_since(since))
        for intent in intents:
            log.info("데모 장마감 청산", extra={"event": "demo_liquidate", "symbol": intent.symbol, "qty": intent.qty})
        self.process_all(intents, ctx, now)

    @staticmethod
    def _kst():
        from .main import KST
        return KST
