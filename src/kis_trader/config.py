"""환경변수 기반 설정. 현재는 모의투자(paper)만 허용한다."""
from __future__ import annotations

import re
from datetime import date, time
from pathlib import Path

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# 공식 예제 kis_devlp.yaml 의 prod / vps 값
PAPER_BASE_URL = "https://openapivts.koreainvestment.com:29443"
LIVE_BASE_URL = "https://openapi.koreainvestment.com:9443"


class ConfigError(RuntimeError):
    pass


def _parse_hhmm(value: str) -> time:
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", value.strip())
    if not m:
        raise ValueError(f"HH:MM 형식이어야 합니다: {value!r}")
    return time(int(m.group(1)), int(m.group(2)))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore", case_sensitive=False)

    # 모드
    trading_mode: str = "paper"
    dry_run: bool = True

    # KIS 모의투자 인증
    kis_paper_app_key: SecretStr = SecretStr("")
    kis_paper_app_secret: SecretStr = SecretStr("")
    kis_paper_account_no: SecretStr = SecretStr("")
    kis_paper_account_product_code: str = "01"

    # KIS 실전투자 인증 (TRADING_MODE=live 일 때만 사용)
    kis_live_app_key: SecretStr = SecretStr("")
    kis_live_app_secret: SecretStr = SecretStr("")
    kis_live_account_no: SecretStr = SecretStr("")
    kis_live_account_product_code: str = "01"
    # 실전 확인 문구: "LIVE-" + 실전 계좌번호 뒤 4자리 (예: LIVE-1234). 일치하지 않으면 시작 거부.
    live_confirm: str = ""

    # 텔레그램
    telegram_bot_token: SecretStr = SecretStr("")
    telegram_chat_id: SecretStr = SecretStr("")

    # 종목
    watchlist: str = "005930"

    # 안전장치
    max_order_amount_krw: int = 500_000
    max_order_qty: int = 100
    max_position_per_symbol_krw: int = 1_000_000
    max_total_position_krw: int = 3_000_000
    daily_max_loss_krw: int = 100_000
    stop_loss_pct: float = 5.0
    max_consecutive_errors: int = 3
    # 하루 매수 주문 누적 금액 한도(원). 0 이면 제한 없음. 매도 후 재매수도 누적된다.
    daily_max_buy_krw: int = 0

    # 1시간봉 데모: DEMO_HOURLY=true 이면 1시간봉으로 매시 판단.
    # DEMO_DATE 는 'YYYY-MM-DD' (하루) 또는 'YYYY-MM-DD~YYYY-MM-DD' (기간, 양끝 포함). 그 날짜에만 전략 주문.
    demo_hourly: bool = False
    demo_date: str = ""

    # 전략: observe(주문 없음) | composite(이동평균+일목+RSI 종합점수) | multi(카테고리별, strategy/multi.py)
    strategy: str = "observe"

    # multi 전략: 카테고리(surge 급등 단타, vbreak 변동성 돌파, swing 추세 스윙, dip 저점 매수)
    multi_sleeves: str = "surge,vbreak,swing,dip"   # 켤 카테고리
    multi_weights: str = "30,20,35,15"              # MAX_TOTAL_POSITION_KRW 대비 예산 비중(%) - 위 순서
    multi_slots: str = "3,2,3,2"                    # 카테고리별 동시 보유 종목 수
    universe_min_turnover_krw: int = 1_000_000_000  # 전일 거래대금이 이보다 작은 종목은 스캔 제외
    universe_min_price: int = 1_000
    universe_max_symbols: int = 350                 # 스캔 종목 수 상한 (거래대금 큰 순). 종목당 API 1회
    entry_tick_min: int = 3                         # 지정가 매수: 현재가에서 몇 틱 아래에 걸지 (최소~최대)
    entry_tick_max: int = 10
    breakout_offset_ticks: int = 3                  # surge·vbreak (돌파 매수) 는 고정 틱
    sweep_per_tick: int = 15                        # 주기마다 유니버스에서 돌아가며 조회할 종목 수 (급등 탐지)
    strategy_buy_score: int = 70
    strategy_sell_score: int = 40
    strategy_buy_krw: int = 0  # 1회 매수 예산. 0 이면 MAX_ORDER_AMOUNT_KRW
    strategy_entry_after: str = "09:10"

    # 동작
    poll_interval_sec: int = 60
    api_min_interval_sec: float = 1.0
    order_start: str = "09:00"
    order_end: str = "15:20"
    extra_holidays: str = ""
    daily_summary_time: str = "15:40"
    log_level: str = "INFO"

    data_dir: Path = Path("/app/data")
    log_dir: Path = Path("/app/logs")

    # ── 검증 ──
    @field_validator("trading_mode")
    @classmethod
    def _mode(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in ("paper", "live"):
            raise ValueError("TRADING_MODE 는 'paper' 또는 'live' 여야 합니다")
        return v

    @field_validator("strategy")
    @classmethod
    def _strategy(cls, v: str) -> str:
        v = v.strip().lower()
        if v not in ("observe", "composite", "multi"):
            raise ValueError("STRATEGY 는 'observe', 'composite', 'multi' 중 하나여야 합니다")
        return v

    @field_validator("strategy_buy_score", "strategy_sell_score")
    @classmethod
    def _score_range(cls, v: int) -> int:
        if not (0 <= v <= 100):
            raise ValueError("점수는 0~100 이어야 합니다")
        return v

    @field_validator("strategy_buy_krw", "daily_max_buy_krw")
    @classmethod
    def _non_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError("0 이상이어야 합니다")
        return v

    @field_validator(
        "max_order_amount_krw", "max_order_qty", "max_position_per_symbol_krw",
        "max_total_position_krw", "daily_max_loss_krw", "max_consecutive_errors",
        "poll_interval_sec",
    )
    @classmethod
    def _positive_int(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("0 보다 커야 합니다")
        return v

    @field_validator("stop_loss_pct")
    @classmethod
    def _stop_loss_range(cls, v: float) -> float:
        if not (0 < v < 100):
            raise ValueError("STOP_LOSS_PCT 는 0 초과 100 미만이어야 합니다")
        return v

    @field_validator("api_min_interval_sec")
    @classmethod
    def _interval_floor(cls, v: float) -> float:
        if v < 0.5:
            raise ValueError("API_MIN_INTERVAL_SEC 는 0.5 이상이어야 합니다 (모의투자 호출 제한 보호)")
        return v

    # ── 편의 속성 ──
    @property
    def is_live(self) -> bool:
        return self.trading_mode == "live"

    @property
    def mode_label(self) -> str:
        return "실전투자" if self.is_live else "모의투자"

    @property
    def base_url(self) -> str:
        return LIVE_BASE_URL if self.is_live else PAPER_BASE_URL

    @property
    def app_key(self) -> SecretStr:
        return self.kis_live_app_key if self.is_live else self.kis_paper_app_key

    @property
    def app_secret(self) -> SecretStr:
        return self.kis_live_app_secret if self.is_live else self.kis_paper_app_secret

    @property
    def account_no(self) -> SecretStr:
        return self.kis_live_account_no if self.is_live else self.kis_paper_account_no

    @property
    def product_code(self) -> str:
        return self.kis_live_account_product_code if self.is_live else self.kis_paper_account_product_code

    @property
    def token_filename(self) -> str:
        return f"token_{self.trading_mode}.json"

    @property
    def db_filename(self) -> str:
        return f"state_{self.trading_mode}.db"  # 모의/실전 기록이 섞이지 않도록 분리

    @property
    def symbols(self) -> list[str]:
        return [s.strip() for s in self.watchlist.split(",") if s.strip()]

    @property
    def order_start_time(self) -> time:
        return _parse_hhmm(self.order_start)

    @property
    def order_end_time(self) -> time:
        return _parse_hhmm(self.order_end)

    @property
    def demo_range(self) -> tuple[date, date] | None:
        raw = self.demo_date.strip()
        if not raw:
            return None
        parts = [p.strip() for p in raw.split("~")]
        if len(parts) > 2 or not all(parts):
            raise ValueError(f"DEMO_DATE 형식 오류: {raw!r}")
        start, end = date.fromisoformat(parts[0]), date.fromisoformat(parts[-1])
        if end < start:
            raise ValueError(f"DEMO_DATE 종료일이 시작일보다 빠릅니다: {raw!r}")
        return start, end

    def is_demo_day(self, d: date) -> bool:
        r = self.demo_range
        return r is not None and r[0] <= d <= r[1]

    @property
    def sleeve_config(self) -> dict[str, tuple[float, int]]:
        """카테고리 -> (예산 비중 %, 동시 보유 수). 형식 오류는 ValueError."""
        names = [x.strip().lower() for x in self.multi_sleeves.split(",") if x.strip()]
        weights = [float(x) for x in self.multi_weights.split(",") if x.strip()]
        slots = [int(x) for x in self.multi_slots.split(",") if x.strip()]
        known = ("surge", "vbreak", "swing", "dip")
        if not names or any(n not in known for n in names) or len(set(names)) != len(names):
            raise ValueError(f"MULTI_SLEEVES 는 {', '.join(known)} 중에서 중복 없이 지정해야 합니다")
        if len(weights) != len(names) or len(slots) != len(names):
            raise ValueError("MULTI_WEIGHTS / MULTI_SLOTS 의 개수가 MULTI_SLEEVES 와 같아야 합니다")
        if any(w <= 0 for w in weights) or sum(weights) > 100.0001 or any(n <= 0 for n in slots):
            raise ValueError("MULTI_WEIGHTS 는 양수이고 합이 100 이하, MULTI_SLOTS 는 1 이상이어야 합니다")
        return {n: (w, k) for n, w, k in zip(names, weights, slots)}

    @property
    def strategy_entry_after_time(self) -> time:
        return _parse_hhmm(self.strategy_entry_after)

    @property
    def summary_time(self) -> time:
        return _parse_hhmm(self.daily_summary_time)

    @property
    def extra_holiday_dates(self) -> set[date]:
        out: set[date] = set()
        for part in self.extra_holidays.split(","):
            part = part.strip()
            if part:
                out.add(date.fromisoformat(part))
        return out

    @property
    def telegram_enabled(self) -> bool:
        return bool(self.telegram_bot_token.get_secret_value() and self.telegram_chat_id.get_secret_value())

    def secret_values(self) -> list[str]:
        """로그 마스킹 대상 비밀값 목록."""
        vals = [
            self.kis_paper_app_key, self.kis_paper_app_secret, self.kis_paper_account_no,
            self.kis_live_app_key, self.kis_live_app_secret, self.kis_live_account_no,
            self.telegram_bot_token, self.telegram_chat_id,
        ]
        return [v.get_secret_value() for v in vals if v.get_secret_value()]

    def require_credentials(self) -> None:
        p = "LIVE" if self.is_live else "PAPER"
        missing = [
            name for name, v in (
                (f"KIS_{p}_APP_KEY", self.app_key),
                (f"KIS_{p}_APP_SECRET", self.app_secret),
                (f"KIS_{p}_ACCOUNT_NO", self.account_no),
            ) if not v.get_secret_value()
        ]
        if missing:
            raise ConfigError(f".env 에 값이 비어 있습니다: {', '.join(missing)}")
        acct = self.account_no.get_secret_value()
        if not re.fullmatch(r"\d{8}", acct):
            raise ConfigError(f"KIS_{p}_ACCOUNT_NO 는 숫자 8자리(계좌번호 앞부분)여야 합니다")
        if not re.fullmatch(r"\d{2}", self.product_code):
            raise ConfigError(f"KIS_{p}_ACCOUNT_PRODUCT_CODE 는 숫자 2자리여야 합니다")
        if self.is_live and self.live_confirm != f"LIVE-{acct[-4:]}":
            raise ConfigError(
                "실전 모드 확인 문구가 맞지 않습니다. .env 의 LIVE_CONFIRM 을 "
                "'LIVE-' + 실전 계좌번호 뒤 4자리로 설정하세요."
            )
        try:
            rng = self.demo_range
        except ValueError as e:
            raise ConfigError(str(e)) from e
        if self.demo_hourly and rng is None:
            raise ConfigError("DEMO_HOURLY=true 이면 DEMO_DATE(YYYY-MM-DD 또는 YYYY-MM-DD~YYYY-MM-DD) 를 지정해야 합니다")
        if self.strategy == "multi":
            if self.demo_hourly:
                raise ConfigError("STRATEGY=multi 는 DEMO_HOURLY=true 와 함께 쓸 수 없습니다 (DEMO_HOURLY=false 로 설정)")
            try:
                self.sleeve_config
            except ValueError as e:
                raise ConfigError(str(e)) from e
            if not (0 <= self.entry_tick_min <= self.entry_tick_max <= 30) or not (0 <= self.breakout_offset_ticks <= 30):
                raise ConfigError("ENTRY_TICK_MIN <= ENTRY_TICK_MAX, 틱 값은 0~30 이어야 합니다")
        for s in self.symbols:
            if not re.fullmatch(r"[0-9A-Z]{6}", s):
                raise ConfigError(f"WATCHLIST 종목코드 형식 오류: {s!r}")
