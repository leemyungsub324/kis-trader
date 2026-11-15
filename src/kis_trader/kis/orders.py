"""주문 전송. Guard 를 통과한 ApprovedOrder 만 받는다.

TR ID: kis/tr.py (모의 VTTC0012U/VTTC0011U, 실전 TTTC0012U/TTTC0011U; 공식 예제 order_cash).
ORD_DVSN: 00=지정가, 01=시장가 (공식 코드표 재확인 필요 - README 미확정 항목).
주문은 네트워크 오류 시 자동 재시도하지 않는다(중복 주문 방지). 결과를 알 수 없으면 'unknown' 으로 남기고 중단한다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from ..config import Settings
from ..safety.circuit_breaker import CircuitBreaker
from ..safety.models import ApprovedOrder
from ..state.db import Database
from .account import OrderRow
from .client import KisClient
from .errors import KisApiError, KisNetworkError
from .tr import tr_ids

log = logging.getLogger(__name__)
ORDER_PATH = "/uapi/domestic-stock/v1/trading/order-cash"
CANCEL_PATH = "/uapi/domestic-stock/v1/trading/order-rvsecncl"


@dataclass
class OrderResult:
    status: str  # dry_run | submitted | unknown
    odno: str | None = None


class OrderService:
    def __init__(self, client: KisClient, settings: Settings, db: Database, breaker: CircuitBreaker, notify):
        self._c = client
        self._s = settings
        self._db = db
        self._breaker = breaker
        self._notify = notify
        self._cano = settings.account_no.get_secret_value()
        self._tr = tr_ids(settings)

    def submit(self, ap: ApprovedOrder) -> OrderResult:
        it = ap.intent
        common = dict(symbol=it.symbol, side=it.side, qty=it.qty, limit_price=it.limit_price,
                      ref_price=ap.ref_price, reason=it.reason, tag=it.tag)
        label = "매수" if it.side == "buy" else "매도"

        if self._s.dry_run:
            self._db.add_order(**common, dry_run=True, status="dry_run")
            log.info("[DRY-RUN] %s 주문 미전송", label,
                     extra={"event": "order", "symbol": it.symbol, "side": it.side, "qty": it.qty,
                            "ref_price": ap.ref_price, "reason": it.reason})
            self._notify(f"[DRY-RUN] {label} {it.symbol} x{it.qty} (기준가 {ap.ref_price:,}) - {it.reason}")
            return OrderResult("dry_run")

        body = {
            "CANO": self._cano,
            "ACNT_PRDT_CD": self._s.product_code,
            "PDNO": it.symbol,
            "ORD_DVSN": "00" if it.limit_price else "01",
            "ORD_QTY": str(it.qty),
            "ORD_UNPR": str(it.limit_price) if it.limit_price else "0",
            "EXCG_ID_DVSN_CD": "KRX",
            "SLL_TYPE": "01" if it.side == "sell" else "",  # 01 일반매도 (공식 예제 order_cash)
            "CNDT_PRIC": "",  # 스탑지정가 조건가격: 미사용
        }
        row_id = self._db.add_order(**common, dry_run=False, status="unknown")  # 전송 전에 먼저 기록
        try:
            r = self._c.call("POST", ORDER_PATH, self._tr.buy if it.side == "buy" else self._tr.sell,
                             body=body, retry_network=False)
        except KisNetworkError:
            self._breaker.record_fatal(f"주문 결과 불명(네트워크 오류): {label} {it.symbol} x{it.qty} - 계좌에서 직접 확인 필요")
            return OrderResult("unknown")
        except KisApiError as e:
            self._db.update_order(row_id, status="rejected")
            log.error("주문 거부: %s", e, extra={"event": "order_rejected", "symbol": it.symbol})
            self._notify(f"⚠️ 주문 거부 {label} {it.symbol} x{it.qty}: {e}")
            raise
        out = r.body.get("output") or {}
        odno = out.get("ODNO")
        if not odno:
            self._breaker.record_fatal(f"주문 응답에 주문번호 없음: {label} {it.symbol} - 계좌에서 직접 확인 필요")
            return OrderResult("unknown")
        self._db.update_order(row_id, status="submitted", odno=odno, orgno=out.get("KRX_FWDG_ORD_ORGNO"))
        kind = f"지정가 {it.limit_price:,}" if it.limit_price else "시장가"
        log.info("주문 전송 %s", label, extra={"event": "order", "symbol": it.symbol, "side": it.side,
                                              "qty": it.qty, "odno": odno, "limit_price": it.limit_price,
                                              "tag": it.tag, "reason": it.reason})
        self._notify(f"📨 주문 전송 {label} {it.symbol} x{it.qty} {kind} (주문번호 {odno})\n{it.reason}")
        return OrderResult("submitted", odno)

    def cancel(self, row) -> bool:
        """미체결 잔량 전부 취소 (DB orders 행). 성공하면 True.

        실패(이미 전량 체결됐거나 취소 불가)는 예외로 올리지 않는다. 체결 여부는 잔고/체결조회가 반영한다.
        네트워크 오류는 재시도하지 않고 다음 주기에 다시 시도한다(취소는 중복돼도 안전).
        """
        if not row["odno"]:
            return False
        body = {
            "CANO": self._cano, "ACNT_PRDT_CD": self._s.product_code,
            "KRX_FWDG_ORD_ORGNO": row["orgno"] or "", "ORGN_ODNO": row["odno"],
            "ORD_DVSN": "00", "RVSE_CNCL_DVSN_CD": "02",  # 02 = 취소
            "ORD_QTY": "0", "ORD_UNPR": "0", "QTY_ALL_ORD_YN": "Y", "EXCG_ID_DVSN_CD": "KRX",
        }
        try:
            self._c.call("POST", CANCEL_PATH, self._tr.cancel, body=body, retry_network=False)
        except KisNetworkError as e:
            log.warning("주문 취소 네트워크 오류, 다음 주기에 재시도: %s", e)
            return False
        except KisApiError as e:
            # 대개 '이미 전량 체결'. 더는 미체결로 취급하지 않도록 상태를 바꾼다(포지션은 잔고로 관리됨).
            self._db.update_order(row["id"], status="cancel_failed")
            log.warning("주문 취소 실패 %s: %s", row["symbol"], e,
                        extra={"event": "cancel_failed", "symbol": row["symbol"], "odno": row["odno"]})
            return False
        self._db.update_order(row["id"], status="canceled")
        log.info("미체결 취소", extra={"event": "order_canceled", "symbol": row["symbol"], "odno": row["odno"],
                                    "filled_qty": row["filled_qty"], "qty": row["qty"]})
        return True

    def sync_fills(self, rows: list[OrderRow]) -> None:
        """전송했던 주문이 당일 체결 조회에 나타나면 체결 상태를 반영하고 알린다."""
        by_odno = {r.odno: r for r in rows}
        for p in self._db.pending_orders():
            row = by_odno.get(p["odno"]) if p["odno"] else None
            if row is None:
                continue
            if row.ccld_qty > p["filled_qty"]:
                done = row.ccld_qty >= row.ord_qty
                self._db.update_order(p["id"], filled_qty=row.ccld_qty, avg_fill_price=row.avg_price,
                                      status="filled" if done else "submitted")
                log.info("체결", extra={"event": "fill", "symbol": row.symbol, "odno": row.odno,
                                      "ccld_qty": row.ccld_qty, "avg_price": row.avg_price})
                side = "매수" if row.side == "buy" else "매도"
                self._notify(f"✅ 체결 {side} {row.symbol} {row.ccld_qty}/{row.ord_qty}주 평균 {row.avg_price:,.0f}원")
            elif row.canceled:
                self._db.update_order(p["id"], status="rejected")
