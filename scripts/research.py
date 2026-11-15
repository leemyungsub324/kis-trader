"""연구용: marcap 실데이터로 전략별 단독·합산 백테스트."""
import sys, time, pickle, os
sys.path.insert(0, '.')
import pandas as pd
from kis_trader.data import load_marcap, market_proxy
from kis_trader.backtest import Panel, run_panel, Costs
from kis_trader.strategies import build

pd.set_option('display.width', 250); pd.set_option('display.float_format', lambda x: f'{x:,.3f}')

def load(markets):
    key = 'data/_cache_' + '_'.join(markets) + '.pkl'
    if os.path.exists(key):
        return pickle.load(open(key, 'rb'))
    data, meta = load_marcap('data/marcap', markets=markets)
    kospi, _ = (data, meta) if markets == ('KOSPI',) else load_marcap('data/marcap', markets=('KOSPI',))
    idx = market_proxy(kospi).close
    pickle.dump((data, meta, idx), open(key, 'wb'))
    return data, meta, idx

def report(res, title):
    s = res.summary()
    print(f"\n== {title} ==")
    print(' | '.join(f"{k} {v:.1%}" if k != '거래수' else f"{k} {v}" for k, v in s.items()))
    print(res.by_strategy().to_string())

if __name__ == '__main__':
    markets = tuple(sys.argv[1].split(',')) if len(sys.argv) > 1 else ('KOSPI',)
    data, meta, idx = load(markets)
    strats = build()
    t = time.time(); panel = Panel(data, strats, idx); print('panel', time.time() - t)
    for period in (('2019-07-01', '2023-12-31'), ('2024-01-01', '2026-11-12')):
        for s in strats:
            report(run_panel(panel, [s], start=period[0], end=period[1]), f"{s.label} 단독 {period}")
        report(run_panel(panel, strats, start=period[0], end=period[1]), f"합산 {period}")
