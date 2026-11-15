"""KIS REST 클라이언트: 호출 간격 제한, 응답 검증, 재시도."""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Callable

import httpx

from ..config import Settings
from .auth import TokenManager
from .errors import KisApiError, KisAuthError, KisNetworkError

log = logging.getLogger(__name__)

RATE_LIMIT_CODE = "EGW00201"  # 초당 거래건수 초과
# 토큰 만료/무효 코드: 공식 오류코드표 확인 전 잠정값 (README 미확정 항목)
AUTH_CODES = {"EGW00121", "EGW00123"}


@dataclass
class KisResponse:
    body: dict[str, Any]
    tr_cont: str = ""


class KisClient:
    def __init__(
        self,
        settings: Settings,
        http: httpx.Client,
        tokens: TokenManager,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        self._s = settings
        self._http = http
        self._tokens = tokens
        self._sleep = sleep
        self._mono = monotonic
        self._last_call = 0.0

    def _throttle(self) -> None:
        gap = self._s.api_min_interval_sec - (self._mono() - self._last_call)
        if gap > 0:
            self._sleep(gap)
        self._last_call = self._mono()

    def _headers(self, tr_id: str, tr_cont: str) -> dict[str, str]:
        return {
            "content-type": "application/json; charset=utf-8",
            "authorization": f"Bearer {self._tokens.get_token()}",
            "appkey": self._s.app_key.get_secret_value(),
            "appsecret": self._s.app_secret.get_secret_value(),
            "tr_id": tr_id,
            "custtype": "P",
            "tr_cont": tr_cont,
        }

    def call(
        self,
        method: str,
        path: str,
        tr_id: str,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
        tr_cont: str = "",
        retry_network: bool = True,
    ) -> KisResponse:
        url = f"{self._s.base_url}{path}"
        net_attempts = 3 if retry_network else 1
        rate_attempts = 4
        net_try = 0
        rate_try = 0
        while True:
            self._throttle()
            try:
                resp = self._http.request(
                    method, url, headers=self._headers(tr_id, tr_cont),
                    params=params, json=body, timeout=10,
                )
            except httpx.HTTPError as e:
                net_try += 1
                if net_try >= net_attempts:
                    raise KisNetworkError(f"{path} 네트워크 오류: {type(e).__name__}") from e
                self._sleep(2 ** net_try)
                continue

            data = self._parse(resp, path)
            msg_cd = str(data.get("msg_cd", ""))

            if msg_cd == RATE_LIMIT_CODE:
                rate_try += 1
                if rate_try >= rate_attempts:
                    raise KisApiError(f"{path} 호출 제한(EGW00201) 반복", msg_cd, resp.status_code)
                delay = 2 ** (rate_try - 1)
                log.warning("호출 제한 감지, %s초 후 재시도", delay, extra={"path": path})
                self._sleep(delay)
                continue

            if resp.status_code in (401, 403) or msg_cd in AUTH_CODES:
                self._tokens.invalidate()
                raise KisAuthError(f"{path} 인증 오류 HTTP {resp.status_code} {msg_cd} {data.get('msg1', '')}".strip())

            if resp.status_code != 200:
                raise KisApiError(f"{path} HTTP {resp.status_code} {msg_cd} {data.get('msg1', '')}".strip(),
                                  msg_cd, resp.status_code)
            if data.get("rt_cd") != "0":
                raise KisApiError(f"{path} 실패 rt_cd={data.get('rt_cd')} {msg_cd} {data.get('msg1', '')}".strip(),
                                  msg_cd, resp.status_code)
            return KisResponse(body=data, tr_cont=resp.headers.get("tr_cont", ""))

    @staticmethod
    def _parse(resp: httpx.Response, path: str) -> dict[str, Any]:
        try:
            data = resp.json()
        except ValueError:
            raise KisApiError(f"{path} 응답이 JSON 이 아님 (HTTP {resp.status_code})", "", resp.status_code)
        if not isinstance(data, dict):
            raise KisApiError(f"{path} 응답 형식 이상", "", resp.status_code)
        return data
