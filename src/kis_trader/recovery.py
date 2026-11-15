"""재시작 복구: 잔고/보유종목/미체결 주문을 먼저 조회해 상태를 복원한 뒤에만 매매 로직을 시작한다."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Callable

from .kis.account import Account, Balance, OrderRow
from .kis.errors import KisAuthError, KisError
from .state.db import Database

log = logging.getLogger(__name__)


class RecoveryError(RuntimeError):
    pass


@dataclass
class BotState:
    balance: Balance
    open_orders: list[OrderRow]


def restore_state(
    account: Account, db: Database, retries: int = 5, delay_sec: float = 30,
    sleep: Callable[[float], None] = time.sleep,
) -> BotState:
    """일시적 네트워크/API 오류(부팅 직후 Wi-Fi 미연결 등)는 재시도한다. 인증 오류는 즉시 실패."""
    last: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            balance = account.get_balance()
            rows = account.get_today_orders()
            break
        except KisAuthError:
            raise
        except KisError as e:
            last = e
            log.warning("복구 조회 실패 (%d/%d): %s", attempt, retries, e)
            if attempt < retries:
                sleep(delay_sec)
    else:
        raise RecoveryError(f"상태 복구 실패: {last}")

    open_orders = Account.open_orders(rows)
    open_odnos = {o.odno for o in open_orders}
    filled = {r.odno: r for r in rows}

    # DB 에 '전송됨/불명' 으로 남은 주문과 계좌 상태를 대조
    for p in db.pending_orders():
        odno = p["odno"]
        if odno and odno in open_odnos:
            continue
        if odno and odno in filled:
            continue  # 정상 체결 처리는 sync_fills 가 반영
        db.event("recovery_unmatched_order", {"id": p["id"], "symbol": p["symbol"], "odno": odno})
        log.warning("DB 의 미확정 주문이 계좌 조회에 없음", extra={"order_id": p["id"], "symbol": p["symbol"]})

    db.event("recovered", {
        "cash": balance.cash, "equity": balance.equity,
        "positions": {s: p.qty for s, p in balance.positions.items()},
        "open_orders": len(open_orders),
    })
    log.info("상태 복원 완료", extra={
        "event": "recovered", "cash": balance.cash, "equity": balance.equity,
        "positions": {s: p.qty for s, p in balance.positions.items()},
        "open_orders": len(open_orders),
    })
    return BotState(balance, open_orders)
