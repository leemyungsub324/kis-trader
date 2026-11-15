"""KRX 호가가격단위 (2023-01-25 개편, 유가증권·코스닥 공통. ETF/ETN 은 별도 규칙이라 대상 아님)."""
from __future__ import annotations

_TABLE = ((2_000, 1), (5_000, 5), (20_000, 10), (50_000, 50), (200_000, 100), (500_000, 500))


def tick_size(price: float) -> int:
    for limit, tick in _TABLE:
        if price < limit:
            return tick
    return 1_000


def floor_to_tick(price: float) -> int:
    t = tick_size(price)
    return int(price // t) * t


def ticks_below(price: float, n: int) -> int:
    """price 에서 n 호가 아래 가격. 가격대 경계를 넘으면 그 구간의 호가단위를 따른다."""
    p = floor_to_tick(price)
    for _ in range(max(0, n)):
        p -= tick_size(p - 1)  # 한 칸 아래 가격이 속한 구간의 단위
        if p <= 0:
            return 0
    return p


def offset_ticks_for(price: float, atr: float | None, atr_mult: float, lo: int, hi: int) -> int:
    """변동성(ATR)에 비례한 매수 호가 간격. lo~hi 틱으로 제한. ATR 이 없으면 lo."""
    if not atr or atr <= 0 or price <= 0:
        return lo
    n = round(atr * atr_mult / tick_size(price))
    return max(lo, min(hi, n))
