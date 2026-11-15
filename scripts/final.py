import sys; sys.path.insert(0,'.')
import pandas as pd
from kis_trader.backtest import Panel, run_panel
from kis_trader.strategies import build
from scripts.research import load
pd.set_option('display.width',250); pd.set_option('display.float_format',lambda x:f'{x:,.3f}')
markets=tuple(sys.argv[1].split(',')); data,meta,idx=load(markets)
st=build(); panel=Panel(data,st,idx)
for a,b in (('2019-07-01','2023-12-31'),('2024-01-01','2026-11-12'),('2019-07-01','2026-11-12')):
    r=run_panel(panel,st,start=a,end=b); s=r.summary()
    print(f"\n== {markets} {a}~{b} =="); print(' | '.join(f"{k} {v:.1%}" if k!='거래수' else f"{k} {v}" for k,v in s.items())); print(r.by_strategy().to_string())
y=r.equity.resample('YE').last().pct_change(); y.iloc[0]=r.equity.resample('YE').last().iloc[0]/5_000_000-1
print('연도별:',{k.year:round(v*100,1) for k,v in y.items()})
t=r.trades; print(t.groupby([t.date_in.dt.year,'strat']).agg(n=('ret','size'),avg=('ret','mean'),pnl=('pnl','sum')).round(3).to_string())
m=r.equity.resample('ME').last().pct_change().dropna(); print('월수익률 분위(10/25/50/75/90%):',[round(x*100,1) for x in m.quantile([.1,.25,.5,.75,.9])])
