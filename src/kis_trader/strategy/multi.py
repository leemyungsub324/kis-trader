"""multi 전략의 매매 규칙 (순수 함수). 차트 데이터(가격·거래량)만 사용한다.

카테고리 4개가 예산을 나눠 쓰고, 각자 진입·청산 규칙이 다르다. 엔진은 multi_bot.py.
────────────────────────────────────────────────────────────────────────────────────
장세 판단 (하루 1회, 전일 종가 기준)
  지수 대용 KODEX 200(069500) 과 시장 폭(스캔 종목 중 종가 > 20일선 비율)으로 정한다.
    bull    : 지수 종가 > SMA20 > SMA60  그리고  폭 >= 50%
    bear    : 지수 종가 < SMA60          그리고  폭 <  40%
    neutral : 그 외
  bear 에서는 swing 신규 진입 중단, surge·vbreak 주문 금액 절반.

1) surge  급등주 단타 (wikidocs '급등주 포착' = 당일 거래량 > 20일 평균 × 10 을 장중용으로 변형)
   진입(09:05~14:30, 모두 충족)
     - 당일 누적 거래량 >= 20일 평균 거래량 × 2   그리고   하루 환산(경과 시간 보정) >= × 5
     - 전일 대비 +4% ~ +18%, 현재가 > 시가(양봉), 현재가 >= 당일 고가 × 0.98
     - 당일 거래대금 >= 30억, 상한가의 93% 미만
   청산: -3% 손절 / +5% 에 절반 / 고점 +3% 이상 찍은 뒤 고점 대비 -2.5% 추적 청산 / 15:15 전량 (오버나이트 없음)

2) vbreak  변동성 돌파 (CoBot 의 하락장 분기, Larry Williams)
   목표가 = 당일 시가 + k × (전일 고가 - 전일 저가),  k = 20일 평균 노이즈 비율(0.4~0.7 로 제한)
   후보: 전일 종가 > SMA5 · SMA20, ATR >= 2%, 거래대금 상위
   진입(09:05~14:00): 목표가 <= 현재가 <= 목표가 × 1.015, 시가 갭 +5% 이하, 전일 변동폭 >= 1.5%
   청산: -3% 손절 / 다음 거래일 첫 주기(시가 부근) 전량. bear 장세면 당일 15:15 전량

3) swing  추세 눌림목 스윙 (CoBot 의 상승장 조건 + 상대강도)
   후보(전일 종가 기준, 모두 충족)
     - MACD > Signal, MACD 상승 중, 종가 > SMA20 > SMA60, SMA20 상승
       (CoBot 의 EMA100 은 일봉 100개 제한 때문에 SMA60 으로 대체)
     - RSI14 < 75, 종가 <= SMA20 × 1.12 (과열·추격 제외), 60일 수익률이 유니버스 상위 30%
   진입: 09:10 이후 현재가 - N틱 지정가 (N = 0.3×ATR, 3~10틱). 전일 대비 ±3% 넘게 벌어지면 그날은 건너뜀
   청산: 손절(2×ATR%, 4%~STOP_LOSS_PCT) / +10% 절반 / +6% 이상 찍은 뒤 고점 - 2.5×ATR 추적
        / 전일 종가 < SMA20 / MACD < Signal / 보유 30일 경과

4) dip  저점 매수 (Connors RSI(2) 평균회귀)
   후보 A 눌림: 종가 > SMA60, RSI2 <= 10, 종가 < SMA5                    (모든 장세)
   후보 B 투매: RSI14 <= 30, 종가 <= 볼린저 하단, 5일 수익률 <= -8%      (bull 제외, 거래대금 100억 이상)
   진입: 09:10 이후 min(현재가, 전일 종가) - N틱 지정가 (N = 0.5×ATR, 3~10틱)
   청산: 손절(STOP_LOSS_PCT) / +6% 익절 / 현재가 > SMA5 이고 수익 중 / 보유 7일 경과
────────────────────────────────────────────────────────────────────────────────────
모든 매수는 지정가(안 닿으면 취소), 모든 매도는 시장가다. 수치는 문헌·관행에서 가져온 시작값이며
이 프로젝트에서 백테스트로 검증된 값이 아니다. 모의투자 결과로 조정할 것.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time

from ..features import Features
from ..kis.market import Quote
from ..ticks import offset_ticks_for, ticks_below

SLEEVES = ("surge", "vbreak", "swing", "dip")
SESSION_MIN = 390          # 09:00~15:30
MIN_ELAPSED_MIN = 30       # 장 초반 거래량 환산 과대평가 방지
INDEX_PROXY = "069500"     # KODEX 200


@dataclass
class Params:
    stop_cap_pct: float = 5.0          # STOP_LOSS_PCT (전역 손절이 먼저 걸리므로 카테고리 손절의 상한)
    tick_min: int = 3
    tick_max: int = 10
    breakout_ticks: int = 3            # surge·vbreak 매수 호가: 현재가 - N틱
    min_turnover: float = 3e9          # 후보 최소 20일 평균 거래대금
    # surge
    surge_vol_now: float = 2.0
    surge_vol_proj: float = 5.0
    surge_chg_min: float = 4.0
    surge_chg_max: float = 18.0
    surge_min_turnover: float = 3e9
    surge_stop: float = 3.0
    surge_half_at: float = 5.0
    surge_trail_arm: float = 3.0
    surge_trail: float = 2.5
    surge_watch: int = 15
    # vbreak
    vb_k_min: float = 0.4
    vb_k_max: float = 0.7
    vb_chase: float = 1.5
    vb_stop: float = 3.0
    vb_watch: int = 8
    # swing
    swing_rs_pct: float = 70.0
    swing_max_gap: float = 3.0
    swing_half_at: float = 10.0
    swing_trail_arm: float = 6.0
    swing_trail_atr: float = 2.5
    swing_max_days: int = 30
    swing_picks: int = 6
    # dip
    dip_rsi2: float = 10.0
    dip_take: float = 6.0
    dip_max_days: int = 7
    dip_picks: int = 4
    # 시간
    entry_from: time = time(9, 5)
    rest_from: time = time(9, 10)
    surge_until: time = time(14, 30)
    vb_until: time = time(14, 0)
    rest_until: time = time(14, 30)
    day_close: time = time(15, 15)


@dataclass
class Plan:
    """하루치 스캔 결과에서 뽑은 감시·주문 후보."""
    regime: str = "neutral"
    breadth: float = 0.0
    surge: list[str] = field(default_factory=list)
    vbreak: list[str] = field(default_factory=list)
    swing: list[str] = field(default_factory=list)
    dip: list[str] = field(default_factory=list)


# ── 장세 ──
def market_breadth(feats: dict[str, Features]) -> float:
    if not feats:
        return 0.0
    return sum(1 for f in feats.values() if f.close > f.sma20) / len(feats) * 100


def regime_of(index: Features | None, breadth: float) -> str:
    if index is None:
        return "neutral"
    if index.close > index.sma20 > index.sma60 and breadth >= 50:
        return "bull"
    if index.close < index.sma60 and breadth < 40:
        return "bear"
    return "neutral"


# ── 후보 선정 (전일 종가 기준) ──
def swing_ok(f: Features, rs_cut: float) -> bool:
    return (f.macd > f.macd_signal and f.macd > f.macd_prev
            and f.close > f.sma20 > f.sma60 and f.sma20 > f.sma20_prev5
            and f.rsi14 < 75 and f.close <= f.sma20 * 1.12 and f.ret60 >= rs_cut)


def dip_kind(f: Features, regime: str, p: Params) -> str | None:
    if f.close > f.sma60 and f.rsi2 <= p.dip_rsi2 and f.close < f.sma5:
        return "눌림"
    if regime != "bull" and f.rsi14 <= 30 and f.close <= f.bb_lower and f.ret5 <= -8 and f.turnover_avg20 >= 1e10:
        return "투매"
    return None


def build_plan(feats: dict[str, Features], index: Features | None, p: Params) -> Plan:
    pool = {s: f for s, f in feats.items() if s != INDEX_PROXY and f.turnover_avg20 >= p.min_turnover}
    breadth = market_breadth(pool)
    plan = Plan(regime=regime_of(index, breadth), breadth=breadth)
    if not pool:
        return plan

    hot = [f for f in pool.values()
           if (f.prev_vol_ratio >= 2 and f.close > f.prev_open) or f.close >= f.high60 * 0.97]
    hot.sort(key=lambda f: max(f.prev_vol_ratio, 1.0) * f.atr_pct, reverse=True)
    plan.surge = [f.symbol for f in hot[: p.surge_watch]]

    vb = [f for f in pool.values() if f.close > f.sma5 and f.close > f.sma20 and f.atr_pct >= 2
          and f.prev_range / f.close * 100 >= 1.5]
    vb.sort(key=lambda f: f.turnover_avg20, reverse=True)
    plan.vbreak = [f.symbol for f in vb[: p.vb_watch]]

    if plan.regime != "bear":
        rets = sorted(f.ret60 for f in pool.values())
        rs_cut = rets[min(len(rets) - 1, int(len(rets) * p.swing_rs_pct / 100))]
        sw = [f for f in pool.values() if swing_ok(f, rs_cut)]
        sw.sort(key=lambda f: f.ret60, reverse=True)
        plan.swing = [f.symbol for f in sw[: p.swing_picks]]

    dp = [f for f in pool.values() if dip_kind(f, plan.regime, p)]
    dp.sort(key=lambda f: f.rsi2)
    plan.dip = [f.symbol for f in dp[: p.dip_picks]]
    return plan


# ── 장중 진입 신호 ──
def session_fraction(now: time) -> float:
    elapsed = (now.hour - 9) * 60 + now.minute
    return max(MIN_ELAPSED_MIN, min(SESSION_MIN, elapsed)) / SESSION_MIN


def volume_ratios(q: Quote, f: Features, now: time) -> tuple[float, float]:
    """(현재까지 거래량 / 20일 평균, 하루 환산 배수)."""
    if f.vol_avg20 <= 0:
        return 0.0, 0.0
    r = q.volume / f.vol_avg20
    return r, r / session_fraction(now)


def surge_signal(q: Quote, f: Features, now: time, p: Params) -> str | None:
    if not (p.entry_from <= now < p.surge_until) or q.open <= 0:
        return None
    r, proj = volume_ratios(q, f, now)
    if r < p.surge_vol_now or proj < p.surge_vol_proj:
        return None
    if not (p.surge_chg_min <= q.change_pct <= p.surge_chg_max):
        return None
    if q.price <= q.open or q.price < q.high * 0.98 or q.turnover < p.surge_min_turnover:
        return None
    if q.upper_limit > 0 and q.price >= q.upper_limit * 0.93:
        return None
    return f"급등 거래량 {r:.1f}배(환산 {proj:.1f}배) 등락 {q.change_pct:+.1f}%"


def surge_warming(q: Quote, f: Features, now: time) -> bool:
    """순회 조회에서 감시 목록으로 올릴 만한 종목 (아직 신호 전)."""
    r, _ = volume_ratios(q, f, now)
    return q.change_pct >= 3.0 and r >= 1.0 and q.price > q.open > 0


def vbreak_target(q: Quote, f: Features, p: Params) -> float:
    k = max(p.vb_k_min, min(p.vb_k_max, f.noise20))
    return q.open + k * f.prev_range


def vbreak_signal(q: Quote, f: Features, now: time, p: Params) -> str | None:
    if not (p.entry_from <= now < p.vb_until) or q.open <= 0 or f.close <= 0:
        return None
    if q.open > f.close * 1.05:
        return None
    target = vbreak_target(q, f, p)
    if not (target <= q.price <= target * (1 + p.vb_chase / 100)):
        return None
    return f"변동성 돌파 목표가 {target:,.0f} (k={max(p.vb_k_min, min(p.vb_k_max, f.noise20)):.2f})"


def breakout_limit(q: Quote, p: Params) -> int:
    return ticks_below(q.price, p.breakout_ticks)


def resting_limit(sleeve: str, q: Quote, f: Features, p: Params) -> int | None:
    """swing/dip 의 대기 지정가. 조건이 안 맞으면 None (그날은 건너뜀)."""
    if q.prev_close <= 0:
        return None
    if sleeve == "swing":
        if abs(q.change_pct) > p.swing_max_gap:
            return None
        n = offset_ticks_for(q.price, f.atr14, 0.3, p.tick_min, p.tick_max)
        return ticks_below(q.price, n)
    base = min(q.price, q.prev_close)
    n = offset_ticks_for(base, f.atr14, 0.5, p.tick_min, p.tick_max)
    return ticks_below(base, n)


def stop_pct_for(sleeve: str, f: Features | None, p: Params) -> float:
    if sleeve == "surge":
        s = p.surge_stop
    elif sleeve == "vbreak":
        s = p.vb_stop
    elif sleeve == "swing":
        s = max(4.0, 2 * f.atr_pct) if f else p.stop_cap_pct
    else:
        s = p.stop_cap_pct
    return min(s, p.stop_cap_pct)


# ── 청산 ──
@dataclass
class Lot:
    symbol: str
    sleeve: str
    opened: datetime
    stop_pct: float
    atr: float
    high: float
    half_done: bool


def exit_decision(lot: Lot, avg_price: float, price: float, qty: int, f: Features | None,
                  now: datetime, regime: str, p: Params) -> tuple[str, float] | None:
    """(사유, 매도 비율) 또는 None. 위에서부터 먼저 걸리는 것 하나."""
    if avg_price <= 0 or price <= 0:
        return None
    pnl = (price / avg_price - 1) * 100
    high = max(lot.high, price)
    high_pnl = (high / avg_price - 1) * 100
    held_days = (now.date() - lot.opened.date()).days
    t = now.time()
    can_half = not lot.half_done and qty >= 2

    if pnl <= -lot.stop_pct:
        return f"손절 {pnl:.1f}% <= -{lot.stop_pct:.1f}%", 1.0

    if lot.sleeve == "surge":
        if t >= p.day_close or held_days >= 1:
            return f"급등 단타 당일 청산 ({pnl:+.1f}%)", 1.0
        if high_pnl >= p.surge_trail_arm and price <= high * (1 - p.surge_trail / 100):
            return f"추적 청산: 고점 {high:,.0f} 대비 -{p.surge_trail}% ({pnl:+.1f}%)", 1.0
        if pnl >= p.surge_half_at and can_half:
            return f"1차 익절 {pnl:+.1f}%", 0.5

    elif lot.sleeve == "vbreak":
        if held_days >= 1:
            return f"변동성 돌파 익일 청산 ({pnl:+.1f}%)", 1.0
        if regime == "bear" and t >= p.day_close:
            return f"하락장 당일 청산 ({pnl:+.1f}%)", 1.0

    elif lot.sleeve == "swing":
        if pnl >= p.swing_half_at and can_half:
            return f"1차 익절 {pnl:+.1f}%", 0.5
        if lot.atr > 0 and high_pnl >= p.swing_trail_arm and price <= high - p.swing_trail_atr * lot.atr:
            return f"추적 청산: 고점 {high:,.0f} - {p.swing_trail_atr}×ATR ({pnl:+.1f}%)", 1.0
        if f is not None and held_days >= 1:
            if f.close < f.sma20:
                return f"추세 이탈: 전일 종가 < 20일선 ({pnl:+.1f}%)", 1.0
            if f.macd < f.macd_signal:
                return f"MACD 데드크로스 ({pnl:+.1f}%)", 1.0
        if held_days >= p.swing_max_days:
            return f"보유 {held_days}일 경과 ({pnl:+.1f}%)", 1.0

    elif lot.sleeve == "dip":
        if pnl >= p.dip_take:
            return f"반등 익절 {pnl:+.1f}%", 1.0
        if f is not None and price > f.sma5 and pnl > 0:
            return f"5일선 회복 ({pnl:+.1f}%)", 1.0
        if held_days >= p.dip_max_days:
            return f"보유 {held_days}일 경과 ({pnl:+.1f}%)", 1.0
    return None
