"""투매 반등(reversal) 청산·필터 변형 비교 백테스트.

  sudo docker run --rm -v "$PWD":/app -w /app --user 1000:1000 --entrypoint python kis-trader:local \
      scripts/compare_reversal.py > data/backtest_reversal.txt
"""
import time

import pandas as pd

from kis_trader.backtest import Costs, Panel, run_panel
from kis_trader.cli import load_cfg
from kis_trader.data import load_marcap, market_proxy
from kis_trader.strategies import Reversal

base = dict(max_positions=4, pos_weight=0.20, drop5=-0.15, rsi_max=30, mkt_ret5=-0.04, mkt_dd20=-0.07,
            ladder_ticks=(-3, -6, -10), ladder_weights=(0.4, 0.3, 0.3), tp_pct=0.12, stop_pct=0.15)
VARIANTS = {
    "A 기존(10일)": dict(max_hold=10),
    "B 기한만 20일": dict(max_hold=20),
    "C 코너스 5일선+20일": dict(max_hold=20, exit_rule="ma5"),
    "D C+폭락5일 제외": dict(max_hold=20, exit_rule="ma5", crash_days=5),
    "E C+지수5일<0": dict(max_hold=20, exit_rule="ma5", mkt_ret5_max=0.0),
    "F C+둘 다": dict(max_hold=20, exit_rule="ma5", crash_days=5, mkt_ret5_max=0.0),
    "G A+둘 다": dict(max_hold=10, crash_days=5, mkt_ret5_max=0.0),
}
PERIODS = [("2019-07-01", None), ("2019-07-01", "2023-12-31"), ("2024-01-01", None)]

t0 = time.time()
cfg = load_cfg("config.yaml")
data, _ = load_marcap("data/marcap", markets=("KOSPI",))
idx = market_proxy(data)["close"]
costs = Costs(**(cfg.get("costs") or {}))
rows = []
for name, kw in VARIANTS.items():
    s = Reversal(**{**base, **kw})
    panel = Panel(data, [s], idx)
    for start, end in PERIODS:
        r = run_panel(panel, [s], cash=30_000_000, costs=costs, start=start, end=end)
        sm, bs = r.summary(), r.by_strategy()
        b = bs.iloc[0] if len(bs) else {}
        rows.append({"변형": name, "구간": f"{start[:4]}~{(end or '현재')[:4]}",
                     "연환산": sm["연환산"], "MDD": sm["MDD"], "최악의달": sm["최악의달"], "거래수": sm["거래수"],
                     "승률": b.get("승률"), "평균수익": b.get("평균수익"), "PF": b.get("손익비PF"),
                     "평균보유일": b.get("평균보유일")})
    print(f"# {name} 완료 ({time.time() - t0:.0f}초)", flush=True)

out = pd.DataFrame(rows)
pd.set_option("display.width", 200)
pd.set_option("display.float_format", lambda x: f"{x:,.3f}")
for per, g in out.groupby("구간", sort=False):
    print(f"\n##### {per}")
    print(g.drop(columns="구간").to_string(index=False))
