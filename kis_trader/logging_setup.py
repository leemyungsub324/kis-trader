"""구조화(JSON) 로그 + 비밀값 마스킹 + 파일 로테이션."""
from __future__ import annotations

import json
import logging
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

MASK = "***"
# 비밀값을 모르더라도 패턴으로 한 번 더 가린다.
_PATTERNS = [
    re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._\-]{10,}"),
    re.compile(r"(?i)(\"?(?:access_token|appkey|appsecret|app_secret|approval_key)\"?\s*[:=]\s*\"?)[^\s\",}]+"),
    re.compile(r"(bot)\d{6,}:[A-Za-z0-9_\-]{20,}"),
]

_secrets: list[str] = []


def register_secrets(values: list[str]) -> None:
    for v in values:
        if v and len(v) >= 4 and v not in _secrets:
            _secrets.append(v)
    _secrets.sort(key=len, reverse=True)


def mask(text: str) -> str:
    for s in _secrets:
        if s in text:
            text = text.replace(s, MASK)
    for p in _PATTERNS:
        text = p.sub(lambda m: m.group(1) + MASK, text)
    return text


_RESERVED = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {"message", "asctime"}


class MaskingJsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for k, v in record.__dict__.items():
            if k not in _RESERVED and not k.startswith("_"):
                payload[k] = v
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        line = json.dumps(payload, ensure_ascii=False, default=str)
        return mask(line)


def setup_logging(log_dir: Path, level: str = "INFO") -> None:
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level.upper())
    fmt = MaskingJsonFormatter()

    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    root.addHandler(sh)

    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(
            log_dir / "kis-trader.log", maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError as e:  # 파일 로그 실패해도 stdout 은 유지
        root.warning("파일 로그를 열 수 없습니다: %s", e)

    # requests(urllib3) 는 DEBUG 에서 요청 URL(텔레그램 토큰 포함)을 찍으므로 낮춘다.
    for noisy in ("urllib3", "requests"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
