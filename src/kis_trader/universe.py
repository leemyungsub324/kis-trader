"""코스피 전 종목 목록 (KIS 종목 마스터 파일, 인증 불필요).

출처: 공식 저장소 stocks_info/kis_kospi_code_mst.py. 파일은 cp949 고정폭이며, 각 행의 뒤 227자가
속성 영역이다(공식 예제의 228 은 줄바꿈 포함). 2026-11-14 파일로 형식 확인: 보통주 798종목.
"""
from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass

import httpx

KOSPI_MST_URL = "https://new.real.download.dws.co.kr/common/master/kospi_code.mst.zip"
_TAIL = 227
# 속성 영역 안의 (시작, 길이). 공식 예제 field_specs 누적합으로 계산.
_GROUP = (0, 2)          # 'ST' = 주권
_SPAC = (29, 1)
_BASE_PRICE = (41, 9)    # 기준가(전일 종가)
_HALT = (60, 1)          # 거래정지
_CLEARING = (61, 1)      # 정리매매
_ADMIN = (62, 1)         # 관리종목
_WARN = (63, 2)          # 시장경고: 00 없음, 01 주의, 02 경고, 03 위험
_PREV_VOL = (81, 12)     # 전일거래량
_PREFERRED = (158, 1)    # 우선주: 0 = 보통주


@dataclass(frozen=True)
class Listing:
    symbol: str
    name: str
    base_price: int
    prev_volume: int

    @property
    def prev_turnover(self) -> int:
        return self.base_price * self.prev_volume


def _field(tail: str, spec: tuple[int, int]) -> str:
    return tail[spec[0]: spec[0] + spec[1]]


def _int(text: str) -> int:
    try:
        return int(text.strip() or 0)
    except ValueError:
        return 0


def parse_kospi_master(text: str) -> list[Listing]:
    """매매 가능한 코스피 보통주만 반환 (ETF/ETN/우선주/스팩/거래정지/관리/정리매매/투자경고 이상 제외)."""
    out: list[Listing] = []
    for row in text.splitlines():
        if len(row) <= _TAIL + 21:
            continue
        head, tail = row[: len(row) - _TAIL], row[len(row) - _TAIL:]
        code = head[0:9].strip()
        if len(code) != 6 or _field(tail, _GROUP) != "ST":
            continue
        if _field(tail, _PREFERRED) != "0" or _field(tail, _SPAC) == "Y":
            continue
        if "Y" in (_field(tail, _HALT), _field(tail, _CLEARING), _field(tail, _ADMIN)):
            continue
        if _field(tail, _WARN) in ("02", "03"):
            continue
        out.append(Listing(code, head[21:].strip(), _int(_field(tail, _BASE_PRICE)), _int(_field(tail, _PREV_VOL))))
    return out


def fetch_kospi_listings(http: httpx.Client) -> list[Listing]:
    r = http.get(KOSPI_MST_URL, timeout=30)
    r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        raw = z.read(z.namelist()[0])
    listings = parse_kospi_master(raw.decode("cp949", errors="replace"))
    if len(listings) < 300:
        raise ValueError(f"종목 마스터 파싱 결과가 비정상적으로 적음: {len(listings)}종목 (파일 형식 변경 가능성)")
    return listings


def liquid(listings: list[Listing], min_price: int, min_turnover: int, limit: int) -> list[Listing]:
    """전일 종가·거래대금으로 1차 선별(추가 API 호출 없음). 거래대금 큰 순으로 limit 개."""
    ok = [x for x in listings if x.base_price >= min_price and x.prev_turnover >= min_turnover]
    ok.sort(key=lambda x: x.prev_turnover, reverse=True)
    return ok[:limit]
