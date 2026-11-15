"""일봉 데이터 적재.

- marcap(공개 KRX 전종목 일봉, github.com/FinanceData/marcap) → 백테스트용
- KIS API로 받은 캐시(data/daily/*.csv) → 실매매 스캔용
공통 포맷: 종목별 DataFrame(index=date, columns=open high low close volume value)
"""
from __future__ import annotations

import glob
import os

import numpy as np
import pandas as pd

COLS = ["open", "high", "low", "close", "volume", "value"]


def is_common_stock(code: str, name: str) -> bool:
    """보통주만. 우선주(끝자리≠0)·스팩·리츠·인프라펀드 제외."""
    if not code.isdigit() or code[-1] != "0":
        return False
    return not any(k in name for k in ("스팩", "리츠", "인프라", "유전", "맵스"))


def load_marcap(folder: str, markets=("KOSPI",), start="2019-01-01", end=None) -> tuple[dict, pd.DataFrame]:
    """marcap parquet들을 읽어 수정주가로 변환. (종목별 dict, 메타 DataFrame) 반환."""
    files = sorted(glob.glob(os.path.join(folder, "marcap-*.parquet")))
    if not files:
        raise FileNotFoundError(f"marcap parquet 없음: {folder}")
    use = ["Code", "Name", "Open", "High", "Low", "Close", "Volume", "Amount",
           "ChangesRatio", "Marcap", "Market", "Dept", "Date"]
    df = pd.concat([pd.read_parquet(f, columns=use) for f in files], ignore_index=True)
    mk = df["Market"].replace({"KOSDAQ GLOBAL": "KOSDAQ"})
    df = df[mk.isin(markets)].copy()
    df["Market"] = mk
    df = df[df["Date"] >= pd.Timestamp(start)]
    if end:
        df = df[df["Date"] <= pd.Timestamp(end)]
    df["admin"] = df["Dept"].fillna("").str.contains("관리|환기")
    df = df.sort_values(["Code", "Date"])
    last = df.groupby("Code").tail(1).set_index("Code")
    meta = last[["Name", "Market", "Marcap"]].rename(columns=str.lower)

    out = {}
    for code, g in df.groupby("Code", sort=False):
        if not is_common_stock(code, meta.at[code, "name"]):
            continue
        g = g[(g["Volume"] > 0) & (g["Open"] > 0)]          # 거래정지일 제거
        if len(g) < 130:
            continue
        c = g["Close"].to_numpy(float)
        raw = np.empty(len(c)); raw[0] = 0.0
        raw[1:] = c[1:] / c[:-1] - 1
        off = g["ChangesRatio"].to_numpy(float) / 100.0
        # 거래소 공식 등락률과 단순 등락률이 다르면 권리락(액면분할·증자 등) → 수정계수
        step = np.ones(len(c))
        ev = np.abs(raw - off) > 0.02
        ev[0] = False
        step[ev] = (1 + raw[ev]) / (1 + off[ev])
        # 과거 가격에 곱할 계수: 이후 발생한 이벤트 계수의 누적곱
        fac = np.concatenate([np.cumprod(step[::-1])[::-1][1:], [1.0]])
        d = pd.DataFrame({
            "open": g["Open"].to_numpy(float) * fac, "high": g["High"].to_numpy(float) * fac,
            "low": g["Low"].to_numpy(float) * fac, "close": c * fac,
            "volume": g["Volume"].to_numpy(float) / fac, "value": g["Amount"].to_numpy(float),
            "admin": g["admin"].to_numpy(bool), "mcap": g["Marcap"].to_numpy(float),
        }, index=pd.DatetimeIndex(g["Date"].to_numpy(), name="date"))
        out[code] = d
    return out, meta.loc[list(out)]


def market_proxy(data: dict, top: int = 200) -> pd.DataFrame:
    """시총 상위 top 종목의 시총가중 수익률로 만든 지수 대용(KOSPI200 ≈ KODEX 200)."""
    r = pd.DataFrame({k: v["close"].pct_change() for k, v in data.items()})
    w = pd.DataFrame({k: v["mcap"] for k, v in data.items()}).shift(1)
    rank = w.rank(axis=1, ascending=False)
    w = w.where(rank <= top)
    ret = (r * w).sum(axis=1) / w.where(r.notna()).sum(axis=1)
    px = (1 + ret.fillna(0)).cumprod() * 1000
    return pd.DataFrame({"close": px})


def load_cache(folder: str) -> dict:
    """KIS에서 받아 저장한 종목별 CSV 캐시 로드."""
    out = {}
    for f in glob.glob(os.path.join(folder, "*.csv")):
        d = pd.read_csv(f, index_col="date", parse_dates=True)
        out[os.path.basename(f)[:-4]] = d[COLS].sort_index()
    return out
