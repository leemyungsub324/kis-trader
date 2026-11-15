import json

import httpx

from kis_trader.config import Settings
from kis_trader.kis.auth import TokenManager


def make(tmp_path, handler, clock):
    s = Settings(kis_paper_app_key="k" * 20, kis_paper_app_secret="s" * 20, data_dir=tmp_path)
    http = httpx.Client(transport=httpx.MockTransport(handler))
    sleeps = []
    tm = TokenManager(s, http, tmp_path / "tok.json", now=lambda: clock[0], sleep=sleeps.append)
    return tm, sleeps


def test_token_cached_and_reused(tmp_path):
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(200, json={"access_token": "TOKEN-ABCDEFGHIJ", "expires_in": 86400})

    clock = [1_000_000.0]
    tm, _ = make(tmp_path, handler, clock)
    assert tm.get_token() == "TOKEN-ABCDEFGHIJ"
    assert tm.get_token() == "TOKEN-ABCDEFGHIJ"
    assert len(calls) == 1
    assert oct((tmp_path / "tok.json").stat().st_mode & 0o777) == "0o600"

    # 새 인스턴스(재시작)도 파일 캐시를 재사용
    tm2, _ = make(tmp_path, handler, clock)
    assert tm2.get_token() == "TOKEN-ABCDEFGHIJ"
    assert len(calls) == 1


def test_expired_token_reissued_with_cooldown(tmp_path):
    n = {"i": 0}

    def handler(req):
        n["i"] += 1
        return httpx.Response(200, json={"access_token": f"TOKEN-{n['i']}-XXXXXXXX", "expires_in": 1000})

    clock = [1_000_000.0]
    tm, sleeps = make(tmp_path, handler, clock)
    tm.get_token()
    clock[0] += 900  # 만료 5분 전 구간 -> 재발급 대상, 그러나 직전 발급 후 65초 지남
    assert tm.get_token().startswith("TOKEN-2")
    assert sleeps == []
    tm.invalidate()
    clock[0] += 10  # 직전 발급 후 10초 -> 쿨다운(65초) 미경과, 대기해야 함
    tm.get_token()
    assert sleeps and sleeps[0] > 0


def test_issue_failure_raises(tmp_path):
    import pytest
    from kis_trader.kis.errors import KisAuthError

    def handler(req):
        return httpx.Response(403, json={"error_code": "EGW00103", "error_description": "invalid"})

    tm, _ = make(tmp_path, handler, [1_000_000.0])
    with pytest.raises(KisAuthError):
        tm.get_token()
