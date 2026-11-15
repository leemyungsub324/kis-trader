"""전략 공통 정의. 신호는 '당일 종가 확정 후' 계산하고 진입은 다음 거래일에 한다."""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd


@dataclass
class Strategy:
    name: str = "base"
    label: str = ""
    max_positions: int = 3
    pos_weight: float = 0.10            # 종목당 계좌 자산 대비 비중
    regimes: tuple = ("BULL", "NEUTRAL", "BEAR")
    regime_scale: dict = field(default_factory=dict)   # 국면별 비중 배수
    # --- 진입 ---
    entry: str = "ladder"               # ladder(지정가 분할) | open(시가) | close(종가) | vb(변동성 돌파)
    ladder_ref: str = "close"           # close: 전일 종가 기준 / open: 당일 시가(현재가) 기준
    ladder_ticks: tuple = (-3, -6, -10)
    ladder_weights: tuple = (0.4, 0.3, 0.3)
    gap_min: float = -0.05              # 시가 갭이 이 범위를 벗어나면 그날 진입 포기
    gap_max: float = 0.05
    ladder_atr: tuple | None = None     # 지정 시 틱 대신 ATR 배수로 주문가 계산 (예: (-0.3, -0.6, -1.0))
    max_new_per_day: int = 99           # 하루 신규 진입 종목 수 상한(진입 시점 분산)
    vol_target: float | None = None     # 지정 시 종목 일변동성(ATR/종가)에 반비례해 비중 조절
    vb_k: float = 0.5
    # --- 청산 ---
    stop_pct: float | None = 0.05       # 평단 대비 손절폭
    stop_atr: float | None = None       # ATR 배수 손절(지정 시 stop_pct는 상한으로 사용)
    tp_pct: float | None = None         # 익절 지정가
    tp_frac: float = 1.0                # 익절 시 매도 비율
    trail_atr: float | None = None      # 고점 대비 ATR 배수 추적손절
    max_hold: int = 5                   # 최대 보유 거래일
    exit_next_open: bool = False        # 다음날 시가 청산(변동성 돌파)

    def prepare(self, f: pd.DataFrame, m: pd.DataFrame | None = None):
        """f=add_features 결과, m=market_features 결과(f와 같은 날짜 인덱스).
        (신호 bool, 점수, 종가기준 청산 bool) Series 반환."""
        raise NotImplementedError

    def quick(self, q: dict) -> bool:
        """장 마감 스캔에서 현재가 한 건(q)만으로 탈락시킬 수 있으면 False. API 호출·계산을 줄인다."""
        return True

    def weight(self, close: float, atr: float) -> float:
        """종목당 비중. vol_target이 있으면 변동성 큰 종목은 덜, 작은 종목은 더(최대 1.5배) 산다."""
        if self.vol_target and atr == atr and atr > 0 and close > 0:
            return self.pos_weight * min(1.5, self.vol_target / (atr / close))
        return self.pos_weight

    def ladder_prices(self, ref: float, atr: float) -> list:
        from ..ticks import shift
        if self.ladder_atr and atr == atr and atr > 0:
            return [shift(ref + k * atr, 0) for k in self.ladder_atr]
        return [shift(ref, n) for n in self.ladder_ticks]

    def stop_price(self, entry: float, atr: float) -> float:
        if self.stop_atr and atr == atr and atr > 0:
            s = entry - self.stop_atr * atr
            if self.stop_pct:
                s = max(s, entry * (1 - self.stop_pct))
            return s
        return entry * (1 - (self.stop_pct or 0.99))


def tradable(f: pd.DataFrame, min_price: float = 1000) -> pd.Series:
    ok = f["close"] >= min_price
    if "admin" in f:
        ok &= ~f["admin"].astype(bool)
    return ok
