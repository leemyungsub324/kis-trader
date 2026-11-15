import sys
sys.path.insert(0, '.')
import pandas as pd, numpy as np
pd.set_option('display.width', 250)
mk = sys.argv[1]
F = pd.read_pickle(f'data/_events_{mk}.pkl')
cols = ['on', 'id1', 'cc1', 'cc3', 'cc5', 'cc10', 'cc20', 'oc5', 'oc10']
def show(name, m):
    out = []
    for lab, per in (('IS', F.index < '2024-01-01'), ('OOS', F.index >= '2024-01-01')):
        g = F[m & per]
        out.append(f"{lab} n={len(g):6d} " + ' '.join(f"{c}={g[c].mean()*100:6.2f}" for c in cols))
    print(f"{name:34s} " + ' || '.join(out))
print('#### ', mk)
base = (F.value >= 5e9) & (F.close > F.ma20)
print('-- 급등 당일 종가 매수 → 익일 시가(on) : 등락률×종가위치')
for lo, hi in ((0.05, 0.10), (0.10, 0.15), (0.15, 0.20), (0.20, 0.29), (0.29, 0.31)):
    for cl in (0.0, 0.8, 0.95):
        show(f'chg {lo:.2f}-{hi:.2f} clv>={cl}', base & (F.chg >= lo) & (F.chg < hi) & (F.clv >= cl))
print('-- 거래량 배수별(chg>=7%, clv>=0.8)')
for v in (1, 2, 3, 5, 10):
    show(f'volx>={v}', base & (F.chg >= 0.07) & (F.chg < 0.29) & (F.clv >= 0.8) & (F.vol_ratio >= v))
print('-- 낙폭과대: 5일 수익률 × 유동성/국면')
liq = F.val_ma20 >= 5e9
for th in (-0.10, -0.15, -0.20, -0.25):
    show(f'ret5<={th}', liq & (F.ret5 <= th))
for r in ('BULL', 'NEUTRAL', 'BEAR'):
    show(f'ret5<=-0.15 {r}', liq & (F.ret5 <= -0.15) & (F.reg == r))
show('ret5<=-0.15 & mcap>=5000억', liq & (F.ret5 <= -0.15) & (F.mcap >= 5e11))
show('ret5<=-0.15 & mcap<2000억', liq & (F.ret5 <= -0.15) & (F.mcap < 2e11))
show('ret5<=-0.15 & 당일 양봉', liq & (F.ret5 <= -0.15) & (F.close > F.open))
show('ret5<=-0.15 & 당일 음봉', liq & (F.ret5 <= -0.15) & (F.close <= F.open))
show('ret5<=-0.15 & rsi2<=10', liq & (F.ret5 <= -0.15) & (F.rsi2 <= 10))
show('ret5<=-0.15 & close>ma120', liq & (F.ret5 <= -0.15) & (F.close > F.ma120))
show('ret5<=-0.15 & close<ma120', liq & (F.ret5 <= -0.15) & (F.close <= F.ma120))
print('-- 연도별 ret5<=-15% 5일·10일 후')
g = F[liq & (F.ret5 <= -0.15)]
print((g.groupby(g.index.year)[['cc5', 'cc10', 'oc5']].agg(['mean', 'count']) * 1).round(4).to_string())
print('-- 연도별 급등 종가베팅 on (chg 7~29, clv>=0.8, volx>=3)')
g = F[base & (F.chg >= 0.07) & (F.chg < 0.29) & (F.clv >= 0.8) & (F.vol_ratio >= 3)]
print(g.groupby(g.index.year)['on'].agg(['mean', 'median', 'count', lambda x: (x > 0).mean()]).round(4).to_string())
