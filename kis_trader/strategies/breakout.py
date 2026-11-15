"""B. 추세 돌파 스윙 — 정배열 + 20일 신고가 + 거래량 증가, 1~4주 보유.

터틀(돈치안 채널 돌파)과 미너비니 추세 템플릿의 축약판이다.
"""
from dataclasses import dataclass

from .base import Strategy, tradable


@dataclass
class Breakout(Strategy):
    name: str = "breakout"
    label: str = "추세 돌파 스윙"
    vol_mult: float = 2.0
    chg_max: float = 0.15
    min_val20: float = 3e9       # 20일 평균 거래대금 30억 이상
    max_positions: int = 4
    pos_weight: float = 0.125
    regimes: tuple = ("BULL", "NEUTRAL")
    ladder_ref: str = "close"
    ladder_ticks: tuple = (-1, -3, -5)
    gap_min: float = -0.03
    gap_max: float = 0.04
    stop_pct: float = 0.10
    stop_atr: float = 2.0
    tp_pct: float = 0.15
    tp_frac: float = 0.5
    trail_atr: float = 3.0
    max_hold: int = 20

    def prepare(self, f, m=None):
        sig = (
            (f["close"] > f["hh20"])
            & (f["vol_ratio"] >= self.vol_mult)
            & (f["close"] > f["ma20"]) & (f["ma20"] > f["ma60"])
            & (f["ma60"] > f["ma60"].shift(10))
            & (f["chg"] <= self.chg_max)
            & (f["val_ma20"] >= self.min_val20)
            & tradable(f)
        )
        return sig, f["ret60"], f["close"] < f["ma20"]
