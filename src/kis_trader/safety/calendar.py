"""장 운영 시간(KST 평일 정규장)과 휴장일 확인. 확인 불가면 주문하지 않는다(fail-closed)."""
from __future__ import annotations

import logging
import time
from datetime import date, datetime
from typing import Callable
from zoneinfo import ZoneInfo

from ..config import Settings
from ..state.db import Database
from ..kis.errors import KisApiError, KisError

# 모의투자 서버가 휴장일 API 를 지원하지 않을 때 돌려주는 코드 (실제 응답: "모의투자 TR 이 아닙니다")
PAPER_UNSUPPORTED_CODE = "EGW02006"

log = logging.getLogger(__name__)
KST = ZoneInfo("Asia/Seoul")
RETRY_AFTER_FAIL_SEC = 300


class MarketCalendar:
    def __init__(
        self,
        settings: Settings,
        db: Database,
        fetcher: Callable[[date], dict[date, bool]] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        self._s = settings
        self._db = db
        self._fetch = fetcher
        self._mono = monotonic
        self._extra = settings.extra_holiday_dates
        self._last_fail: float | None = None
        self._paper_unsupported = False

    def is_open_day(self, d: date) -> bool | None:
        """True=개장일, False=휴장일, None=확인 불가.

        모의투자 서버는 휴장일 API 를 지원하지 않는다(EGW02006). 이때 모의 모드에서만
        '평일이고 EXTRA_HOLIDAYS 가 아니면 개장'으로 간주한다. 공휴일은 EXTRA_HOLIDAYS 로 직접 보강할 것.
        실전 모드는 확인 불가이면 항상 None(주문 거부)이다.
        """
        if d.weekday() >= 5 or d in self._extra:
            return False
        if self._paper_unsupported and not self._s.is_live:
            return True
        cached = self._db.get_holiday(d)
        if cached is not None:
            return cached
        if self._fetch is None:
            return None
        if self._last_fail is not None and self._mono() - self._last_fail < RETRY_AFTER_FAIL_SEC:
            return None
        try:
            days = self._fetch(d)
        except KisApiError as e:
            if e.msg_cd == PAPER_UNSUPPORTED_CODE and not self._s.is_live:
                self._paper_unsupported = True
                log.warning("모의투자는 휴장일 API 미지원: 평일 기준으로 판단합니다. 공휴일은 EXTRA_HOLIDAYS 로 직접 지정하세요.")
                return True
            self._last_fail = self._mono()
            log.warning("휴장일 조회 실패: %s", e)
            return None
        except KisError as e:
            self._last_fail = self._mono()
            log.warning("휴장일 조회 실패: %s", e)
            return None
        self._last_fail = None
        for day, is_open in days.items():
            self._db.set_holiday(day, is_open)
        return days.get(d)

    def can_order(self, now: datetime) -> tuple[bool, str]:
        now = now.astimezone(KST)
        open_day = self.is_open_day(now.date())
        if open_day is None:
            return False, "휴장 여부 확인 불가"
        if not open_day:
            return False, "휴장일"
        t = now.time()
        if not (self._s.order_start_time <= t < self._s.order_end_time):
            return False, f"주문 허용 시간 아님 ({self._s.order_start}~{self._s.order_end})"
        return True, "ok"
