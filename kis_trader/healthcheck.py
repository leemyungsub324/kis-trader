"""컨테이너 헬스체크: 메인 루프가 남기는 heartbeat 파일이 최근 것인지만 확인한다 (네트워크/비밀값 불필요)."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path


def main() -> int:
    data_dir = Path(os.environ.get("DATA_DIR", "/app/data"))
    max_age = float(os.environ.get("HEARTBEAT_MAX_AGE_SEC", "300"))
    try:
        ts = float((data_dir / "heartbeat").read_text().strip())
    except (OSError, ValueError):
        print("heartbeat 없음")
        return 1
    age = time.time() - ts
    if age > max_age:
        print(f"heartbeat 오래됨: {age:.0f}초")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
