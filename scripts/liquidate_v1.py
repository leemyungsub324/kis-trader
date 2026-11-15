"""v1 이 남긴 삼성전자(005930) 보유분 시장가 전량 청산 (모의투자 전용, 2026-11-15 결정).

  PYTHONPATH=/app python scripts/liquidate_v1.py

- env=mock 과 모의 서버 주소가 아니면 중단, 평일 09:05~15:10 밖이면 중단.
- 매도 수량 = min(실제 보유 수량, 4). 보유가 없으면 아무것도 하지 않는다.
- 주문번호·체결 수량·평균가를 당일 체결조회로 확인하고 data/verify/liquidate_*.json 에 남긴다.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime

from kis_trader.cli import load_cfg
from kis_trader.kis_api import URLS, KisClient

CODE, MAX_QTY = "005930", 4


def main():
    cfg = load_cfg("config.yaml")
    if cfg.get("env") != "mock":
        sys.exit("env 가 mock 이 아님 → 중단")
    now = datetime.now()
    if now.weekday() >= 5 or not ("09:05" <= now.strftime("%H:%M") <= "15:10"):
        sys.exit(f"장중(평일 09:05~15:10)에만 실행: 지금 {now:%a %H:%M}")
    kc = KisClient(cfg["app_key"], cfg["app_secret"], cfg["account"], "mock", token_dir=cfg["data_dir"])
    assert kc.base == URLS["mock"] and "vts" in kc.base

    before = kc.balance()
    held = before["holdings"].get(CODE, {})
    qty = min(int(held.get("qty", 0)), MAX_QTY)
    print(f"보유 {CODE} {held.get('qty', 0)}주 평단 {held.get('avg', 0):,.0f} → 매도 {qty}주")
    if qty <= 0:
        print("보유 없음 → 종료")
        return
    r = kc.order(CODE, "sell", qty)          # price=0 → 시장가
    print("주문번호", r)
    row = None
    for _ in range(15):
        time.sleep(3)
        row = kc.orders_today().get(r["odno"])
        if row and (row["filled"] >= qty or row["cancelled"]):
            break
    print("체결조회", row)
    after = kc.balance()
    out = {"order": r, "qty": qty, "ccld": row, "avg_before": held.get("avg"),
           "holding_after": after["holdings"].get(CODE, {}).get("qty", 0),
           "cash_before": before["cash"], "cash_after": after["cash"],
           "equity_before": before["equity"], "equity_after": after["equity"]}
    if row and row["filled"]:
        out["pnl_before_cost"] = (row["avg"] - held.get("avg", 0)) * row["filled"]
    for k, v in out.items():
        print(f"  {k}: {v}")
    os.makedirs(os.path.join(cfg["data_dir"], "verify"), exist_ok=True)
    path = os.path.join(cfg["data_dir"], "verify", f"liquidate_{now:%Y%m%d_%H%M}.json")
    with open(path, "w") as f:
        json.dump(out, f, ensure_ascii=False, indent=1, default=str)
    print("저장:", path)


if __name__ == "__main__":
    main()
