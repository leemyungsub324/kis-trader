# syntax=docker/dockerfile:1
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# tzdata: TZ=Asia/Seoul 이 실제로 적용되도록 설치 (v2 는 datetime.now() 를 KST 로 가정)
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/*

# 호스트 사용자(ms, uid 1000)와 같은 UID 로 실행 -> 볼륨 권한 문제 방지
RUN groupadd --gid 1000 trader \
    && useradd --uid 1000 --gid 1000 --no-create-home --shell /usr/sbin/nologin trader

WORKDIR /app
COPY requirements.txt ./
RUN pip install -r requirements.txt
COPY kis_trader ./kis_trader

# --- 테스트용 단계: docker build --target test . ---
FROM base AS test
COPY tests ./tests
RUN python -m pytest tests -q

# --- 실행용 단계 (기본) ---
FROM base AS runtime
RUN mkdir -p /app/data /app/logs && chown -R trader:trader /app/data /app/logs
USER trader
ENTRYPOINT ["python", "-m", "kis_trader"]
# 실제 명령(run --dry-run 등)은 docker-compose.yml 의 command 에서 정한다
CMD ["run", "--dry-run"]
