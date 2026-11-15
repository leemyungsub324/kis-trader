# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# tzdata: TZ=Asia/Seoul 이 실제로 적용되도록 설치
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

# 호스트 사용자(ms, uid 1000)와 같은 UID 로 실행 -> 볼륨 권한 문제 방지
RUN groupadd --gid 1000 trader \
    && useradd --uid 1000 --gid 1000 --no-create-home --shell /usr/sbin/nologin trader

WORKDIR /app
COPY pyproject.toml ./
COPY src ./src
RUN pip install .

# --- 테스트용 단계: docker build --target test . ---
FROM base AS test
COPY tests ./tests
RUN pip install "pytest>=8" && python -m pytest -q

# --- 실행용 단계 (기본) ---
FROM base AS runtime
RUN mkdir -p /app/data /app/logs && chown -R trader:trader /app/data /app/logs
USER trader
CMD ["python", "-m", "kis_trader.main"]
