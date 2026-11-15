"""이벤트 스터디: 신호 발생 후 구간별 평균 수익률(비용 전)."""
import sys, pickle
sys.path.insert(0, '.')
import numpy as np, pandas as pd
from kis_trader.indicators import add_features
from kis_trader.regime import regime_series
from scripts.research import load
pd.set_option('display.width', 250); pd.set_option('display.float_format', lambda x: f'{x:,.4f}')

markets = tuple(sys.argv[1].split(',')) if len(sys.argv) > 1 else ('KOSPI',)
data, meta, idx = load(markets)
reg = regime_series(idx)
rows = []
for code, d in data.items():
    f = add_features(d)
    c, o, h, l = f.close, f.open, f.high, f.low
    f['on'] = o.shift(-1) / c - 1                 # 종가→익일 시가
    f['id1'] = c.shift(-1) / o.shift(-1) - 1      # 익일 시가→종가
    f['oh1'] = h.shift(-1) / o.shift(-1) - 1
    f['ol1'] = l.shift(-1) / o.shift(-1) - 1
    for n in (1, 3, 5, 10, 20):
        f[f'cc{n}'] = c.shift(-n) / c - 1         # 종가→n일 뒤 종가
        f[f'oc{n}'] = c.shift(-n) / o.shift(-1) - 1   # 익일 시가→n일 뒤 종가
    f['ret5'] = c / c.shift(5) - 1
    f['ret20'] = c / c.shift(20) - 1
    from kis_trader.indicators import rsi as _rsi
    f['rsi14'] = _rsi(c, 14)
    f['gc'] = (f.ma20 > f.ma60) & (f.ma20.shift(1) <= f.ma60.shift(1))
    f['dc'] = (f.ma20 < f.ma60) & (f.ma20.shift(1) >= f.ma60.shift(1))
    tk = (h.rolling(9).max() + l.rolling(9).min()) / 2
    kj = (h.rolling(26).max() + l.rolling(26).min()) / 2
    sa = ((tk + kj) / 2).shift(26); sb = ((h.rolling(52).max() + l.rolling(52).min()) / 2).shift(26)
    top = pd.concat([sa, sb], axis=1).max(axis=1); bot = pd.concat([sa, sb], axis=1).min(axis=1)
    f['cloud_up'] = (c > top) & (c.shift(1) <= top.shift(1))
    f['cloud_dn'] = (c < bot) & (c.shift(1) >= bot.shift(1))
    f['above_cloud'] = c > top
    f['tk_up'] = (tk > kj) & (tk.shift(1) <= kj.shift(1))
    sd = c.rolling(20).std(); f['bbz'] = (c - f.ma20) / sd
    f['ret3'] = c / c.shift(3) - 1
    f['dd20'] = c / h.rolling(20).max() - 1
    f['code'] = code
    f['reg'] = reg.reindex(f.index).to_numpy()
    f = f[(f.close >= 1000) & (~f.admin) & (f.val_ma20 >= 1e9)]
    rows.append(f)
F = pd.concat(rows)
F.to_pickle('data/_events_' + '_'.join(markets) + '.pkl')
print(len(F))
