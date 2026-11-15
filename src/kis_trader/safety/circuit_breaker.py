"""매매 중단(halt) 관리.

HALT 는 data/HALT 파일로 영속화한다. 컨테이너가 재시작되어도 유지되며,
원인을 확인한 뒤 사용자가 파일을 직접 삭제해야 재개된다.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)
KST = ZoneInfo("Asia/Seoul")


class CircuitBreaker:
    def __init__(self, data_dir: Path, max_consecutive_errors: int,
                 notify: Callable[[str], None] = lambda msg: None):
        self._path = data_dir / "HALT"
        self._max = max_consecutive_errors
        self._notify = notify
        self._errors = 0

    @property
    def halted(self) -> bool:
        return self._path.exists()

    def reason(self) -> str:
        try:
            return json.loads(self._path.read_text(encoding="utf-8")).get("reason", "")
        except (OSError, ValueError):
            return "(HALT 파일을 읽을 수 없음)"

    def halt(self, reason: str) -> None:
        if self.halted:
            return
        payload = {"ts": datetime.now(KST).isoformat(), "reason": reason}
        try:
            self._path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        except OSError:
            log.exception("HALT 파일 기록 실패")
        log.error("매매 중단: %s", reason, extra={"event": "halt"})
        self._notify(f"🛑 매매 중단: {reason}\n확인 후 data/HALT 파일을 삭제하면 재개됩니다.")

    def record_success(self) -> None:
        self._errors = 0

    def record_error(self, what: str) -> None:
        self._errors += 1
        log.warning("연속 오류 %d/%d: %s", self._errors, self._max, what)
        if self._errors >= self._max:
            self.halt(f"연속 오류 {self._errors}회 ({what})")

    def record_fatal(self, what: str) -> None:
        """인증 실패, 주문 결과 불명 등 즉시 중단 사유."""
        self.halt(what)
