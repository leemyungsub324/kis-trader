"""한국투자증권 Open API(REST) 클라이언트.

TR ID·파라미터는 공식 저장소(github.com/koreainvestment/open-trading-api) 예제 기준이다.
모의투자에서 지원하지 않는 API(정정취소가능주문조회, 휴장일조회)는 쓰지 않는다.
거래량순위·등락률순위·업종지수는 2026-11-16 모의서버에서 정상 응답을 확인했다(시황 브리핑용).
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta

import pandas as pd
import requests

log = logging.getLogger("kis")

URLS = {
    "real": "https://openapi.koreainvestment.com:9443",
    "mock": "https://openapivts.koreainvestment.com:29443",
}
MIN_INTERVAL = {"real": 0.06, "mock": 0.55}     # 초당 호출 제한(실전 20건, 모의 2건) 대응


class KisError(RuntimeError):
    pass


class KisClient:
    def __init__(self, app_key: str, app_secret: str, account: str, env: str = "mock",
                 token_dir: str = ".", session=None, sleep=time.sleep):
        if env not in URLS:
            raise ValueError("env는 mock 또는 real")
        self.env, self.base = env, URLS[env]
        self.key, self.secret = app_key, app_secret
        acc = account.replace("-", "")
        self.cano, self.prdt = acc[:8], (acc[8:] or "01")
        self.token_path = os.path.join(token_dir, f".token_{env}.json")
        self.http = session or requests.Session()
        self._sleep = sleep
        self._last = 0.0
        self._token = None
        self._expire = datetime.min

    # ------------------------------------------------------------------ 인증
    def token(self) -> str:
        if self._token and datetime.now() < self._expire:
            return self._token
        if os.path.exists(self.token_path):
            try:
                with open(self.token_path) as f:
                    t = json.load(f)
                exp = datetime.fromisoformat(t["expire"])
                if t.get("key") == self.key[-6:] and datetime.now() < exp:
                    self._token, self._expire = t["token"], exp
                    return self._token
            except (ValueError, KeyError):
                pass
        r = self.http.post(f"{self.base}/oauth2/tokenP", timeout=10, json={
            "grant_type": "client_credentials", "appkey": self.key, "appsecret": self.secret})
        d = r.json()
        if "access_token" not in d:
            raise KisError(f"토큰 발급 실패: {d}")
        self._token = d["access_token"]
        # 유효기간 24시간. 여유를 두고 1시간 일찍 만료 처리(재발급은 1분당 1회 제한)
        self._expire = datetime.now() + timedelta(seconds=int(d.get("expires_in", 86400)) - 3600)
        with open(self.token_path, "w") as f:
            json.dump({"token": self._token, "expire": self._expire.isoformat(), "key": self.key[-6:]}, f)
        os.chmod(self.token_path, 0o600)
        return self._token

    def _call(self, method: str, path: str, tr_id: str, params=None, body=None, tr_cont: str = ""):
        headers = {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self.token()}",
            "appkey": self.key, "appsecret": self.secret,
            "tr_id": tr_id, "custtype": "P", "tr_cont": tr_cont,
        }
        for attempt in range(4):
            wait = MIN_INTERVAL[self.env] - (time.monotonic() - self._last)
            if wait > 0:
                self._sleep(wait)
            self._last = time.monotonic()
            try:
                if method == "GET":
                    r = self.http.get(self.base + path, headers=headers, params=params, timeout=10)
                else:
                    r = self.http.post(self.base + path, headers=headers, json=body, timeout=10)
                d = r.json()
            except (requests.RequestException, ValueError) as e:
                if method == "POST":         # 주문은 중복 위험이 있어 재시도하지 않는다
                    raise KisError(f"주문 전송 오류(체결 여부 확인 필요): {e}") from e
                log.warning("통신 오류 %s (재시도 %d)", e, attempt + 1)
                self._sleep(1 + attempt)
                continue
            if d.get("rt_cd") == "0":
                d["_tr_cont"] = r.headers.get("tr_cont", "")
                return d
            msg = f"{d.get('msg_cd')} {d.get('msg1')}"
            if d.get("msg_cd") == "EGW00201" or "초당" in str(d.get("msg1")):   # 호출 제한 초과
                self._sleep(1 + attempt)
                continue
            if d.get("msg_cd") in ("EGW00123", "EGW00121"):                     # 토큰 만료
                self._token = None
                if os.path.exists(self.token_path):
                    os.remove(self.token_path)
                headers["authorization"] = f"Bearer {self.token()}"
                continue
            raise KisError(f"{tr_id} 실패: {msg}")
        raise KisError(f"{tr_id} 재시도 초과")

    def _tr(self, real: str) -> str:
        """계좌 관련 TR ID는 모의투자에서 첫 글자가 V로 바뀐다."""
        return real if self.env == "real" else "V" + real[1:]

    # ------------------------------------------------------------------ 시세
    def price(self, code: str) -> dict:
        """현재가. 주요 키: price open high low prev volume value warn halted."""
        o = self._call("GET", "/uapi/domestic-stock/v1/quotations/inquire-price", "FHKST01010100",
                       {"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code})["output"]
        f = lambda k: float(o.get(k) or 0)
        return {
            "price": f("stck_prpr"), "open": f("stck_oprc"), "high": f("stck_hgpr"), "low": f("stck_lwpr"),
            "prev": f("stck_sdpr"), "volume": f("acml_vol"), "value": f("acml_tr_pbmn"),
            "upper": f("stck_mxpr"), "lower": f("stck_llam"),
            # 51 관리 52 투자위험 53 투자경고 54 투자주의 58 거래정지 59 단기과열
            "warn": o.get("iscd_stat_cls_code", "") in ("51", "52", "53", "58", "59")
                    or o.get("mrkt_warn_cls_code", "00") in ("02", "03"),
            "halted": o.get("temp_stop_yn", "N") == "Y" or o.get("iscd_stat_cls_code") == "58",
        }

    def index_price(self, code: str = "0001") -> dict:
        """업종지수 현재가. code: 0001 코스피, 1001 코스닥, 2001 코스피200."""
        o = self._call("GET", "/uapi/domestic-stock/v1/quotations/inquire-index-price", "FHPUP02100000",
                       {"FID_COND_MRKT_DIV_CODE": "U", "FID_INPUT_ISCD": code})["output"]
        f = lambda k: float(o.get(k) or 0)
        return {"price": f("bstp_nmix_prpr"), "chg": f("bstp_nmix_prdy_ctrt") / 100,
                "open": f("bstp_nmix_oprc"), "high": f("bstp_nmix_hgpr"), "low": f("bstp_nmix_lwpr"),
                "prev": f("bstp_nmix_prpr") - f("bstp_nmix_prdy_vrss"), "value": f("acml_tr_pbmn") * 1e6,
                "up": int(f("ascn_issu_cnt")), "down": int(f("down_issu_cnt")),
                "upper": int(f("uplm_issu_cnt")), "lower": int(f("lslm_issu_cnt"))}

    def volume_rank(self, kind: str = "value") -> list[dict]:
        """보통주 순위 30건(ETF·ETN·우선주·관리·경고 종목 제외).
        kind: value 거래대금순 / surge 평균거래량 대비 증가율순."""
        d = self._call("GET", "/uapi/domestic-stock/v1/quotations/volume-rank", "FHPST01710000", {
            "FID_COND_MRKT_DIV_CODE": "J", "FID_COND_SCR_DIV_CODE": "20171", "FID_INPUT_ISCD": "0000",
            "FID_DIV_CLS_CODE": "1", "FID_BLNG_CLS_CODE": {"value": "3", "surge": "1"}[kind],
            "FID_TRGT_CLS_CODE": "111111111", "FID_TRGT_EXLS_CLS_CODE": "1111111111",
            "FID_INPUT_PRICE_1": "", "FID_INPUT_PRICE_2": "", "FID_VOL_CNT": "", "FID_INPUT_DATE_1": ""})
        f = lambda o, k: float(o.get(k) or 0)
        return [{"code": o["mksc_shrn_iscd"], "name": o["hts_kor_isnm"], "price": f(o, "stck_prpr"),
                 "chg": f(o, "prdy_ctrt") / 100, "value": f(o, "acml_tr_pbmn"),
                 "vol_ratio": f(o, "vol_inrt") / 100} for o in d.get("output") or []]

    def change_rank(self, down: bool = False) -> list[dict]:
        """등락률 순위 30건(상승률순 / down=True면 하락률순). 거래대금은 현재가×거래량 추정치."""
        d = self._call("GET", "/uapi/domestic-stock/v1/ranking/fluctuation", "FHPST01700000", {
            "fid_cond_mrkt_div_code": "J", "fid_cond_scr_div_code": "20170", "fid_input_iscd": "0000",
            "fid_rank_sort_cls_code": "1" if down else "0", "fid_input_cnt_1": "0", "fid_prc_cls_code": "0",
            "fid_input_price_1": "", "fid_input_price_2": "", "fid_vol_cnt": "", "fid_trgt_cls_code": "0",
            "fid_trgt_exls_cls_code": "0", "fid_div_cls_code": "0", "fid_rsfl_rate1": "", "fid_rsfl_rate2": ""})
        f = lambda o, k: float(o.get(k) or 0)
        return [{"code": o["stck_shrn_iscd"], "name": o["hts_kor_isnm"], "price": f(o, "stck_prpr"),
                 "chg": f(o, "prdy_ctrt") / 100, "value": f(o, "stck_prpr") * f(o, "acml_vol"),
                 "high": f(o, "stck_hgpr"), "low": f(o, "stck_lwpr")} for o in d.get("output") or []]

    def daily(self, code: str, start: str, end: str) -> pd.DataFrame:
        """수정주가 일봉(최대 100건). start/end: YYYYMMDD."""
        d = self._call("GET", "/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice", "FHKST03010100", {
            "FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code, "FID_INPUT_DATE_1": start,
            "FID_INPUT_DATE_2": end, "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "0"})
        rows = [r for r in d.get("output2", []) if r.get("stck_bsop_date")]
        if not rows:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume", "value"])
        df = pd.DataFrame({
            "date": pd.to_datetime([r["stck_bsop_date"] for r in rows]),
            "open": [float(r["stck_oprc"]) for r in rows], "high": [float(r["stck_hgpr"]) for r in rows],
            "low": [float(r["stck_lwpr"]) for r in rows], "close": [float(r["stck_clpr"]) for r in rows],
            "volume": [float(r["acml_vol"]) for r in rows], "value": [float(r["acml_tr_pbmn"]) for r in rows],
        }).set_index("date").sort_index()
        return df[df["volume"] > 0]

    def daily_history(self, code: str, days: int = 200) -> pd.DataFrame:
        """최근 days 거래일 일봉. 100건씩 나눠 받는다."""
        out, end = [], datetime.now()
        for _ in range((days + 99) // 100):
            start = end - timedelta(days=145)          # 달력 145일 ≈ 거래일 100일
            df = self.daily(code, start.strftime("%Y%m%d"), end.strftime("%Y%m%d"))
            if df.empty:
                break
            out.append(df)
            end = df.index[0].to_pydatetime() - timedelta(days=1)
        if not out:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume", "value"])
        df = pd.concat(out).sort_index()
        return df[~df.index.duplicated()].tail(days)

    # ------------------------------------------------------------------ 주문
    def order(self, code: str, side: str, qty: int, price: int = 0) -> dict:
        """현금 주문. price=0이면 시장가, 아니면 지정가. 반환: {odno, orgno}."""
        if qty <= 0:
            raise ValueError("수량은 1 이상")
        tr = self._tr("TTTC0012U" if side == "buy" else "TTTC0011U")
        body = {
            "CANO": self.cano, "ACNT_PRDT_CD": self.prdt, "PDNO": code,
            "ORD_DVSN": "01" if price == 0 else "00",
            "ORD_QTY": str(int(qty)), "ORD_UNPR": str(int(price)),
            "EXCG_ID_DVSN_CD": "KRX", "SLL_TYPE": "01" if side == "sell" else "", "CNDT_PRIC": "",
        }
        o = self._call("POST", "/uapi/domestic-stock/v1/trading/order-cash", tr, body=body)["output"]
        return {"odno": str(o["ODNO"]).lstrip("0"), "orgno": o.get("KRX_FWDG_ORD_ORGNO", "")}

    def cancel(self, odno: str, orgno: str) -> bool:
        """잔량 전부 취소. 이미 체결·취소된 주문이면 False."""
        body = {
            "CANO": self.cano, "ACNT_PRDT_CD": self.prdt, "KRX_FWDG_ORD_ORGNO": orgno,
            "ORGN_ODNO": odno, "ORD_DVSN": "00", "RVSE_CNCL_DVSN_CD": "02",
            "ORD_QTY": "0", "ORD_UNPR": "0", "QTY_ALL_ORD_YN": "Y", "EXCG_ID_DVSN_CD": "KRX",
        }
        try:
            self._call("POST", "/uapi/domestic-stock/v1/trading/order-rvsecncl", self._tr("TTTC0013U"), body=body)
            return True
        except KisError as e:
            log.info("취소 불가(%s): %s", odno, e)
            return False

    def orders_today(self) -> dict:
        """당일 주문·체결 내역. {주문번호: {code side qty filled avg remain cancelled}}."""
        today = datetime.now().strftime("%Y%m%d")
        params = {
            "CANO": self.cano, "ACNT_PRDT_CD": self.prdt, "INQR_STRT_DT": today, "INQR_END_DT": today,
            "SLL_BUY_DVSN_CD": "00", "PDNO": "", "CCLD_DVSN": "00", "INQR_DVSN": "00", "INQR_DVSN_3": "00",
            "ORD_GNO_BRNO": "", "ODNO": "", "INQR_DVSN_1": "", "CTX_AREA_FK100": "", "CTX_AREA_NK100": "",
        }
        out, cont = {}, ""
        for _ in range(20):
            d = self._call("GET", "/uapi/domestic-stock/v1/trading/inquire-daily-ccld",
                           self._tr("TTTC0081R"), params, tr_cont=cont)
            for r in d.get("output1", []):
                qty, filled = int(float(r.get("ord_qty") or 0)), int(float(r.get("tot_ccld_qty") or 0))
                out[str(r["odno"]).lstrip("0")] = {
                    "code": r["pdno"], "side": "sell" if r.get("sll_buy_dvsn_cd") == "01" else "buy",
                    "qty": qty, "filled": filled, "avg": float(r.get("avg_prvs") or 0),
                    "remain": int(float(r.get("rmn_qty") or 0)), "cancelled": r.get("cncl_yn") == "Y",
                }
            if d.get("_tr_cont") not in ("M", "F"):
                break
            params["CTX_AREA_FK100"] = d.get("ctx_area_fk100", "")
            params["CTX_AREA_NK100"] = d.get("ctx_area_nk100", "")
            cont = "N"
        return out

    def balance(self) -> dict:
        """잔고. {cash: 주문가능 예수금(D+2), equity: 총평가금액, holdings: {code: {qty, avg}}}."""
        params = {
            "CANO": self.cano, "ACNT_PRDT_CD": self.prdt, "AFHR_FLPR_YN": "N", "OFL_YN": "",
            "INQR_DVSN": "02", "UNPR_DVSN": "01", "FUND_STTL_ICLD_YN": "N", "FNCG_AMT_AUTO_RDPT_YN": "N",
            "PRCS_DVSN": "00", "CTX_AREA_FK100": "", "CTX_AREA_NK100": "",
        }
        holdings, cont, summary = {}, "", {}
        for _ in range(20):
            d = self._call("GET", "/uapi/domestic-stock/v1/trading/inquire-balance",
                           self._tr("TTTC8434R"), params, tr_cont=cont)
            for r in d.get("output1", []):
                q = int(float(r.get("hldg_qty") or 0))
                if q > 0:
                    holdings[r["pdno"]] = {"qty": q, "avg": float(r.get("pchs_avg_pric") or 0),
                                           "name": r.get("prdt_name", "")}
            o2 = d.get("output2") or [{}]
            summary = o2[0] if isinstance(o2, list) else o2
            if d.get("_tr_cont") not in ("M", "F"):
                break
            params["CTX_AREA_FK100"] = d.get("ctx_area_fk100", "")
            params["CTX_AREA_NK100"] = d.get("ctx_area_nk100", "")
            cont = "N"
        return {
            "cash": float(summary.get("prvs_rcdl_excc_amt") or summary.get("dnca_tot_amt") or 0),
            "equity": float(summary.get("tot_evlu_amt") or 0),
            "holdings": holdings,
        }
