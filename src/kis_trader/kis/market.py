"""시세/일봉/휴장일 조회. 모두 모의투자 기준 TR ID.

TR ID 출처: 공식 예제(koreainvestment/open-trading-api examples_llm). 시세는 실전/모의 동일.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from ..config import Settings
from .client import KisClient
from .errors import KisApiError
from .tr import tr_ids

KST = ZoneInfo("Asia/Seoul")
BASE = "/uapi/domestic-stock/v1/quotations"


@dataclass(frozen=True)
class Candle:
    day: date
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass(frozen=True)
class MinuteBar:
    day: date
    hhmmss: str  # 체결 시각 (봉 종료 시각)
    open: float
    high: float
    low: float
    close: float
    volume: int


@dataclass(frozen=True)
class Quote:
    """현재가 조회 한 번으로 얻는 당일 시세 (inquire-price)."""
    symbol: str
    price: int
    open: int
    high: int
    low: int
    prev_close: int
    volume: int      # 당일 누적 거래량
    turnover: int    # 당일 누적 거래대금(원)
    upper_limit: int  # 상한가 (없으면 0)

    @property
    def change_pct(self) -> float:
        return (self.price / self.prev_close - 1) * 100 if self.prev_close > 0 else 0.0


def _minus_one_minute(hhmmss: str) -> str:
    t = datetime.strptime(hhmmss, "%H%M%S") - timedelta(minutes=1)
    return t.strftime("%H%M%S")


class Market:
    def __init__(self, client: KisClient, settings: Settings):
        self._c = client
        self._tr = tr_ids(settings)

    def get_price(self, symbol: str) -> int:
        r = self._c.call(
            "GET", f"{BASE}/inquire-price", "FHKST01010100",
            params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": symbol},
        )
        out = r.body.get("output") or {}
        try:
            price = int(out["stck_prpr"])
        except (KeyError, ValueError, TypeError):
            raise KisApiError(f"현재가 응답 형식 이상: {symbol}")
        if price <= 0:
            raise KisApiError(f"현재가가 0 이하: {symbol}")
        return price

    def get_quote(self, symbol: str) -> Quote:
        r = self._c.call(
            "GET", f"{BASE}/inquire-price", "FHKST01010100",
            params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": symbol},
        )
        out = r.body.get("output") or {}

        def num(key: str) -> int:
            try:
                return int(float(out.get(key) or 0))
            except (ValueError, TypeError):
                return 0

        price = num("stck_prpr")
        if price <= 0:
            raise KisApiError(f"현재가 응답 형식 이상: {symbol}")
        return Quote(symbol, price, num("stck_oprc"), num("stck_hgpr"), num("stck_lwpr"), num("stck_sdpr"),
                     num("acml_vol"), num("acml_tr_pbmn"), num("stck_mxpr"))

    def get_daily_candles(self, symbol: str, calendar_days: int = 170) -> list[Candle]:
        """수정주가 일봉 (오래된 -> 최신). 호출당 최대 100건."""
        today = datetime.now(KST).date()
        r = self._c.call(
            "GET", f"{BASE}/inquire-daily-itemchartprice", "FHKST03010100",
            params={
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": symbol,
                "FID_INPUT_DATE_1": (today - timedelta(days=calendar_days)).strftime("%Y%m%d"),
                "FID_INPUT_DATE_2": today.strftime("%Y%m%d"),
                "FID_PERIOD_DIV_CODE": "D",
                "FID_ORG_ADJ_PRC": "0",  # 0 = 수정주가
            },
        )
        rows = r.body.get("output2") or []
        candles: list[Candle] = []
        for row in rows:
            if not row.get("stck_bsop_date"):
                continue  # 빈 행
            try:
                candles.append(Candle(
                    day=datetime.strptime(row["stck_bsop_date"], "%Y%m%d").date(),
                    open=float(row["stck_oprc"]), high=float(row["stck_hgpr"]),
                    low=float(row["stck_lwpr"]), close=float(row["stck_clpr"]),
                    volume=int(row.get("acml_vol") or 0),
                ))
            except (KeyError, ValueError) as e:
                raise KisApiError(f"일봉 응답 형식 이상: {symbol} {type(e).__name__}")
        candles.sort(key=lambda c: c.day)
        return candles

    def get_minute_bars(self, symbol: str, day: date, until: str = "153000",
                        stop: str = "090000", today: bool = False) -> list["MinuteBar"]:
        """1분봉 (오래된 -> 최신). until(HHMMSS) 부터 거꾸로 stop 까지 페이지 조회.

        과거일: 주식일별분봉조회 FHKST03010230 (호출당 120건). 당일: FHKST03010200 (호출당 30건).
        둘 다 모의투자 서버에서 동작 확인 (2026-11-13).
        """
        bars: dict[str, MinuteBar] = {}
        cursor = until
        for _ in range(40):  # 안전 상한
            if today:
                r = self._c.call("GET", f"{BASE}/inquire-time-itemchartprice", "FHKST03010200", params={
                    "FID_ETC_CLS_CODE": "", "FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": symbol,
                    "FID_INPUT_HOUR_1": cursor, "FID_PW_DATA_INCU_YN": "Y"})
            else:
                r = self._c.call("GET", f"{BASE}/inquire-time-dailychartprice", "FHKST03010230", params={
                    "FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": symbol, "FID_INPUT_HOUR_1": cursor,
                    "FID_INPUT_DATE_1": day.strftime("%Y%m%d"), "FID_PW_DATA_INCU_YN": "Y",
                    "FID_FAKE_TICK_INCU_YN": ""})
            rows = r.body.get("output2") or []
            earliest = None
            for row in rows:
                d, t = row.get("stck_bsop_date"), row.get("stck_cntg_hour")
                if not d or not t or d != day.strftime("%Y%m%d"):
                    continue
                try:
                    bars[t] = MinuteBar(day, t, float(row["stck_oprc"]), float(row["stck_hgpr"]),
                                        float(row["stck_lwpr"]), float(row["stck_prpr"]),
                                        int(row.get("cntg_vol") or 0))
                except (KeyError, ValueError) as e:
                    raise KisApiError(f"분봉 응답 형식 이상: {symbol} {type(e).__name__}")
                earliest = t if earliest is None or t < earliest else earliest
            if earliest is None or earliest <= stop or earliest >= cursor:
                break
            cursor = _minus_one_minute(earliest)
        return [bars[t] for t in sorted(bars) if t >= stop]

    def fetch_holidays(self, base: date) -> dict[date, bool]:
        """base 이후 일자별 개장 여부 (True=개장). 하루 1회 수준으로만 호출할 것(공식 주석)."""
        # 공식 kis_auth._url_fetch 는 모의 모드에서 tr_id 첫 글자 T/J/C 를 V 로 바꾼다
        # (CTCA0903R -> VTCA0903R). 모의 서버가 어느 쪽을 받는지는 미확인이라 순서대로 시도한다.
        params = {"BASS_DT": base.strftime("%Y%m%d"), "CTX_AREA_NK": "", "CTX_AREA_FK": ""}
        r = None
        for i, tr in enumerate(self._tr.holiday):
            try:
                r = self._c.call("GET", f"{BASE}/chk-holiday", tr, params=params)
                break
            except KisApiError:
                if i == len(self._tr.holiday) - 1:
                    raise
        out: dict[date, bool] = {}
        for row in r.body.get("output") or []:
            try:
                d = datetime.strptime(row["bass_dt"], "%Y%m%d").date()
                out[d] = row["opnd_yn"] == "Y"
            except (KeyError, ValueError):
                continue
        if not out:
            raise KisApiError("휴장일 응답이 비어 있음")
        return out
