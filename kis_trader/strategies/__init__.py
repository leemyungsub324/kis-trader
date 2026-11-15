from .base import Strategy
from .breakout import Breakout
from .pullback import Pullback
from .reversal import Reversal
from .surge import Surge
from .vb import VolBreakout

REGISTRY = {"reversal": Reversal, "surge": Surge, "breakout": Breakout, "pullback": Pullback, "vb": VolBreakout}
DEFAULT = ("reversal", "surge")   # 검증에서 기대값이 양(+)이었던 전략만 기본 사용


def build(cfg: dict | None = None) -> list[Strategy]:
    """설정 dict({이름: {파라미터}})로 전략 목록 생성. enabled: false면 제외."""
    cfg = cfg if cfg is not None else {k: {} for k in DEFAULT}
    out = []
    for name, params in cfg.items():
        params = dict(params or {})
        if not params.pop("enabled", True):
            continue
        for k, v in params.items():
            if isinstance(v, list):
                params[k] = tuple(v)
        out.append(REGISTRY[name](**params))
    return out
