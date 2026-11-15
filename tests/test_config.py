import pytest
from pydantic import ValidationError

from kis_trader.config import PAPER_BASE_URL, ConfigError, Settings


def test_defaults_are_paper_and_dry_run(monkeypatch):
    for k in ("TRADING_MODE", "DRY_RUN"):
        monkeypatch.delenv(k, raising=False)
    s = Settings()
    assert s.trading_mode == "paper" and s.dry_run is True
    assert s.base_url == PAPER_BASE_URL and "vts" in s.base_url


def test_invalid_mode_rejected(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "prod")
    with pytest.raises(ValidationError):
        Settings()


def _live_env(monkeypatch, confirm):
    monkeypatch.setenv("TRADING_MODE", "live")
    monkeypatch.setenv("KIS_LIVE_APP_KEY", "k" * 20)
    monkeypatch.setenv("KIS_LIVE_APP_SECRET", "s" * 20)
    monkeypatch.setenv("KIS_LIVE_ACCOUNT_NO", "12345678")
    monkeypatch.setenv("LIVE_CONFIRM", confirm)


def test_live_requires_confirm_phrase(monkeypatch):
    for bad in ("", "LIVE", "live-5678", "LIVE-0000"):
        _live_env(monkeypatch, bad)
        with pytest.raises(ConfigError):
            Settings().require_credentials()


def test_live_ok_with_confirm_and_separate_resources(monkeypatch):
    _live_env(monkeypatch, "LIVE-5678")
    s = Settings()
    s.require_credentials()
    assert s.is_live and "vts" not in s.base_url
    assert s.token_filename == "token_live.json" and s.db_filename == "state_live.db"
    assert s.dry_run is True  # 실전에서도 기본은 DRY_RUN


def test_live_ignores_paper_keys(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "live")
    monkeypatch.setenv("KIS_PAPER_APP_KEY", "p" * 20)
    monkeypatch.delenv("KIS_LIVE_APP_KEY", raising=False)
    with pytest.raises(ConfigError):
        Settings().require_credentials()


def test_missing_credentials(monkeypatch):
    monkeypatch.delenv("KIS_PAPER_APP_KEY", raising=False)
    with pytest.raises(ConfigError):
        Settings().require_credentials()


def test_interval_floor(monkeypatch):
    monkeypatch.setenv("API_MIN_INTERVAL_SEC", "0.1")
    with pytest.raises(ValidationError):
        Settings()
