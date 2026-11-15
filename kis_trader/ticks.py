"""호가단위(틱) 계산. 2023-01-25 이후 코스피·코스닥 공통 호가단위 기준."""
from __future__ import annotations

_BANDS = (  # (가격 상한 미만, 호가단위)
    (2_000, 1), (5_000, 5), (20_000, 10), (50_000, 50),
    (200_000, 100), (500_000, 500), (float("inf"), 1_000),
)


def tick_size(price: float, etf: bool = False) -> int:
    """해당 가격대의 호가단위. ETF/ETN은 2,000원 미만 1원, 이상 5원."""
    if etf:
        return 1 if price < 2_000 else 5
    for upper, tick in _BANDS:
        if price < upper:
            return tick
    return 1_000


def round_down(price: float, etf: bool = False) -> int:
    t = tick_size(price, etf)
    return int(price // t * t)


def round_up(price: float, etf: bool = False) -> int:
    p = round_down(price, etf)
    return p if p >= price else p + tick_size(p, etf)


def shift(price: float, n: int, etf: bool = False) -> int:
    """price에서 n틱 이동한 호가(음수면 아래). 가격대 경계를 넘으면 틱 크기가 바뀐다."""
    p = round_down(price, etf) if n <= 0 else round_up(price, etf)
    for _ in range(abs(n)):
        if n > 0:
            p += tick_size(p, etf)
        else:
            p -= tick_size(p - 1, etf)  # 경계 바로 아래는 아래 구간 틱 적용
            if p <= 0:
                return tick_size(1, etf)
    return int(p)
