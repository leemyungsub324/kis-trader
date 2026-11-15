"""텔레그램 알림 (v1 notify/telegram.py 이식). 토큰이 없으면 아무것도 하지 않는다.

전송 실패는 매매에 영향을 주지 않는다. 설정은 환경변수 TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID
(docker compose 의 env_file .env) 또는 config.yaml 의 telegram: {bot_token, chat_id}.
"""
from __future__ import annotations

import logging
import os

import requests

from .logging_setup import mask

log = logging.getLogger("notify")


class Notifier:
    def __init__(self, token: str = "", chat_id: str = "", session=None):
        self._token, self._chat = token or "", str(chat_id or "")
        self._http = session or requests.Session()

    @property
    def enabled(self) -> bool:
        return bool(self._token and self._chat)

    @classmethod
    def from_cfg(cls, cfg: dict) -> "Notifier":
        t = cfg.get("telegram") or {}
        return cls(os.environ.get("TELEGRAM_BOT_TOKEN") or t.get("bot_token", ""),
                   os.environ.get("TELEGRAM_CHAT_ID") or t.get("chat_id", ""))

    def send(self, text: str) -> None:
        if not self.enabled:
            return
        try:
            r = self._http.post(f"https://api.telegram.org/bot{self._token}/sendMessage",
                                json={"chat_id": self._chat, "text": mask(text)[:3500]}, timeout=10)
            if r.status_code != 200:
                log.warning("텔레그램 전송 실패 HTTP %s", r.status_code)
        except requests.RequestException as e:
            # 예외 문자열에 URL(토큰)이 들어갈 수 있어 타입만 기록한다.
            log.warning("텔레그램 전송 오류: %s", type(e).__name__)


class NullNotifier:
    enabled = False

    def send(self, text: str) -> None:
        pass
