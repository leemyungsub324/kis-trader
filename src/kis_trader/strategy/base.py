"""교체 가능한 전략 인터페이스.

전략은 '주문 의도(OrderIntent)'만 반환한다. API 를 직접 호출하지 않으며,
의도는 Guard 의 모든 검사를 통과해야만 전송된다.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime

from ..indicators import IndicatorSet
from ..safety.models import OrderIntent, Position


@dataclass
class Snapshot:
    now: datetime
    prices: dict[str, int]
    indicators: dict[str, IndicatorSet]  # 마감된 일봉 기준 (수정주가)
    cash: int
    equity: int
    positions: dict[str, Position]
    open_orders: list = field(default_factory=list)
    orders_today: set[tuple[str, str]] = field(default_factory=set)  # DB 에 기록된 오늘의 (종목, side)
    vkospi: float | None = None  # 데이터 공급자 확정 전까지 항상 None (미사용)


class Strategy(ABC):
    name: str = "base"

    @abstractmethod
    def on_tick(self, snap: Snapshot) -> list[OrderIntent]:
        """주기마다 호출. 주문할 것이 없으면 빈 리스트."""
