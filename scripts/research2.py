"""낙폭과대 반등·종가베팅 변형 비교 (IS 2019-07~2023 / OOS 2024~2026-11)."""
import sys, time
sys.path.insert(0, '.')
import pandas as pd
from dataclasses import replace
from kis_trader.backtest import Panel, run_panel, Costs
from kis_trader.strategies import Reversal, Surge
from scripts.research import load
pd.set_option('display.width', 250)
markets = tuple(sys.argv[1].split(',')) if len(sys.argv) > 1 else ('KOSPI',)
data, meta, idx = load(markets)
PER = (('IS', '2019-07-01', '2023-12-31'), ('OOS', '2024-01-01', '2026-11-12'))

def line(name, strat):
    panel = Panel(data, [strat], idx)
    out = []
    for lab, a, b in PER:
        r = run_panel(panel, [strat], start=a, end=b)
        s = r.summary(); t = r.by_strategy()
        if t.empty: out.append(f"{lab} 거래없음"); continue
        t = t.iloc[0]
        out.append(f"{lab} 연{s['연환산']*100:6.1f}% MDD{s['MDD']*100:6.1f}% 월중앙{s['월중앙값']*100:5.2f}% 수익월{s['수익월비율']*100:3.0f}% n={int(t['거래수']):4d} 승{t['승률']*100:3.0f}% 평균{t['평균수익']*100:5.2f}% PF{t['손익비PF']:.2f} 체결{t['체결률']*100:3.0f}%")
    print(f"{name:40s} " + ' || '.join(out), flush=True)

base = Reversal()
which = sys.argv[2] if len(sys.argv) > 2 else 'rev'
if which == 'rev':
    line('기본(패닉필터, ladder -3/-6/-10, tp12 sl15 10d)', base)
    line('패닉필터 끔', replace(base, mkt_ret5=1, mkt_dd20=1))
    line('패닉: 지수5일 -4%만', replace(base, mkt_dd20=-1))
    line('패닉: 지수5일 -6% or dd -10%', replace(base, mkt_ret5=-0.06, mkt_dd20=-0.10))
    line('정렬: 거래대금 큰 순', replace(base, rank_by='liquid'))
    line('진입: 시가 매수', replace(base, entry='open'))
    line('진입: ladder -10/-20/-30틱', replace(base, ladder_ticks=(-10, -20, -30)))
    line('갭 허용 넓힘(-15~+8%)', replace(base, gap_min=-0.15, gap_max=0.08))
    line('익절 없음', replace(base, tp_pct=None))
    line('익절 8%', replace(base, tp_pct=0.08))
    line('익절 20%', replace(base, tp_pct=0.20))
    line('손절 8%', replace(base, stop_pct=0.08))
    line('손절 없음', replace(base, stop_pct=0.99))
    line('보유 5일', replace(base, max_hold=5))
    line('보유 20일', replace(base, max_hold=20))
    line('RSI 조건 없음', replace(base, rsi_max=101))
    line('drop5 -10%', replace(base, drop5=-0.10))
    line('drop5 -20%', replace(base, drop5=-0.20))
    line('6종목×15%', replace(base, max_positions=6, pos_weight=0.15))
    line('8종목×12%', replace(base, max_positions=8, pos_weight=0.12))
else:
    s = Surge()
    line('종가베팅 기본(vol3 chg7-20 clv.8 BULL/NEU)', s)
    line('전 국면', replace(s, regimes=('BULL', 'NEUTRAL', 'BEAR')))
    line('BULL만', replace(s, regimes=('BULL',)))
    line('clv>=0.9', replace(s, clv_min=0.9))
    line('vol>=5', replace(s, vol_mult=5))
    line('vol>=10 (wikidocs 원본)', replace(s, vol_mult=10))
    line('chg 10~20', replace(s, chg_min=0.10))
    line('chg 5~15', replace(s, chg_min=0.05, chg_max=0.15))
    line('거래대금 300억↑', replace(s, min_value=3e10))
    line('5종목×10%', replace(s, max_positions=5))
