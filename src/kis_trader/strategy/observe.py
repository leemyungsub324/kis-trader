"""관찰 전략: 시세/잔고/지표를 로그로만 남기고 아무 주문도 내지 않는다."""
from __future__ import annotations

import logging

from ..safety.models import OrderIntent
from .base import Snapshot, Strategy

log = logging.getLogger(__name__)


class ObserveStrategy(Strategy):
    name = "observe"

    def on_tick(self, snap: Snapshot) -> list[OrderIntent]:
        for sym, price in snap.prices.items():
            ind = snap.indicators.get(sym)
            ich = ind.ichimoku if ind else None
            log.info(
                "관찰", extra={
                    "event": "observe", "symbol": sym, "price": price,
                    "bars": ind.bars if ind else 0,
                    "sma5": ind.sma5 if ind else None, "sma20": ind.sma20 if ind else None,
                    "sma60": ind.sma60 if ind else None, "rsi14": ind.rsi14 if ind else None,
                    "tenkan": ich.tenkan if ich else None, "kijun": ich.kijun if ich else None,
                    "cloud_top": ich.cloud_top if ich else None,
                    "cloud_bottom": ich.cloud_bottom if ich else None,
                    "cash": snap.cash, "equity": snap.equity,
                    "held_qty": snap.positions[sym].qty if sym in snap.positions else 0,
                },
            )
        return []
