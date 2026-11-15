"""R. 투매 반등 — 시장 전체가 급락할 때 함께 과매도된 종목을 지정가로 받아 반등을 먹는다(3~10일).

2019~2026 KRX 일봉 검증 결과
- 시장은 멀쩡한데 혼자 급락한 종목(개별 악재)은 이후에도 평균적으로 더 빠졌다 → 사지 않는다.
- 지수가 급락하는 구간에서 같이 투매된 종목은 5~10일 뒤 평균 +3~10% 반등했다 → 이때만 산다.
'하락장에서 저점 매수'를 담당한다. 신호가 1년에 몇 차례만 몰려서 나온다.
"""
from dataclasses import dataclass

from ..indicators import rsi
from .base import Strategy, tradable


@dataclass
class Reversal(Strategy):
    name: str = "reversal"
    label: str = "투매 반등"
    drop5: float = -0.15         # 5거래일 수익률 상한
    rsi_max: float = 30.0        # RSI(14) 상한
    crash_chg: float = -0.08     # 당일 등락률이 이보다 낮고
    crash_vol: float = 3.0       # 거래량이 평균의 이 배수 이상이면 제외(악재성 폭락)
    mkt_ret5: float = -0.04      # 지수 5일 수익률이 이 이하이거나
    mkt_dd20: float = -0.07      # 지수가 20일 고점 대비 이만큼 빠졌을 때만 매수
    confirm: str = ""            # "stock_up": 신호일 종목 양봉 / "mkt_up": 신호일 지수 상승 (반등 확인 후 진입)
    exit_rule: str = ""          # "ma5": 종가가 5일선 위로 올라오면 청산
    rank_by: str = "liquid"       # drop: 낙폭 큰 순 / liquid: 거래대금 큰 순
    min_val20: float = 5e9
    max_positions: int = 4
    pos_weight: float = 0.20
    ladder_ref: str = "close"
    ladder_ticks: tuple = (-3, -6, -10)
    gap_min: float = -0.10
    gap_max: float = 0.03
    stop_pct: float = 0.15
    tp_pct: float = 0.12
    max_hold: int = 10

    def prepare(self, f, m=None):
        c = f["close"]
        ret5 = c / c.shift(5) - 1
        r14 = rsi(c, 14)
        crash = (f["chg"] <= self.crash_chg) & (f["vol_ratio"] >= self.crash_vol)
        panic = (m["ret5"] <= self.mkt_ret5) | (m["dd20"] <= self.mkt_dd20) if m is not None else True
        sig = (ret5 <= self.drop5) & (r14 < self.rsi_max) & ~crash & panic \
            & (f["val_ma20"] >= self.min_val20) & tradable(f)
        if self.confirm == "stock_up":
            sig &= c > f["open"]
        elif self.confirm == "mkt_up" and m is not None:
            sig &= m["close"] > m["close"].shift(1)
        ex = (c > f["ma5"]) if self.exit_rule == "ma5" else (c < 0)
        return sig, (-ret5 if self.rank_by == "drop" else f["val_ma20"]), ex
