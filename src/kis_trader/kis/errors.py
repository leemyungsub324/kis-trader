class KisError(Exception):
    """KIS 호출 관련 기본 예외."""


class KisAuthError(KisError):
    """인증 실패 (즉시 매매 중단 대상)."""


class KisApiError(KisError):
    """HTTP 200 이어도 rt_cd != '0' 이거나 비정상 응답."""

    def __init__(self, message: str, msg_cd: str = "", http_status: int = 200):
        super().__init__(message)
        self.msg_cd = msg_cd
        self.http_status = http_status


class KisNetworkError(KisError):
    """네트워크/타임아웃. 일시적일 수 있어 매매 중단 대신 재시도 대상."""
