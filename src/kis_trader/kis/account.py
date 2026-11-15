"""잔고/보유종목/오늘 주문·체결 조회 (모의투자 TR ID).

미체결 전용 API(inquire-psbl-rvsecncl)는 모의 지원 여부가 불확실해, 당일 주문체결 조회
(inquire-daily-ccld, VTTC0081R)에서 잔량(rmn_qty)>0 인 건을 미체결로 간주한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from ..config import Settings
from ..safety.models import Position
from .client import KisClient
from .errors import KisApiError
from .tr import tr_ids

KST = ZoneInfo("Asia/Seoul")
TRADING = "/uapi/domestic-stock/v1/trading"
MAX_PAGES = 10


@dataclass
class Balance:
    cash: int
    equity: int  # 총평가금액(예수금 + 주식평가)
    positions: dict[str, Position] = field(default_factory=dict)


@dataclass
class OrderRow:
    odno: str
    symbol: str
    side: str  # buy / sell
    ord_qty: int
    ccld_qty: int
    avg_price: float
    remain_qty: int
    canceled: bool


def _int(v, default=0) -> int:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


class Account:
    def __init__(self, client: KisClient, settings: Settings):
        self._c = client
        self._cano = settings.account_no.get_secret_value()
        self._prdt = settings.product_code
        self._tr = tr_ids(settings)

    def get_balance(self) -> Balance:
        fk, nk, tr_cont = "", "", ""
        positions: dict[str, Position] = {}
        summary: dict = {}
        for _ in range(MAX_PAGES):
            r = self._c.call(
                "GET", f"{TRADING}/inquire-balance", self._tr.balance,
                params={
                    "CANO": self._cano, "ACNT_PRDT_CD": self._prdt,
                    "AFHR_FLPR_YN": "N", "OFL_YN": "", "INQR_DVSN": "02",
                    "UNPR_DVSN": "01", "FUND_STTL_ICLD_YN": "N",
                    "FNCG_AMT_AUTO_RDPT_YN": "N", "PRCS_DVSN": "00",
                    "CTX_AREA_FK100": fk, "CTX_AREA_NK100": nk,
                },
                tr_cont=tr_cont,
            )
            for row in r.body.get("output1") or []:
                qty = _int(row.get("hldg_qty"))
                if qty > 0:
                    sym = row.get("pdno", "")
                    positions[sym] = Position(
                        symbol=sym, qty=qty,
                        avg_price=float(row.get("pchs_avg_pric") or 0),
                        last_price=_int(row.get("prpr")),
                    )
            out2 = r.body.get("output2") or []
            if out2:
                summary = out2[0]
            if r.tr_cont in ("M", "F"):
                fk = r.body.get("ctx_area_fk100", "")
                nk = r.body.get("ctx_area_nk100", "")
                tr_cont = "N"
                continue
            break
        else:
            raise KisApiError("잔고 조회 페이지가 너무 많음")
        if not summary:
            raise KisApiError("잔고 응답에 output2 요약이 없음")
        return Balance(
            cash=_int(summary.get("dnca_tot_amt")),
            equity=_int(summary.get("tot_evlu_amt")),
            positions=positions,
        )

    def get_today_orders(self) -> list[OrderRow]:
        today = datetime.now(KST).strftime("%Y%m%d")
        fk, nk, tr_cont = "", "", ""
        rows: list[OrderRow] = []
        for _ in range(MAX_PAGES):
            r = self._c.call(
                "GET", f"{TRADING}/inquire-daily-ccld", self._tr.ccld,
                params={
                    "CANO": self._cano, "ACNT_PRDT_CD": self._prdt,
                    "INQR_STRT_DT": today, "INQR_END_DT": today,
                    "SLL_BUY_DVSN_CD": "00", "PDNO": "", "CCLD_DVSN": "00",
                    "INQR_DVSN": "00", "INQR_DVSN_3": "00", "ORD_GNO_BRNO": "",
                    "ODNO": "", "INQR_DVSN_1": "", "EXCG_ID_DVSN_CD": "KRX",
                    "CTX_AREA_FK100": fk, "CTX_AREA_NK100": nk,
                },
                tr_cont=tr_cont,
            )
            for row in r.body.get("output1") or []:
                if not row.get("odno"):
                    continue
                rows.append(OrderRow(
                    odno=row["odno"], symbol=row.get("pdno", ""),
                    side="sell" if row.get("sll_buy_dvsn_cd") == "01" else "buy",
                    ord_qty=_int(row.get("ord_qty")), ccld_qty=_int(row.get("tot_ccld_qty")),
                    avg_price=float(row.get("avg_prvs") or 0),
                    remain_qty=_int(row.get("rmn_qty")),
                    canceled=row.get("cncl_yn") == "Y",
                ))
            if r.tr_cont in ("M", "F"):
                fk = r.body.get("ctx_area_fk100", "")
                nk = r.body.get("ctx_area_nk100", "")
                tr_cont = "N"
                continue
            break
        else:
            raise KisApiError("주문체결 조회 페이지가 너무 많음")
        return rows

    @staticmethod
    def open_orders(rows: list[OrderRow]) -> list[OrderRow]:
        return [r for r in rows if r.remain_qty > 0 and not r.canceled]
