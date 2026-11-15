"""D. 변동성 돌파 — 당일 시가 + k×전일 변동폭 돌파 시 매수, 다음날 시가 청산.

CoBot 글의 하락장 규칙(래리 윌리엄스)이다. 거래대금 상위 대형주만 대상으로 한다.
"""
from dataclasses import dataclass

from .base import Strategy, tradable


@dataclass
class VolBreakout(Strategy):
    name: str = "vb"
    label: str = "변동성 돌파"
    min_val20: float = 3e10      # 20일 평균 거래대금 300억 이상
    max_positions: int = 3
    pos_weight: float = 0.10
    regimes: tuple = ("NEUTRAL", "BEAR")
    entry: str = "vb"
    vb_k: float = 0.5
    stop_pct: float = None
    max_hold: int = 1
    exit_next_open: bool = True

    def prepare(self, f, m=None):
        sig = (f["val_ma20"] >= self.min_val20) & (f["close"] > f["ma5"]) & tradable(f)
        return sig, f["val_ma20"], f["close"] < 0
