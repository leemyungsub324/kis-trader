"""개선안 비교: 기본값 대비 한 가지씩만 바꿔 IS/OOS 동시 확인."""
import sys; sys.path.insert(0, '.')
import pandas as pd
from dataclasses import replace
from kis_trader.backtest import Panel, run_panel
from kis_trader.strategies import Reversal, Surge
from scripts.research import load
data, meta, idx = load(('KOSPI',))
PER = (('IS', '2019-07-01', '2023-12-31'), ('OOS', '2024-01-01', '2026-11-12'))
def line(name, strat):
    panel = Panel(data, [strat], idx); out = []
    for lab, a, b in PER:
        r = run_panel(panel, [strat], start=a, end=b); s = r.summary(); t = r.by_strategy().iloc[0]
        out.append(f"{lab} 연{s['연환산']*100:5.1f}% MDD{s['MDD']*100:6.1f}% 최악월{s['최악의달']*100:6.1f}% n={int(t['거래수']):4d} 승{t['승률']*100:3.0f}% 평균{t['평균수익']*100:5.2f}% PF{t['손익비PF']:.2f}")
    print(f"{name:34s} " + ' || '.join(out), flush=True)
b = Reversal()
line('R 기본', b)
line('R 하루 신규 1종목', replace(b, max_new_per_day=1))
line('R 하루 신규 2종목', replace(b, max_new_per_day=2))
line('R 반등확인: 종목 양봉', replace(b, confirm='stock_up'))
line('R 반등확인: 지수 상승일', replace(b, confirm='mkt_up'))
line('R ATR 사다리 -0.2/-0.5/-0.8', replace(b, ladder_atr=(-0.2, -0.5, -0.8)))
line('R ATR 사다리 -0.5/-1.0/-1.5', replace(b, ladder_atr=(-0.5, -1.0, -1.5)))
line('R 변동성 비중(목표 4%)', replace(b, vol_target=0.04))
line('R 청산: 5일선 회복', replace(b, exit_rule='ma5'))
line('R 청산: 5일선 회복+익절없음', replace(b, exit_rule='ma5', tp_pct=None))
line('R 익절 절반(12%)+나머지 10일', replace(b, tp_frac=0.5))
line('R 6종목×15%', replace(b, max_positions=6, pos_weight=0.15))
line('R 6×15% + 하루2', replace(b, max_positions=6, pos_weight=0.15, max_new_per_day=2))
line('R 8×12% + 하루2', replace(b, max_positions=8, pos_weight=0.12, max_new_per_day=2))
