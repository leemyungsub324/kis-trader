"""C. 눌림목 역추세 — 장기 상승 추세 종목의 단기 과매도(RSI2) 매수, 2~7일 보유.

래리 코너스 RSI(2) 규칙을 국내 일봉에 맞게 옮겼다. '하락 중 저점 매수'를 담당한다.
"""
from dataclasses import dataclass

from .base import Strategy, tradable


@dataclass
class Pullback(Strategy):
    name: str = "pullback"
    label: str = "눌림목 역추세"
    rsi_max: float = 10.0
    min_val20: float = 5e9
    max_positions: int = 3
    pos_weight: float = 0.10
    ladder_ref: str = "close"
    ladder_ticks: tuple = (-3, -6, -10)
    gap_min: float = -0.05
    gap_max: float = 0.03
    stop_pct: float = 0.08
    max_hold: int = 7

    def prepare(self, f, m=None):
        sig = (
            (f["close"] > f["ma120"]) & (f["ma20"] > f["ma60"])
            & (f["rsi2"] <= self.rsi_max)
            & (f["val_ma20"] >= self.min_val20)
            & tradable(f)
        )
        return sig, -f["rsi2"], f["close"] > f["ma5"]
