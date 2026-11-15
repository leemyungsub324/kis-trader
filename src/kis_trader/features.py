"""종목별 일봉 특징값 (순수 계산). 입력은 '마감된 일봉'만, 오래된 -> 최신 순서.

multi 전략의 스캔 단계가 종목마다 한 번 계산해 하루 동안 쓴다. dict 로 직렬화해 DB(kv)에 저장한다.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from .indicators import rsi, sma

MIN_BARS = 61  # SMA60 + 전일 비교


def _ema_series(values: list[float], n: int) -> list[float]:
    k = 2 / (n + 1)
    out = [values[0]]
    for v in values[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def macd(closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9) -> tuple[float, float, float] | None:
    """(MACD, Signal, 전일 MACD). 데이터가 부족하면 None."""
    if len(closes) < slow + signal + 1:
        return None
    line = [f - s for f, s in zip(_ema_series(closes, fast), _ema_series(closes, slow))]
    sig = _ema_series(line, signal)
    return line[-1], sig[-1], line[-2]


def atr(highs: list[float], lows: list[float], closes: list[float], n: int = 14) -> float | None:
    """Wilder ATR."""
    if len(closes) < n + 1:
        return None
    trs = [max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
           for i in range(1, len(closes))]
    a = sum(trs[:n]) / n
    for tr in trs[n:]:
        a = (a * (n - 1) + tr) / n
    return a


@dataclass
class Features:
    symbol: str
    close: float            # 전일 종가
    prev_open: float
    prev_high: float
    prev_low: float
    sma5: float
    sma20: float
    sma60: float
    sma20_prev5: float      # 5봉 전 SMA20 (기울기)
    rsi14: float
    rsi2: float
    atr14: float
    macd: float
    macd_signal: float
    macd_prev: float
    bb_lower: float         # 볼린저 하단 (20, 2σ)
    vol_avg20: float        # 전일까지 20일 평균 거래량 (wikidocs 급등주 기준선)
    turnover_avg20: float   # 20일 평균 거래대금(원, 종가×거래량 근사)
    prev_vol_ratio: float   # 전일 거래량 / 그 이전 20일 평균
    high60: float           # 최근 60봉 고가
    ret5: float             # 수익률(%)
    ret20: float
    ret60: float
    noise20: float          # 평균 노이즈 비율 1-|시-종|/(고-저)  (변동성 돌파 k)
    down_streak: int        # 연속 하락 일수

    @property
    def atr_pct(self) -> float:
        return self.atr14 / self.close * 100 if self.close else 0.0

    @property
    def prev_range(self) -> float:
        return self.prev_high - self.prev_low

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Features":
        return cls(**d)


def compute_features(symbol: str, opens: list[float], highs: list[float], lows: list[float],
                     closes: list[float], volumes: list[float]) -> Features | None:
    n = len(closes)
    if n < MIN_BARS or closes[-1] <= 0:
        return None
    m = macd(closes)
    a = atr(highs, lows, closes)
    r14, r2 = rsi(closes, 14), rsi(closes, 2)
    if m is None or a is None or r14 is None or r2 is None:
        return None
    last20 = closes[-20:]
    mean20 = sum(last20) / 20
    sd20 = (sum((c - mean20) ** 2 for c in last20) / 20) ** 0.5
    vol20 = sum(volumes[-20:]) / 20
    vol20_before = sum(volumes[-21:-1]) / 20
    noises = []
    for o, h, l, c in zip(opens[-20:], highs[-20:], lows[-20:], closes[-20:]):
        noises.append(1 - abs(o - c) / (h - l) if h > l else 1.0)
    streak = 0
    for i in range(n - 1, 0, -1):
        if closes[i] < closes[i - 1]:
            streak += 1
        else:
            break

    def ret(k: int) -> float:
        return (closes[-1] / closes[-1 - k] - 1) * 100 if closes[-1 - k] > 0 else 0.0

    return Features(
        symbol=symbol, close=closes[-1], prev_open=opens[-1], prev_high=highs[-1], prev_low=lows[-1],
        sma5=sma(closes, 5), sma20=mean20, sma60=sma(closes, 60), sma20_prev5=sma(closes[:-5], 20),
        rsi14=r14, rsi2=r2, atr14=a, macd=m[0], macd_signal=m[1], macd_prev=m[2],
        bb_lower=mean20 - 2 * sd20, vol_avg20=vol20,
        turnover_avg20=sum(c * v for c, v in zip(last20, volumes[-20:])) / 20,
        prev_vol_ratio=volumes[-1] / vol20_before if vol20_before > 0 else 0.0,
        high60=max(highs[-60:]), ret5=ret(5), ret20=ret(20), ret60=ret(60),
        noise20=sum(noises) / len(noises), down_streak=streak,
    )
