from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class OrderIntent:
    """전략이 내는 '주문 의도'. 실제 전송 여부는 Guard 가 결정한다."""
    symbol: str
    side: str  # "buy" | "sell"
    qty: int
    limit_price: int | None = None  # None 이면 시장가
    reason: str = ""
    tag: str | None = None  # 전략 카테고리 (multi 전략: surge/vbreak/swing/dip). DB orders.tag 에 기록


@dataclass(frozen=True)
class ApprovedOrder:
    """Guard 를 통과한 주문. Guard.check() 만 생성해야 한다."""
    intent: OrderIntent
    ref_price: int


@dataclass
class Position:
    symbol: str
    qty: int
    avg_price: float
    last_price: int = 0

    @property
    def value(self) -> int:
        return int(self.qty * self.last_price)

    @property
    def pnl_pct(self) -> float:
        if self.avg_price <= 0:
            return 0.0
        return (self.last_price - self.avg_price) / self.avg_price * 100


@dataclass
class RiskContext:
    cash: int
    equity: int
    day_start_equity: int
    positions: dict[str, Position] = field(default_factory=dict)
    prices: dict[str, int] = field(default_factory=dict)
    bought_today_krw: int = 0  # 오늘 매수 주문 누적 금액 (DAILY_MAX_BUY_KRW 검사용)


@dataclass
class Decision:
    approved: bool
    reasons: list[str] = field(default_factory=list)
    order: ApprovedOrder | None = None
