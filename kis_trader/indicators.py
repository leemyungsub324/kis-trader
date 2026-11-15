"""차트 지표. 모두 pandas Series 입력·출력이며 미래 데이터를 참조하지 않는다."""
from __future__ import annotations

import numpy as np
import pandas as pd


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.rolling(n, min_periods=n).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    """Wilder RSI."""
    d = close.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False, min_periods=n).mean()
    out = 100 - 100 / (1 + up / dn.replace(0, np.nan))
    return out.where(dn != 0, 100.0).where(up.notna())


def atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    pc = close.shift(1)
    tr = pd.concat([high - low, (high - pc).abs(), (low - pc).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def macd(close: pd.Series, fast: int = 12, slow: int = 26, sig: int = 9):
    line = ema(close, fast) - ema(close, slow)
    signal = line.ewm(span=sig, adjust=False, min_periods=sig).mean()
    return line, signal


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    """전략 공용 피처. df 컬럼: open high low close volume value (날짜 오름차순)."""
    o, h, l, c, v = df["open"], df["high"], df["low"], df["close"], df["volume"]
    f = df.copy()
    f["chg"] = c.pct_change()
    for n in (5, 20, 60, 120):
        f[f"ma{n}"] = sma(c, n)
    f["atr"] = atr(h, l, c, 14)
    f["rsi2"] = rsi(c, 2)
    f["vol_ma20"] = sma(v, 20).shift(1)          # 당일 제외 직전 20일 평균
    f["val_ma20"] = sma(df["value"], 20).shift(1)
    f["vol_ratio"] = v / f["vol_ma20"]
    f["hh20"] = h.rolling(20, min_periods=20).max().shift(1)   # 직전 20일 고가
    f["hh120"] = h.rolling(120, min_periods=60).max()
    f["ret60"] = c / c.shift(60) - 1
    rng = (h - l)
    f["clv"] = ((c - l) / rng.replace(0, np.nan)).fillna(0.5)  # 종가 위치(0=저가,1=고가)
    f["range"] = rng
    f["max_chg5"] = f["chg"].rolling(5, min_periods=1).max().shift(1)
    return f
