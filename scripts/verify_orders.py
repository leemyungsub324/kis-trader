"""모의투자 주문 실접속 검증 (월요일 장중 09:05~15:10 에 실행). KODEX 200(069500) 1주만 쓴다.

  PYTHONPATH=/app python scripts/verify_orders.py

A. 주문 전 잔고·주문가능금액
B. 1주 지정가 매수(현재가 -5%, 체결 안 되는 가격) → 주문번호 확인 → 취소 → 당일 체결조회에 반영되는지
C. 1주 시장가 매수 → Engine.sync_fills 로 체결 반영(state·trades.csv)
D. 1주 시장가 매도 직후 / 체결 후 balance().cash 와 주문가능금액(inquire-psbl-order) 비교
   → engine.open() 이 _park 매도 직후 balance() 를 다시 읽어 매수 예산을 잡는데, 그 cash 에 매도 대금이 들어가는지

안전장치: env=mock 과 모의 서버 주소가 아니면 중단, 평일 09:05~15:10 밖이면 중단, 수량은 항상 1주.
엔진 상태는 임시 폴더(data/verify/engine_*)에 쓰므로 실제 data/state.json 은 건드리지 않는다.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime

from kis_trader.cli import load_cfg
from kis_trader.engine import Engine
from kis_trader.kis_api import URLS, KisClient
from kis_trader.strategies import build
from kis_trader.ticks import round_down

CODE = "069500"
OUT = {}


def show(k, v):
    OUT[k] = v
    print(f"  {k}: {v}")


def psbl(kc, price=0):
    """주문가능금액 조회 (TTTC8908R → 모의 VTTC8908R). 시장가 기준."""
    d = kc._call("GET", "/uapi/domestic-stock/v1/trading/inquire-psbl-order", kc._tr("TTTC8908R"), {
        "CANO": kc.cano, "ACNT_PRDT_CD": kc.prdt, "PDNO": CODE, "ORD_UNPR": str(int(price)),
        "ORD_DVSN": "01" if not price else "00", "CMA_EVLU_AMT_ICLD_YN": "N", "OVRS_ICLD_YN": "N"})
    o = d.get("output") or {}
    return {k: o.get(k) for k in ("ord_psbl_cash", "nrcvb_buy_amt", "max_buy_amt", "max_buy_qty", "ruse_psbl_amt")}


def cash_view(kc, tag):
    b = kc.balance()
    p = psbl(kc)
    show(f"{tag}.balance.cash", b["cash"])
    show(f"{tag}.balance.equity", b["equity"])
    show(f"{tag}.holding_{CODE}", b["holdings"].get(CODE, {}).get("qty", 0))
    show(f"{tag}.psbl", p)
    return b, p


def wait_fill(e, odno, sec=40):
    t0 = time.time()
    while time.time() - t0 < sec:
        e.sync_fills()
        o = next(o for o in e.st["orders"] if o["odno"] == odno)
        if not o["open"]:
            return o, time.time() - t0
        time.sleep(3)
    return next(o for o in e.st["orders"] if o["odno"] == odno), None


def main():
    cfg = load_cfg("config.yaml")
    if cfg.get("env") != "mock":
        sys.exit("env 가 mock 이 아님 → 중단")
    now = datetime.now()
    if now.weekday() >= 5 or not ("09:05" <= now.strftime("%H:%M") <= "15:10"):
        sys.exit(f"장중(평일 09:05~15:10)에만 실행: 지금 {now:%a %H:%M}")
    kc = KisClient(cfg["app_key"], cfg["app_secret"], cfg["account"], "mock", token_dir=cfg["data_dir"])
    assert kc.base == URLS["mock"] and "vts" in kc.base
    tmp = os.path.join(cfg["data_dir"], "verify", f"engine_{now:%Y%m%d_%H%M}")
    os.makedirs(tmp, exist_ok=True)
    e = Engine(kc, build(cfg.get("strategies")), cfg, data_dir=tmp)
    e._roll_day()

    print("A) 주문 전")
    q = kc.price(CODE)
    show("price", q["price"])
    cash_view(kc, "A")

    print("B) 1주 지정가 매수 → 취소")
    px = max(round_down(q["price"] * 0.95, etf=True), int(q["lower"]))
    r = e._place(CODE, "park", "buy", 1, px, "park", "검증-지정가")
    show("B.order", r)
    time.sleep(2)
    od = kc.orders_today()
    show("B.ccld_before_cancel", od.get(r["odno"]))
    ok = kc.cancel(r["odno"], r["orgno"])
    show("B.cancel_ok", ok)
    time.sleep(2)
    od = kc.orders_today()
    show("B.ccld_after_cancel_original", od.get(r["odno"]))
    show("B.ccld_other_rows_today", {k: v for k, v in od.items() if k != r["odno"]})
    for o in e.st["orders"]:
        if o["odno"] == r["odno"]:
            o["open"] = False      # 엔진의 _cancel 과 같은 처리
    e.sync_fills()
    show("B.engine_positions", e.st["positions"])
    show("B.engine_park", e.st.get("park"))

    print("C) 1주 시장가 매수 → sync_fills")
    r = e._place(CODE, "park", "buy", 1, 0, "park", "검증-시장가매수")
    show("C.order", r)
    o, dt = wait_fill(e, r["odno"])
    show("C.filled_after_sec", dt)
    show("C.engine_order", o)
    show("C.engine_park", e.st.get("park"))
    show("C.ccld_row", kc.orders_today().get(r["odno"]))
    b_c, p_c = cash_view(kc, "C")

    print("D) 1주 시장가 매도 → 현금 반영")
    r = e._place(CODE, "park", "sell", 1, 0, "park", "검증-시장가매도")
    show("D.order", r)
    cash_view(kc, "D.immediately")
    o, dt = wait_fill(e, r["odno"])
    show("D.filled_after_sec", dt)
    show("D.engine_order", o)
    show("D.engine_park", e.st.get("park"))
    b_d, p_d = cash_view(kc, "D.after_fill")
    time.sleep(10)
    b_d2, p_d2 = cash_view(kc, "D.after_10s")
    sale = (o.get("filled") or 0) and kc.orders_today().get(r["odno"], {}).get("avg", 0)
    show("D.sale_price", sale)
    show("D.balance_cash_delta_vs_C", b_d2["cash"] - b_c["cash"])
    try:
        show("D.psbl_cash_delta_vs_C", float(p_d2["ord_psbl_cash"]) - float(p_c["ord_psbl_cash"]))
    except (TypeError, ValueError):
        pass
    with open(os.path.join(tmp, "trades.csv")) as f:
        show("trades.csv", f.read().strip().splitlines())
    path = os.path.join(cfg["data_dir"], "verify", f"orders_{now:%Y%m%d_%H%M}.json")
    with open(path, "w") as f:
        json.dump(OUT, f, ensure_ascii=False, indent=1, default=str)
    print("저장:", path)


if __name__ == "__main__":
    main()
