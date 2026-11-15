"""텔레그램 알림. 토큰이 없으면 아무것도 하지 않는다. 전송 실패는 매매에 영향을 주지 않는다."""
from __future__ import annotations

import logging

import httpx

from ..config import Settings
from ..logging_setup import mask

log = logging.getLogger(__name__)


class Notifier:
    def __init__(self, settings: Settings, http: httpx.Client | None = None):
        self._enabled = settings.telegram_enabled
        self._token = settings.telegram_bot_token.get_secret_value()
        self._chat = settings.telegram_chat_id.get_secret_value()
        self._http = http or httpx.Client()

    def send(self, text: str) -> None:
        if not self._enabled:
            return
        try:
            r = self._http.post(
                f"https://api.telegram.org/bot{self._token}/sendMessage",
                json={"chat_id": self._chat, "text": mask(text)[:3500]},
                timeout=10,
            )
            if r.status_code != 200:
                log.warning("텔레그램 전송 실패 HTTP %s", r.status_code)
        except httpx.HTTPError as e:
            # 예외 문자열에 URL(토큰)이 들어갈 수 있어 타입만 기록한다.
            log.warning("텔레그램 전송 오류: %s", type(e).__name__)
