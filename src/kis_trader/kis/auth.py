"""접근토큰 발급/캐시/만료 처리.

- 토큰은 data/token_paper.json 에 권한 600 으로 저장하고, 유효하면 재사용한다.
- KIS 는 토큰 발급을 1분에 1회로 제한하므로, 발급 시도 시각을 기록해 쿨다운을 지킨다.
- 응답 필드(access_token, expires_in, access_token_token_expired)는 공식 예제/문서 기준이며
  실제 호출로 한 번 더 확인이 필요하다 (README '미확정 항목' 참고).
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

import httpx

from ..config import Settings
from .errors import KisAuthError, KisNetworkError

log = logging.getLogger(__name__)
KST = ZoneInfo("Asia/Seoul")
ISSUE_COOLDOWN_SEC = 65  # 공식: 1분당 1회 -> 여유 5초
EXPIRY_MARGIN_SEC = 300  # 만료 5분 전부터 재발급 대상
DEFAULT_TTL_SEC = 23 * 3600


class TokenManager:
    def __init__(
        self,
        settings: Settings,
        http: httpx.Client,
        cache_path: Path,
        now: Callable[[], float] = time.time,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._s = settings
        self._http = http
        self._path = cache_path
        self._now = now
        self._sleep = sleep
        self._token: str | None = None
        self._expires_at: float = 0.0
        self._last_attempt: float = 0.0
        self._load()

    # ── 캐시 ──
    def _load(self) -> None:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            self._token = data.get("access_token")
            self._expires_at = float(data.get("expires_at", 0))
            self._last_attempt = float(data.get("last_attempt", 0))
        except (OSError, ValueError):
            pass

    def _save(self) -> None:
        payload = json.dumps(
            {"access_token": self._token, "expires_at": self._expires_at, "last_attempt": self._last_attempt}
        )
        tmp = self._path.with_suffix(".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(payload)
        os.replace(tmp, self._path)

    def _valid(self) -> bool:
        return bool(self._token) and self._now() < self._expires_at - EXPIRY_MARGIN_SEC

    def invalidate(self) -> None:
        self._token = None
        self._expires_at = 0.0
        self._save()

    # ── 공개 API ──
    def get_token(self) -> str:
        if self._valid():
            return self._token  # type: ignore[return-value]
        return self._issue()

    # ── 발급 ──
    def _issue(self) -> str:
        wait = self._last_attempt + ISSUE_COOLDOWN_SEC - self._now()
        if wait > 0:
            log.warning("토큰 재발급 쿨다운 대기 %.0f초", wait)
            self._sleep(wait)

        self._last_attempt = self._now()
        self._save()  # 실패해도 시도 시각을 남겨 재시작 루프에서 호출 제한을 지킨다

        body = {
            "grant_type": "client_credentials",
            "appkey": self._s.app_key.get_secret_value(),
            "appsecret": self._s.app_secret.get_secret_value(),
        }
        try:
            resp = self._http.post(f"{self._s.base_url}/oauth2/tokenP", json=body, timeout=10)
        except httpx.HTTPError as e:
            raise KisNetworkError(f"토큰 발급 네트워크 오류: {type(e).__name__}") from e

        try:
            data = resp.json()
        except ValueError:
            raise KisAuthError(f"토큰 발급 응답이 JSON 이 아님 (HTTP {resp.status_code})")

        token = data.get("access_token") if isinstance(data, dict) else None
        if resp.status_code != 200 or not token:
            code = data.get("error_code", "") if isinstance(data, dict) else ""
            desc = data.get("error_description", "") if isinstance(data, dict) else ""
            raise KisAuthError(f"토큰 발급 실패 HTTP {resp.status_code} {code} {desc}".strip())

        self._token = token
        self._expires_at = self._parse_expiry(data)
        self._save()
        log.info("접근토큰 발급 완료", extra={"expires_at": self._expires_at})
        return token

    def _parse_expiry(self, data: dict) -> float:
        now = self._now()
        expires_in = data.get("expires_in")
        if isinstance(expires_in, (int, float)) and expires_in > 0:
            return now + float(expires_in)
        text = data.get("access_token_token_expired")
        if isinstance(text, str):
            try:
                return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST).timestamp()
            except ValueError:
                pass
        return now + DEFAULT_TTL_SEC
