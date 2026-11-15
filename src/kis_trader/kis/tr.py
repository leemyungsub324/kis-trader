"""모드별 TR ID. 출처: 공식 예제(koreainvestment/open-trading-api examples_llm).

모의 TR ID 는 실전 ID 의 첫 글자 T/J/C 를 V 로 바꾼 규칙이다 (공식 kis_auth._url_fetch).
시세(FHKST*)는 실전/모의가 같다.
"""
from __future__ import annotations

from dataclasses import dataclass

from ..config import Settings


@dataclass(frozen=True)
class TrIds:
    balance: str
    buy: str
    sell: str
    ccld: str
    cancel: str  # 주문 정정취소 (공식 예제 order_rvsecncl)
    holiday: tuple[str, ...]  # 순서대로 시도


PAPER = TrIds("VTTC8434R", "VTTC0012U", "VTTC0011U", "VTTC0081R", "VTTC0013U", ("VTCA0903R", "CTCA0903R"))
LIVE = TrIds("TTTC8434R", "TTTC0012U", "TTTC0011U", "TTTC0081R", "TTTC0013U", ("CTCA0903R",))


def tr_ids(settings: Settings) -> TrIds:
    return LIVE if settings.is_live else PAPER
