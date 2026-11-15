"""S. 급등주 종가베팅 — 거래량 폭증 + 고가 부근 마감 종목을 장 마감 직전 매수, 다음날 시가 매도.

출발점은 wikidocs '급등주 포착'(당일 거래량 > 직전 20일 평균의 10배)이다.
2019~2026 검증에서 급등 '다음날' 추격 매수는 평균 손실이었고, 수익이 남는 구간은
'당일 종가 → 익일 시가'(밤사이 갭)뿐이었다. 그래서 보유 구간을 그 하룻밤으로 한정한다.
"""
from dataclasses import dataclass

from .base import Strategy, tradable


@dataclass
class Surge(Strategy):
    name: str = "surge"
    label: str = "급등주 종가베팅"
    vol_mult: float = 5.0        # 거래량 배수 (wikidocs 원본은 10 — 신호가 너무 드물어 5로 낮춤)
    chg_min: float = 0.07        # 당일 상승률 하한
    chg_max: float = 0.20        # 상한(20% 이상은 익일 급락 빈도가 높음)
    clv_min: float = 0.8         # 종가가 당일 범위 상단 20% 안
    min_value: float = 5e9       # 당일 거래대금 50억 이상
    max_positions: int = 3
    pos_weight: float = 0.15
    regimes: tuple = ("BULL", "NEUTRAL")
    entry: str = "close"         # 당일 종가 부근 매수
    stop_pct: float = None
    max_hold: int = 1
    exit_next_open: bool = True

    def quick(self, q):
        chg = q["price"] / q["prev"] - 1
        return self.chg_min <= chg <= self.chg_max and q["value"] >= self.min_value

    def prepare(self, f, m=None):
        sig = (
            (f["vol_ratio"] >= self.vol_mult)
            & (f["chg"] >= self.chg_min) & (f["chg"] <= self.chg_max)
            & (f["clv"] >= self.clv_min)
            & (f["value"] >= self.min_value)
            & (f["close"] > f["ma20"])
            & tradable(f)
        )
        return sig, f["clv"] + f["vol_ratio"] / 100, f["close"] < 0
