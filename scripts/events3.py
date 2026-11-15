import sys
sys.path.insert(0, '.')
import pandas as pd, numpy as np
mk = sys.argv[1]
F = pd.read_pickle(f'data/_events_{mk}.pkl')
cols = ['cc1', 'cc3', 'cc5', 'cc10', 'cc20', 'oc5', 'oc10']
def show(name, m):
    out = []
    for lab, per in (('IS', F.index < '2024-01-01'), ('OOS', F.index >= '2024-01-01')):
        g = F[m & per]
        out.append(f"{lab} n={len(g):6d} " + ' '.join(f"{c}={g[c].mean()*100:6.2f}" for c in cols))
    print(f"{name:34s} " + ' || '.join(out))
print('####', mk)
liq = F.val_ma20 >= 3e9
show('전체(기준선)', liq)
show('골든크로스 20/60', liq & F.gc)
show('데드크로스 20/60', liq & F.dc)
show('정배열 유지(ma20>ma60)', liq & (F.ma20 > F.ma60))
show('역배열(ma20<ma60)', liq & (F.ma20 < F.ma60))
show('RSI14<30', liq & (F.rsi14 < 30))
show('RSI14<25', liq & (F.rsi14 < 25))
show('RSI14>70', liq & (F.rsi14 > 70))
show('RSI14 30 상향돌파', liq & (F.rsi14 >= 30) & (F.groupby('code').rsi14.shift(1) < 30))
show('일목 구름 상향돌파', liq & F.cloud_up)
show('일목 구름 하향이탈', liq & F.cloud_dn)
show('일목 구름 위', liq & F.above_cloud)
show('일목 전환선>기준선 교차', liq & F.tk_up)
show('볼린저 하단 이탈(z<-2)', liq & (F.bbz < -2))
show('볼린저 z<-2.5', liq & (F.bbz < -2.5))
show('ret3<=-12%', liq & (F.ret3 <= -0.12))
show('20일고점대비 -25%', liq & (F.dd20 <= -0.25))
show('20일고점대비 -30%', liq & (F.dd20 <= -0.30))
show('ret5<=-15 & RSI14<30', liq & (F.ret5 <= -0.15) & (F.rsi14 < 30))
show('ret5<=-15 & RSI14>=30', liq & (F.ret5 <= -0.15) & (F.rsi14 >= 30))
show('ret5<=-15 & bbz<-2', liq & (F.ret5 <= -0.15) & (F.bbz < -2))
show('ret5<=-12 & bbz<-2 & RSI14<30', liq & (F.ret5 <= -0.12) & (F.bbz < -2) & (F.rsi14 < 30))
show('ret5<=-15 & 구름 위', liq & (F.ret5 <= -0.15) & F.above_cloud)
show('당일 -8%↓ & 거래량3x', liq & (F.chg <= -0.08) & (F.vol_ratio >= 3))
