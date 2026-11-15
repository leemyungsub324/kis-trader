"""v1 에서 이식한 운영 기능: 텔레그램 알림, 로그 마스킹, heartbeat/헬스체크, 일일 요약."""
import json
import logging
import time

import requests

from kis_trader import healthcheck
from kis_trader.engine import Engine
from kis_trader.logging_setup import MaskingJsonFormatter, mask, register_secrets
from kis_trader.notify import Notifier, NullNotifier
from kis_trader.strategies import Reversal, Surge


class Rec:
    def __init__(self, status=200, exc=None):
        self.sent, self.status, self.exc = [], status, exc

    def post(self, url, json=None, timeout=None):
        if self.exc:
            raise self.exc
        self.sent.append((url, json))
        return type("R", (), {"status_code": self.status})()


class Memo:
    enabled = True

    def __init__(self):
        self.msgs = []

    def send(self, text):
        self.msgs.append(text)


class Bal:
    def balance(self):
        return {"equity": 5_000_000, "cash": 5_000_000, "holdings": {}}


def engine(tmp_path, notify=None):
    return Engine(Bal(), [Reversal(), Surge()], {}, data_dir=str(tmp_path), notify=notify)


def test_notifier_disabled_without_token():
    h = Rec()
    n = Notifier("", "123", session=h)
    n.send("x")
    assert not n.enabled and h.sent == []


def test_notifier_masks_secrets_and_swallows_errors():
    register_secrets(["APPKEY-SECRET-1234"])
    h = Rec()
    Notifier("tok", "42", session=h).send("key=APPKEY-SECRET-1234")
    assert h.sent[0][1] == {"chat_id": "42", "text": "key=***"}
    Notifier("tok", "42", session=Rec(exc=requests.ConnectionError("https://api.telegram.org/bottok/x"))).send("y")
    Notifier("tok", "42", session=Rec(status=500)).send("z")      # 예외가 밖으로 나오지 않아야 한다


def test_notifier_from_env(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    assert Notifier.from_cfg({}).enabled
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN")
    monkeypatch.delenv("TELEGRAM_CHAT_ID")
    assert Notifier.from_cfg({"telegram": {"bot_token": "t", "chat_id": 1}}).enabled
    assert not Notifier.from_cfg({}).enabled


def test_log_formatter_masks_bearer_and_registered_secret():
    register_secrets(["MYSECRETVALUE"])
    rec = logging.LogRecord("x", logging.INFO, "", 0, "auth Bearer abcdefghijklmnop secret=MYSECRETVALUE", (), None)
    out = json.loads(MaskingJsonFormatter().format(rec))["msg"]
    assert "abcdefghijklmnop" not in out and "MYSECRETVALUE" not in out
    assert mask('"appsecret": "zzzz"') == '"appsecret": "***"'


def test_engine_default_notifier_is_null(tmp_path):
    assert isinstance(engine(tmp_path).notify, NullNotifier)


def test_trade_sends_fill_notification(tmp_path):
    m = Memo()
    e = engine(tmp_path, m)
    e._trade("reversal", "005930", "buy", 3, 70000, "entry")
    assert m.msgs == ["[체결] reversal 005930 매수 3주 @70,000 (entry)"]


def test_heartbeat_and_healthcheck(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    assert healthcheck.main() == 1                 # 아직 없음
    engine(tmp_path)._beat()
    assert healthcheck.main() == 0
    (tmp_path / "heartbeat").write_text(str(time.time() - 4000))
    monkeypatch.setenv("HEARTBEAT_MAX_AGE_SEC", "1800")
    assert healthcheck.main() == 1


def test_daily_summary(tmp_path):
    m = Memo()
    e = engine(tmp_path, m)
    e._trade("surge", "000660", "sell", 2, 100000, "시가청산")
    m.msgs.clear()
    e.st["positions"] = {"005930": {"strat": "reversal", "qty": 3}}
    e._daily_summary()
    assert len(m.msgs) == 1
    t = m.msgs[0]
    assert "평가금액 5,000,000원" in t and "005930(reversal 3주)" in t and "오늘 체결 1건" in t
