"""종합 점수 전략: 이동평균 + 일목균형표 + RSI 를 0~100점으로 수치화해 매수/매도.

모든 값은 '마감된 일봉' 기준이다(main.indicators_for 가 장중 미완성 봉을 제외).
따라서 신호는 하루에 한 번 바뀌며, 종목·방향별로 하루 1회만 주문 의도를 낸다.

점수표 (합계 100)
────────────────────────────────────────────────────────────────────────────
A. 이동평균 추세 (35)
   종가 > SMA20 ............................................... 8
   종가 > SMA60 ............................................... 7
   정배열 SMA5 > SMA20 > SMA60 ................................ 10  (SMA20 > SMA60 만이면 5)
   SMA20 상승 (오늘 SMA20 > 5봉 전 SMA20) ...................... 5
   눌림 위치: 종가가 SMA20 대비 -2% ~ +5% ...................... 5  (추격매수 억제)
B. 일목균형표 (40)
   종가 > 구름 상단 ........................................... 12  (구름 안이면 4)
   전환선 > 기준선 ............................................. 8  (같으면 4)
   후행스팬 > 26봉 전 종가 ...................................... 8
   종가 >= 기준선 ............................................... 6
   미래 구름 양운 (선행스팬1 > 선행스팬2, 26봉 뒤) ................ 6
C. RSI(14, Wilder) (25)  — Cardwell/Brown 의 상승장 RSI 범위(40~80) 기준
   40 ~ 60 (상승장 눌림 구간) ................................... 15
   60 ~ 70 ..................................................... 10
   35 ~ 40 ...................................................... 6
   70 ~ 75 ...................................................... 3
   그 외(35 미만, 75 이상) ....................................... 0
   RSI 반등 (오늘 RSI > 전일 RSI) ................................ 10
────────────────────────────────────────────────────────────────────────────

매수 (모두 충족)
  1) 점수 >= buy_score (기본 70)
  2) 필수 필터: 종가 > 구름 상단, 종가 > SMA60, RSI < 75 (점수가 높아도 하락추세·과열은 제외)
  3) 미보유, 오늘 같은 종목 매수 기록 없음, entry_after(기본 09:10) 이후
  수량: 점수 >= 85 면 예산 100%, 그 외 50%. 예산 = STRATEGY_BUY_KRW (0 이면 MAX_ORDER_AMOUNT_KRW)

매도 (보유 종목, 위에서부터 먼저 걸리는 것 하나)
  1) 추세 이탈(전량): 종가 < 구름 하단  또는  (전환선 < 기준선 이고 종가 < 기준선)
  2) 점수 붕괴(전량): 점수 < sell_score (기본 40)
  3) 과열 익절(절반): RSI >= 75 이고 수익률 >= +10%
  ※ 평균단가 대비 -STOP_LOSS_PCT 손절은 Guard 쪽에서 장중 매 주기 먼저 평가된다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, time

from ..indicators import IndicatorSet
from ..safety.models import OrderIntent
from .base import Snapshot, Strategy

log = logging.getLogger(__name__)

MIN_BARS = 78  # 일목 선행스팬2(52) + 이동(26)


@dataclass
class Score:
    total: int = 0
    ma: int = 0
    ichimoku: int = 0
    rsi: int = 0
    details: dict[str, int] = field(default_factory=dict)
    ready: bool = False  # 지표가 모두 계산됐는지


def _rsi_band(r: float) -> int:
    if 40 <= r < 60:
        return 15
    if 60 <= r < 70:
        return 10
    if 35 <= r < 40:
        return 6
    if 70 <= r < 75:
        return 3
    return 0


def score(ind: IndicatorSet) -> Score:
    ich = ind.ichimoku
    c = ind.last_close
    need = (c, ind.sma5, ind.sma20, ind.sma60, ind.sma20_prev5, ind.rsi14, ind.rsi14_prev,
            ich.tenkan, ich.kijun, ich.cloud_top, ich.cloud_bottom, ich.chikou_vs_price,
            ich.span_a_future, ich.span_b_future)
    if ind.bars < MIN_BARS or any(v is None for v in need):
        return Score()

    d: dict[str, int] = {}
    # A. 이동평균
    d["above_sma20"] = 8 if c > ind.sma20 else 0
    d["above_sma60"] = 7 if c > ind.sma60 else 0
    if ind.sma5 > ind.sma20 > ind.sma60:
        d["ma_aligned"] = 10
    elif ind.sma20 > ind.sma60:
        d["ma_aligned"] = 5
    else:
        d["ma_aligned"] = 0
    d["sma20_rising"] = 5 if ind.sma20 > ind.sma20_prev5 else 0
    dist = (c - ind.sma20) / ind.sma20 * 100
    d["pullback_zone"] = 5 if -2.0 <= dist <= 5.0 else 0
    ma = sum(d[k] for k in ("above_sma20", "above_sma60", "ma_aligned", "sma20_rising", "pullback_zone"))

    # B. 일목균형표
    if c > ich.cloud_top:
        d["above_cloud"] = 12
    elif c >= ich.cloud_bottom:
        d["above_cloud"] = 4
    else:
        d["above_cloud"] = 0
    if ich.tenkan > ich.kijun:
        d["tk_cross"] = 8
    elif ich.tenkan == ich.kijun:
        d["tk_cross"] = 4
    else:
        d["tk_cross"] = 0
    d["chikou"] = 8 if ich.chikou_vs_price > 0 else 0
    d["above_kijun"] = 6 if c >= ich.kijun else 0
    d["future_cloud"] = 6 if ich.span_a_future > ich.span_b_future else 0
    ic = sum(d[k] for k in ("above_cloud", "tk_cross", "chikou", "above_kijun", "future_cloud"))

    # C. RSI
    d["rsi_band"] = _rsi_band(ind.rsi14)
    d["rsi_rising"] = 10 if ind.rsi14 > ind.rsi14_prev else 0
    rs = d["rsi_band"] + d["rsi_rising"]

    return Score(total=ma + ic + rs, ma=ma, ichimoku=ic, rsi=rs, details=d, ready=True)


def buy_blockers(ind: IndicatorSet) -> list[str]:
    """매수 필수 필터에서 탈락한 사유 (비어 있으면 통과)."""
    ich = ind.ichimoku
    out = []
    if ind.last_close <= ich.cloud_top:
        out.append("종가<=구름상단" if ind.last_close >= ich.cloud_bottom else "종가<구름하단")
    if ind.last_close <= ind.sma60:
        out.append("종가<=SMA60")
    if ind.rsi14 >= 75:
        out.append("RSI>=75")
    return out


def buy_filters_ok(ind: IndicatorSet) -> bool:
    return not buy_blockers(ind)


def exit_reason(ind: IndicatorSet, sc: Score, pnl_pct: float, sell_score: int) -> tuple[str, float] | None:
    """(사유, 매도비율) 또는 None."""
    ich = ind.ichimoku
    c = ind.last_close
    if c < ich.cloud_bottom:
        return "추세이탈: 종가 < 구름 하단", 1.0
    if ich.tenkan < ich.kijun and c < ich.kijun:
        return "추세이탈: 전환선<기준선 & 종가<기준선", 1.0
    if sc.total < sell_score:
        return f"점수 붕괴 {sc.total} < {sell_score}", 1.0
    if ind.rsi14 >= 75 and pnl_pct >= 10.0:
        return f"과열 익절 RSI {ind.rsi14:.1f}, 수익 {pnl_pct:.1f}%", 0.5
    return None


class CompositeStrategy(Strategy):
    name = "composite"

    def __init__(self, buy_budget_krw: int, buy_score: int = 70, sell_score: int = 40,
                 entry_after: time = time(9, 10), entry_before: time | None = None):
        self.buy_budget_krw = buy_budget_krw
        self.entry_before = entry_before
        self.buy_score = buy_score
        self.sell_score = sell_score
        self.entry_after = entry_after
        self._sent: set[tuple[object, str, str]] = set()
        self._logged: set[tuple[object, str]] = set()
        self._blocked_logged: set[tuple[object, str]] = set()

    def period(self, snap: Snapshot) -> object:
        """신호가 바뀌는 단위. 일봉=날짜. (1시간봉 데모는 (날짜, 시) 로 바꾼다)"""
        return snap.now.date()

    def _once(self, today: object, sym: str, side: str, snap: Snapshot) -> bool:
        key = (today, sym, side)
        if key in self._sent or (sym, side) in snap.orders_today:
            return False
        self._sent.add(key)
        return True

    def on_tick(self, snap: Snapshot) -> list[OrderIntent]:
        today = self.period(snap)
        out: list[OrderIntent] = []
        for sym, price in snap.prices.items():
            ind = snap.indicators.get(sym)
            if ind is None:
                continue
            sc = score(ind)
            if (today, sym) not in self._logged:  # 점수는 기간(봉)마다 한 번만 바뀌므로 1회 기록
                self._logged.add((today, sym))
                log.info("종합점수", extra={
                    "event": "composite_score", "period": str(today), "symbol": sym, "ready": sc.ready, "bars": ind.bars,
                    "score": sc.total, "ma": sc.ma, "ichimoku": sc.ichimoku, "rsi": sc.rsi,
                    "details": sc.details, "rsi14": ind.rsi14, "close": ind.last_close,
                    "cloud_top": ind.ichimoku.cloud_top, "cloud_bottom": ind.ichimoku.cloud_bottom,
                    "sma20": ind.sma20, "sma60": ind.sma60,
                })
            if not sc.ready:
                continue

            pos = snap.positions.get(sym)
            if pos and pos.qty > 0:
                ex = exit_reason(ind, sc, pos.pnl_pct, self.sell_score)
                if ex and self._once(today, sym, "sell", snap):
                    why, ratio = ex
                    qty = pos.qty if ratio >= 1.0 else max(1, int(pos.qty * ratio))
                    out.append(OrderIntent(sym, "sell", qty, None, f"[{sc.total}점] {why}"))
                continue

            if snap.now.time() < self.entry_after:
                continue
            if self.entry_before is not None and snap.now.time() >= self.entry_before:
                continue
            blockers = buy_blockers(ind)
            if sc.total < self.buy_score or blockers:
                # 점수가 기준 근처인데 막힌 경우만 사유를 기록 (기간·종목당 1회). 사후 분석용.
                if sc.total >= self.buy_score - 5 and (today, sym) not in self._blocked_logged:
                    self._blocked_logged.add((today, sym))
                    reasons = (["점수<기준"] if sc.total < self.buy_score else []) + blockers
                    log.info("매수 보류", extra={
                        "event": "buy_blocked", "period": str(today), "symbol": sym, "score": sc.total,
                        "buy_score": self.buy_score, "reasons": reasons, "close": ind.last_close,
                        "cloud_top": ind.ichimoku.cloud_top, "sma60": ind.sma60, "rsi14": ind.rsi14})
                continue
            budget = self.buy_budget_krw if sc.total >= 85 else self.buy_budget_krw // 2
            budget = min(budget, snap.cash)
            qty = budget // price if price > 0 else 0
            if qty <= 0:
                continue
            if self._once(today, sym, "buy", snap):
                out.append(OrderIntent(
                    sym, "buy", qty, None,
                    f"[{sc.total}점 MA{sc.ma}/일목{sc.ichimoku}/RSI{sc.rsi}] 매수",
                ))
        return out
