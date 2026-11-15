import json
import logging

from kis_trader.logging_setup import MaskingJsonFormatter, mask, register_secrets


def test_registered_secret_masked():
    register_secrets(["SUPERSECRETVALUE123"])
    assert "SUPERSECRETVALUE123" not in mask("key=SUPERSECRETVALUE123 end")


def test_bearer_and_telegram_patterns():
    out = mask("authorization: Bearer abcdefghijklmnop1234 url=https://api.telegram.org/bot123456789:AAAAAAAAAAAAAAAAAAAAAAAAAAAA/send")
    assert "abcdefghijklmnop1234" not in out
    assert "AAAAAAAAAAAAAAAAAAAA" not in out


def test_formatter_masks_extra_and_exception():
    register_secrets(["ACCT12345678"])
    rec = logging.LogRecord("t", logging.INFO, "f", 1, "hello %s", ("ACCT12345678",), None)
    rec.detail = "ACCT12345678"
    line = MaskingJsonFormatter().format(rec)
    assert "ACCT12345678" not in line
    assert json.loads(line)["level"] == "INFO"
