"""시장 국면 판정. 지수(KODEX 200 등) 일봉 종가만 사용."""
from __future__ import annotations

import pandas as pd

BULL, NEUTRAL, BEAR = "BULL", "NEUTRAL", "BEAR"


def regime_series(close: pd.Series, fast: int = 20, slow: int = 60) -> pd.Series:
    """BULL: 종가>60일선 & 20일선>60일선 / BEAR: 종가<60일선 & 20일선<60일선 / 그 외 NEUTRAL."""
    mf, ms = close.rolling(fast).mean(), close.rolling(slow).mean()
    out = pd.Series(NEUTRAL, index=close.index)
    out[(close > ms) & (mf > ms)] = BULL
    out[(close < ms) & (mf < ms)] = BEAR
    out[ms.isna()] = NEUTRAL
    return out


def market_features(close: pd.Series) -> pd.DataFrame:
    """지수 종가로 만든 시장 상태. 전략이 '시장 전체가 급락 중인가'를 판단하는 데 쓴다."""
    return pd.DataFrame({
        "close": close,
        "ret5": close / close.shift(5) - 1,                    # 지수 5일 수익률
        "dd20": close / close.rolling(20).max() - 1,           # 지수 20일 고점 대비 낙폭
        "regime": regime_series(close),
    })
