"""주문 전 모든 안전 검사를 한 곳에서 수행하는 단일 관문.

모든 주문은 Guard.check() 를 통과해 ApprovedOrder 를 받아야만 전송된다.
- 매수: 수량/금액/예수금/종목별·전체 포지션/감시종목/일일손실 검사
- 매도: 보유수량 검사 (손절 청산이 한도에 막히지 않도록 금액 한도는 적용하지 않음)
- 공통: HALT 여부, 장 운영시간/휴장일(확인 불가 시 거부), 종목코드 형식
"""
from __future__ import annotations

import re
from datetime import datetime

from ..config import Settings
from .calendar import MarketCalendar
from .circuit_breaker import CircuitBreaker
from .models import ApprovedOrder, Decision, OrderIntent, Position, RiskContext

_SYMBOL_RE = re.compile(r"[0-9A-Z]{6}")


class Guard:
    def __init__(self, settings: Settings, calendar: MarketCalendar, breaker: CircuitBreaker):
        self._s = settings
        self._cal = calendar
        self._breaker = breaker
        self.extra_symbols: set[str] = set()  # multi 전략이 스캔한 매수 허용 종목 (WATCHLIST 에 더해짐)

    def daily_loss(self, ctx: RiskContext) -> int:
        return ctx.day_start_equity - ctx.equity

    def loss_limit_hit(self, ctx: RiskContext) -> bool:
        return self.daily_loss(ctx) >= self._s.daily_max_loss_krw

    def check(self, intent: OrderIntent, ctx: RiskContext, now: datetime) -> Decision:
        why: list[str] = []

        if self._breaker.halted:
            why.append(f"매매 중단 상태: {self._breaker.reason()}")
        ok, msg = self._cal.can_order(now)
        if not ok:
            why.append(msg)
        if self.loss_limit_hit(ctx):
            why.append(f"일일 최대 손실 도달 ({self.daily_loss(ctx):,}원)")

        if intent.side not in ("buy", "sell"):
            why.append(f"side 오류: {intent.side!r}")
        if not _SYMBOL_RE.fullmatch(intent.symbol):
            why.append(f"종목코드 형식 오류: {intent.symbol!r}")
        if not isinstance(intent.qty, int) or intent.qty <= 0:
            why.append("수량은 1 이상의 정수여야 함")

        ref = intent.limit_price or ctx.prices.get(intent.symbol)
        if not ref or ref <= 0:
            why.append("기준 가격을 알 수 없음")

        if not why and ref:
            pos = ctx.positions.get(intent.symbol, Position(intent.symbol, 0, 0.0, 0))
            if intent.side == "buy":
                why += self._check_buy(intent, ctx, pos, ref)
            else:
                if intent.qty > pos.qty:
                    why.append(f"보유수량 초과 매도 (보유 {pos.qty}, 요청 {intent.qty})")

        if why:
            return Decision(False, why)
        return Decision(True, [], ApprovedOrder(intent, int(ref)))

    def _check_buy(self, it: OrderIntent, ctx: RiskContext, pos: Position, ref: int) -> list[str]:
        s = self._s
        why: list[str] = []
        amount = it.qty * ref
        if it.symbol not in s.symbols and it.symbol not in self.extra_symbols:
            why.append("WATCHLIST/스캔 유니버스에 없는 종목")
        if it.qty > s.max_order_qty:
            why.append(f"1회 최대 수량 초과 ({it.qty} > {s.max_order_qty})")
        if amount > s.max_order_amount_krw:
            why.append(f"1회 최대 금액 초과 ({amount:,} > {s.max_order_amount_krw:,})")
        if s.daily_max_buy_krw and ctx.bought_today_krw + amount > s.daily_max_buy_krw:
            why.append(f"일일 매수 한도 초과 ({ctx.bought_today_krw + amount:,} > {s.daily_max_buy_krw:,})")
        if amount > ctx.cash:
            why.append(f"예수금 부족 ({amount:,} > {ctx.cash:,})")
        sym_after = (pos.qty + it.qty) * ref
        if sym_after > s.max_position_per_symbol_krw:
            why.append(f"종목별 포지션 한도 초과 ({sym_after:,} > {s.max_position_per_symbol_krw:,})")
        total_now = sum(p.value for p in ctx.positions.values())
        if total_now + amount > s.max_total_position_krw:
            why.append(f"전체 포지션 한도 초과 ({total_now + amount:,} > {s.max_total_position_krw:,})")
        return why


def stop_loss_intents(positions: dict[str, Position], stop_loss_pct: float) -> list[OrderIntent]:
    """전략과 무관하게 손절선(평균단가 대비 -N%) 이하인 보유종목의 전량 시장가 매도 의도."""
    out = []
    for p in positions.values():
        if p.qty > 0 and p.last_price > 0 and p.pnl_pct <= -stop_loss_pct:
            out.append(OrderIntent(
                p.symbol, "sell", p.qty, None,
                f"손절 {p.pnl_pct:.2f}% <= -{stop_loss_pct}%",
            ))
    return out
