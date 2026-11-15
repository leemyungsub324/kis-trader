"""지표 계산 (순수 파이썬). 입력은 오래된 것 -> 최신 순서의 '마감된 일봉'.

룩어헤드 방지: 각 함수는 마지막 원소(최신 마감봉) 시점에서 알 수 있는 값만 사용한다.
"""
from __future__ import annotations

from dataclasses import dataclass, field


def sma(values: list[float], n: int) -> float | None:
    if n <= 0 or len(values) < n:
        return None
    return sum(values[-n:]) / n


def ema(values: list[float], n: int) -> float | None:
    if n <= 0 or len(values) < n:
        return None
    k = 2 / (n + 1)
    e = sum(values[:n]) / n  # 첫 EMA 는 SMA 로 시작
    for v in values[n:]:
        e = v * k + e * (1 - k)
    return e


def rsi(closes: list[float], n: int = 14) -> float | None:
    """Wilder RSI. 데이터가 n+1 개 미만이면 None."""
    if len(closes) < n + 1:
        return None
    gains = []
    losses = []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(d, 0.0))
        losses.append(max(-d, 0.0))
    avg_g = sum(gains[:n]) / n
    avg_l = sum(losses[:n]) / n
    for g, l in zip(gains[n:], losses[n:]):
        avg_g = (avg_g * (n - 1) + g) / n
        avg_l = (avg_l * (n - 1) + l) / n
    if avg_l == 0:
        return 100.0 if avg_g > 0 else 50.0
    rs = avg_g / avg_l
    return 100 - 100 / (1 + rs)


def _mid(highs: list[float], lows: list[float], end: int, n: int) -> float | None:
    """end(포함) 까지 n 개 구간의 (최고가+최저가)/2. end 는 인덱스."""
    start = end - n + 1
    if start < 0 or end >= len(highs):
        return None
    return (max(highs[start : end + 1]) + min(lows[start : end + 1])) / 2


@dataclass
class Ichimoku:
    tenkan: float | None = None          # 전환선(9)
    kijun: float | None = None           # 기준선(26)
    span_a_now: float | None = None      # 오늘 위치에 그려진 선행스팬1 (26봉 전 값)
    span_b_now: float | None = None      # 오늘 위치에 그려진 선행스팬2 (26봉 전 값)
    span_a_future: float | None = None   # 26봉 뒤에 그려질 선행스팬1 (오늘 계산)
    span_b_future: float | None = None
    chikou_vs_price: float | None = None  # 오늘 종가 - 26봉 전 종가 (후행스팬 위치 판단용)
    cloud_top: float | None = None
    cloud_bottom: float | None = None


def ichimoku(
    highs: list[float], lows: list[float], closes: list[float],
    conv: int = 9, base: int = 26, span_b: int = 52, shift: int = 26,
) -> Ichimoku:
    n = len(closes)
    out = Ichimoku()
    if n == 0:
        return out
    i = n - 1
    out.tenkan = _mid(highs, lows, i, conv)
    out.kijun = _mid(highs, lows, i, base)
    if out.tenkan is not None and out.kijun is not None:
        out.span_a_future = (out.tenkan + out.kijun) / 2
    out.span_b_future = _mid(highs, lows, i, span_b)

    # 오늘 위치에 그려진 구름 = shift 봉 전 시점에서 계산된 값 (미래 데이터 미사용)
    j = i - shift
    if j >= 0:
        t = _mid(highs, lows, j, conv)
        k = _mid(highs, lows, j, base)
        if t is not None and k is not None:
            out.span_a_now = (t + k) / 2
        out.span_b_now = _mid(highs, lows, j, span_b)
        out.chikou_vs_price = closes[i] - closes[j]
    if out.span_a_now is not None and out.span_b_now is not None:
        out.cloud_top = max(out.span_a_now, out.span_b_now)
        out.cloud_bottom = min(out.span_a_now, out.span_b_now)
    return out


@dataclass
class IndicatorSet:
    last_close: float | None = None
    sma5: float | None = None
    sma20: float | None = None
    sma60: float | None = None
    sma120: float | None = None
    rsi14: float | None = None
    rsi14_prev: float | None = None   # 전일 마감 기준 RSI (반등/꺾임 판단용)
    sma20_prev5: float | None = None  # 5봉 전 SMA20 (20일선 기울기 판단용)
    ichimoku: Ichimoku = field(default_factory=Ichimoku)
    bars: int = 0


def compute_all(highs: list[float], lows: list[float], closes: list[float]) -> IndicatorSet:
    return IndicatorSet(
        last_close=closes[-1] if closes else None,
        sma5=sma(closes, 5),
        sma20=sma(closes, 20),
        sma60=sma(closes, 60),
        sma120=sma(closes, 120),
        rsi14=rsi(closes, 14),
        rsi14_prev=rsi(closes[:-1], 14),
        sma20_prev5=sma(closes[:-5], 20),
        ichimoku=ichimoku(highs, lows, closes),
        bars=len(closes),
    )
