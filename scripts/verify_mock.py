"""모의투자 서버 실접속 검증 (조회 전용, 주문 없음).

kis_api.py 가 읽는 필드명이 실제 응답에 있는지 하나씩 대조한다.
  PYTHONPATH=/app python scripts/verify_mock.py [--ccld-date YYYYMMDD]

- env 가 mock 이 아니면 바로 종료한다.
- 원본 응답(토큰 발급 응답 제외)을 data/verify/ 아래 JSON 으로 남긴다. 응답에는 키·계좌번호가 없다.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
from datetime import datetime, timedelta

import pandas as pd
import requests

from kis_trader.cli import load_cfg
from kis_trader.kis_api import URLS, KisClient

# kis_api.py 가 실제로 읽는 필드 (코드에서 그대로 옮김)
USED = {
    "price.output": ["stck_prpr", "stck_oprc", "stck_hgpr", "stck_lwpr", "stck_sdpr", "acml_vol", "acml_tr_pbmn",
                     "stck_mxpr", "stck_llam", "iscd_stat_cls_code", "mrkt_warn_cls_code", "temp_stop_yn"],
    "daily.output2": ["stck_bsop_date", "stck_oprc", "stck_hgpr", "stck_lwpr", "stck_clpr", "acml_vol", "acml_tr_pbmn"],
    "balance.output1": ["pdno", "hldg_qty", "pchs_avg_pric", "prdt_name"],
    "balance.output2": ["prvs_rcdl_excc_amt", "dnca_tot_amt", "tot_evlu_amt"],
    "ccld.output1": ["odno", "pdno", "sll_buy_dvsn_cd", "ord_qty", "tot_ccld_qty", "avg_prvs", "rmn_qty", "cncl_yn"],
    "paging": ["ctx_area_fk100", "ctx_area_nk100"],
}


class Recorder:
    """requests.Session 래퍼: 응답 원본을 기록한다(토큰 발급 응답은 키 이름만)."""

    def __init__(self):
        self.s, self.log = requests.Session(), []

    def _rec(self, method, url, r, tr_id):
        try:
            body = r.json()
        except ValueError:
            body = {"_raw": r.text[:500]}
        if url.endswith("/oauth2/tokenP"):
            body = {k: ("<hidden>" if k == "access_token" else v) for k, v in body.items()}
        self.log.append({"method": method, "path": url.split(":29443")[-1], "tr_id": tr_id, "http": r.status_code,
                         "resp_tr_cont": r.headers.get("tr_cont"), "body": body})
        return r

    def get(self, url, headers=None, **kw):
        return self._rec("GET", url, self.s.get(url, headers=headers, **kw), (headers or {}).get("tr_id"))

    def post(self, url, headers=None, **kw):
        return self._rec("POST", url, self.s.post(url, headers=headers, **kw), (headers or {}).get("tr_id"))


def ok_body(rec, n, path_part):
    """n 번째 기록 이후 path_part 호출 중 마지막 성공 응답 (재시도 앞의 실패 응답은 건너뜀)."""
    for e in reversed(rec.log[n:]):
        if path_part in e["path"] and e["body"].get("rt_cd") == "0":
            return e
    raise RuntimeError(f"{path_part} 성공 응답 없음")


def keys_of(x):
    if isinstance(x, list):
        x = x[0] if x else {}
    return set(x) if isinstance(x, dict) else set()


def check(name, have, problems):
    need = USED[name]
    miss = [k for k in need if k not in have]
    print(f"  [{'OK' if not miss else '누락'}] {name}: 사용 {len(need)}개" + (f", 없음 {miss}" if miss else ""))
    if miss:
        problems.append((name, miss))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-c", "--config", default="config.yaml")
    ap.add_argument("--ccld-date", help="체결조회 필드 확인용 과거 거래일(YYYYMMDD). 기본: 최근 평일")
    a = ap.parse_args()
    cfg = load_cfg(a.config)
    if cfg.get("env") != "mock":
        sys.exit("env 가 mock 이 아님 → 중단")
    rec = Recorder()
    kc = KisClient(cfg["app_key"], cfg["app_secret"], cfg["account"], "mock", token_dir=cfg["data_dir"], session=rec)
    assert kc.base == URLS["mock"]
    problems, report = [], {}

    print("1) 토큰")
    had = os.path.exists(kc.token_path)
    kc.token()
    tok = next((e for e in rec.log if e["path"].endswith("tokenP")), None)
    print(f"  캐시 {'재사용' if had and tok is None else '새로 발급'}; 만료(로컬) {kc._expire:%Y-%m-%d %H:%M}"
          + (f"; 응답 키 {sorted(tok['body'])}" if tok else ""))
    if tok:
        for k in ("access_token", "expires_in"):
            if k not in tok["body"]:
                problems.append(("tokenP", [k]))

    print("2) 현재가 (005930, 069500)")
    for code in ("005930", "069500"):
        n = len(rec.log)
        q = kc.price(code)
        raw = ok_body(rec, n, "inquire-price")["body"]["output"]
        check("price.output", keys_of(raw), problems)
        print(f"    {code}: {q}")
        report[f"price_{code}"] = q

    print("3) 일봉 (005930, 200거래일) + marcap 대조")
    n = len(rec.log)
    h = kc.daily_history("005930", 200)
    check("daily.output2", keys_of(ok_body(rec, n, "itemchartprice")["body"].get("output2")), problems)
    print(f"    {len(h)}건 {h.index[0].date()} ~ {h.index[-1].date()}, 호출 {len(rec.log) - n}회")
    files = sorted(glob.glob(os.path.join(cfg["data_dir"], "marcap", "marcap-*.parquet")))
    if files:
        m = pd.read_parquet(files[-1], columns=["Code", "Date", "Close", "Volume"])
        m = m[m.Code == "005930"].set_index("Date").sort_index()
        j = h.join(m, how="inner").tail(10)
        diff = (j["close"] - j["Close"]).abs().max()
        vol = (j["volume"] - j["Volume"]).abs().max()
        print(f"    marcap 과 겹치는 최근 {len(j)}일: 종가 최대차 {diff:.0f}원, 거래량 최대차 {vol:.0f}")
        if diff > 0:
            problems.append(("daily vs marcap", [f"종가 차이 {diff}"]))

    print("4) 잔고")
    n = len(rec.log)
    b = kc.balance()
    first_e = next(e for e in rec.log[n:] if "inquire-balance" in e["path"] and e["body"].get("rt_cd") == "0")
    first = first_e["body"]
    check("balance.output1", keys_of(first.get("output1")) if first.get("output1") else set(USED["balance.output1"]), problems)
    if not first.get("output1"):
        print("    (보유 종목 없음 → output1 필드는 확인 불가)")
    check("balance.output2", keys_of(first.get("output2")), problems)
    check("paging", set(first), problems)
    o2 = first.get("output2")
    o2 = o2[0] if isinstance(o2, list) and o2 else o2 or {}
    cash_keys = ["dnca_tot_amt", "nxdy_excc_amt", "prvs_rcdl_excc_amt", "tot_evlu_amt", "scts_evlu_amt", "nass_amt"]
    print("    output2 금액:", {k: o2.get(k) for k in cash_keys})
    print(f"    balance() → cash {b['cash']:,.0f} / equity {b['equity']:,.0f} / 보유 {len(b['holdings'])}종목")
    for c, v in b["holdings"].items():
        print(f"      {c} {v['name']} {v['qty']}주 평단 {v['avg']:,.0f}")
    print(f"    tr_cont 헤더: {first_e['resp_tr_cont']!r}, 호출 {len(rec.log) - n}회")
    report["balance"] = b

    print("5) 당일 주문체결 (오늘) + 과거 거래일 필드 확인")
    n = len(rec.log)
    od = kc.orders_today()
    e = ok_body(rec, n, "inquire-daily-ccld")
    print(f"    오늘: {len(od)}건, 응답 msg {e['body'].get('msg_cd')} {e['body'].get('msg1')}")
    d = a.ccld_date
    if not d:
        t = datetime.now() - timedelta(days=1)
        while t.weekday() >= 5:
            t -= timedelta(days=1)
        d = t.strftime("%Y%m%d")
    params = {"CANO": kc.cano, "ACNT_PRDT_CD": kc.prdt, "INQR_STRT_DT": d, "INQR_END_DT": d,
              "SLL_BUY_DVSN_CD": "00", "PDNO": "", "CCLD_DVSN": "00", "INQR_DVSN": "00", "INQR_DVSN_3": "00",
              "ORD_GNO_BRNO": "", "ODNO": "", "INQR_DVSN_1": "", "CTX_AREA_FK100": "", "CTX_AREA_NK100": ""}
    try:
        r = kc._call("GET", "/uapi/domestic-stock/v1/trading/inquire-daily-ccld", kc._tr("TTTC0081R"), params)
        rows = r.get("output1", [])
        print(f"    {d}: {len(rows)}건")
        if rows:
            check("ccld.output1", keys_of(rows), problems)
            for x in rows[:5]:
                print("     ", {k: x.get(k) for k in USED["ccld.output1"]})
        else:
            print("    (해당일 주문 없음 → output1 필드는 확인 불가)")
    except Exception as e:
        print(f"    {d}: 실패 {e}")
        problems.append(("ccld", [str(e)]))

    errs = [(e["tr_id"], e["body"].get("msg_cd"), e["body"].get("msg1")) for e in rec.log
            if "rt_cd" in e["body"] and e["body"].get("rt_cd") != "0"]
    print(f"\n실패 응답(재시도로 넘어간 것 포함) {len(errs)}건:", errs[:10])
    os.makedirs(os.path.join(cfg["data_dir"], "verify"), exist_ok=True)
    out = os.path.join(cfg["data_dir"], "verify", f"query_{datetime.now():%Y%m%d_%H%M}.json")
    with open(out, "w") as f:
        json.dump({"calls": rec.log, "report": report, "problems": problems}, f, ensure_ascii=False, indent=1, default=str)
    print(f"\n원본 응답 저장: {out} (호출 {len(rec.log)}회)")
    print("문제:", problems or "없음")


if __name__ == "__main__":
    main()
