"""매매 대상 종목 목록. 한국투자증권이 공개하는 종목 마스터 파일(kospi_code.mst)을 쓴다.

파일 구조는 공식 저장소 stocks_info/kis_kospi_code_mst.py 와 같다
(행 끝 228자 = 고정폭 속성 227자 + 줄바꿈, 그 앞이 단축코드 9 · 표준코드 12 · 한글명).
"""
from __future__ import annotations

import io
import os
import urllib.request
import zipfile

import pandas as pd

MST_URL = "https://new.real.download.dws.co.kr/common/master/kospi_code.mst.zip"

# 뒤쪽 227자 중 필요한 필드의 (시작, 길이). 공식 예제의 field_specs 누적 위치.
_W = [2, 1, 4, 4, 4] + [1] * 26 + [9, 5, 5, 1, 1, 1, 2, 1, 1, 1, 2, 2, 2, 3, 1, 3, 12, 12, 8,
                                   15, 21, 2, 7, 1, 1, 1, 1, 1, 9, 9, 9, 5, 9, 8, 9, 3, 1, 1, 1]
_N = ["그룹코드", "시가총액규모", "지수업종대분류", "지수업종중분류", "지수업종소분류", "제조업", "저유동성",
      "지배구조지수종목", "KOSPI200섹터업종", "KOSPI100", "KOSPI50", "KRX", "ETP", "ELW발행", "KRX100",
      "KRX자동차", "KRX반도체", "KRX바이오", "KRX은행", "SPAC", "KRX에너지화학", "KRX철강", "단기과열",
      "KRX미디어통신", "KRX건설", "Non1", "KRX증권", "KRX선박", "KRX섹터_보험", "KRX섹터_운송", "SRI",
      "기준가", "매매수량단위", "시간외수량단위", "거래정지", "정리매매", "관리종목", "시장경고", "경고예고",
      "불성실공시", "우회상장", "락구분", "액면변경", "증자구분", "증거금비율", "신용가능", "신용기간",
      "전일거래량", "액면가", "상장일자", "상장주수", "자본금", "결산월", "공모가", "우선주", "공매도과열",
      "이상급등", "KRX300", "KOSPI", "매출액", "영업이익", "경상이익", "당기순이익", "ROE", "기준년월",
      "시가총액", "그룹사코드", "회사신용한도초과", "담보대출가능", "대주가능"]
assert len(_W) == len(_N) and sum(_W) == 227
_POS = {}
_p = 0
for _n, _w in zip(_N, _W):
    _POS[_n] = (_p, _w)
    _p += _w


def parse_mst(text: str) -> pd.DataFrame:
    rows = []
    for line in text.splitlines():
        if len(line) < 250:
            continue
        head, tail = line[:-227], line[-227:]
        g = lambda k: tail[_POS[k][0]:_POS[k][0] + _POS[k][1]].strip()
        rows.append({
            "code": head[0:9].strip(), "name": head[21:].strip(), "group": g("그룹코드"),
            "spac": g("SPAC"), "halt": g("거래정지"), "delist": g("정리매매"), "admin": g("관리종목"),
            "warn": g("시장경고"), "pref": g("우선주"), "base_price": float(g("기준가") or 0),
            "mcap": float(g("시가총액") or 0),      # 억 원
        })
    return pd.DataFrame(rows)


def download_mst(cache_dir: str = "data") -> pd.DataFrame:
    os.makedirs(cache_dir, exist_ok=True)
    raw = urllib.request.urlopen(MST_URL, timeout=30).read()
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        text = z.read(z.namelist()[0]).decode("cp949")
    df = parse_mst(text)
    df.to_csv(os.path.join(cache_dir, "universe.csv"), index=False)
    return df


def filter_universe(df: pd.DataFrame, min_price: float = 1000) -> pd.DataFrame:
    """보통주만: ETF·ETN·우선주·스팩·거래정지·정리매매·관리종목·투자경고/위험 제외."""
    ok = (
        (df["group"] == "ST") & (df["spac"] != "Y") & (df["halt"] != "Y") & (df["delist"] != "Y")
        & (df["admin"] != "Y") & (~df["warn"].astype(str).isin(["2", "3", "02", "03"]))
        & (df["code"].str.len() == 6) & (df["code"].str[-1] == "0")
        & (df["base_price"] >= min_price)
    )
    return df[ok].reset_index(drop=True)


def load_universe(cache_dir: str = "data", refresh: bool = True, min_price: float = 1000) -> pd.DataFrame:
    path = os.path.join(cache_dir, "universe.csv")
    if refresh:
        try:
            return filter_universe(download_mst(cache_dir), min_price)
        except Exception as e:      # 다운로드 실패 시 캐시 사용
            if not os.path.exists(path):
                raise RuntimeError(f"종목 마스터 다운로드 실패: {e}") from e
    return filter_universe(pd.read_csv(path, dtype={"code": str, "warn": str}), min_price)
