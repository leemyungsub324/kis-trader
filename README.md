# kis-trader

한국투자증권(KIS) Open API 자동매매 뼈대 (모의투자/실전투자). 기본 전략은 **아무 주문도 내지 않는 관찰 전략**이다.
기본은 `TRADING_MODE=paper` + `DRY_RUN=true`. 실전(`live`)은 `LIVE_CONFIRM` 확인 문구가 맞아야만 시작된다.

## 실전 모드 사용 순서 (권장)

1. `.env`: `TRADING_MODE=live`, `KIS_LIVE_*` 3개 값, `LIVE_CONFIRM=LIVE-<계좌 뒤 4자리>`, **`DRY_RUN=true` 유지**.
2. 실행 후 로그로 실전 계좌의 잔고/보유/시세 조회가 맞는지 확인 (이 단계는 관찰 전략이라 주문이 나가지 않음).
3. 매매 규칙 전략을 붙이고도 `DRY_RUN=true` 로 며칠 돌려 `[DRY-RUN]` 주문 로그가 의도와 같은지 확인.
4. 그다음에만 한도(`MAX_*`, `DAILY_MAX_LOSS_KRW`, `STOP_LOSS_PCT`)를 소액으로 설정하고 `DRY_RUN=false`.
- 모의/실전은 토큰 파일과 DB 가 분리된다 (`token_live.json`, `state_live.db`). HALT 파일은 공용이다.
- 실전 주문 전송 코드는 모의 계좌로도 검증되지 않았다. 실전에서 처음 전송되는 주문이 첫 실거래 검증이 된다.

## 구조

```
docker-compose.yml  Dockerfile  .env.example  pyproject.toml
data/   SQLite(state.db), 토큰 캐시(token_paper.json, 권한 600), heartbeat, HALT   (볼륨)
logs/   kis-trader.log (5MB x 5 로테이션)                                         (볼륨)
src/kis_trader/
  config.py            환경변수 설정/검증 (모의 전용, 한도값)
  logging_setup.py     JSON 로그 + 비밀값 마스킹 + 로테이션
  indicators.py        SMA/EMA/RSI(Wilder)/일목균형표 (마감된 일봉, 룩어헤드 방지)
  kis/                 auth(토큰) client(호출간격·검증) market account orders
  safety/              guard(단일 관문) calendar(장시간·휴장) circuit_breaker(HALT)
  state/db.py          SQLite 주문/이벤트/휴장일/일일 시작 평가금액
  recovery.py          재시작 시 잔고·보유·미체결 복원
  strategy/            base(인터페이스) observe(기본: 주문 없음) composite(MA+일목+RSI 종합점수)
  notify/telegram.py   체결/오류/일일 요약 알림
  demo_hourly.py       1시간봉 하루 데모 (DEMO_HOURLY=true)
  healthcheck.py  main.py
tests/                 pytest (Docker 빌드의 test 단계에서 실행)
```

## 실행

```bash
cd ~/stacks/kis-trader
cp .env.example .env && chmod 600 .env     # 값은 직접 편집 (앱키/시크릿/계좌번호/텔레그램)
sudo docker build --target test .          # 단위 테스트
sudo docker compose up -d --build
sudo docker compose logs -f
sudo docker stats --no-stream kis-trader
sudo docker inspect -f '{{.State.Health.Status}}' kis-trader
```

## 환경변수

| 이름 | 기본값 | 설명 |
|---|---|---|
| TRADING_MODE | paper | `paper` 또는 `live`. live 는 LIVE_CONFIRM 필요 |
| LIVE_CONFIRM | 빈 값 | live 일 때 `LIVE-` + 실전 계좌 뒤 4자리. 불일치 시 시작 거부 |
| DRY_RUN | true | true 면 주문을 전송하지 않고 DB/로그에만 기록 (live 에서도 기본 true) |
| KIS_PAPER_APP_KEY / APP_SECRET | (비밀) | 모의투자 앱키/시크릿 |
| KIS_PAPER_ACCOUNT_NO / PRODUCT_CODE | (비밀) / 01 | 모의 계좌 앞 8자리 / 뒤 2자리 |
| KIS_LIVE_APP_KEY / APP_SECRET | (비밀) | 실전투자 앱키/시크릿 (live 일 때만 사용) |
| KIS_LIVE_ACCOUNT_NO / PRODUCT_CODE | (비밀) / 01 | 실전 계좌 앞 8자리 / 뒤 2자리 |
| TELEGRAM_BOT_TOKEN / CHAT_ID | 빈 값 | 비우면 알림 비활성 |
| WATCHLIST | 005930 | 쉼표 구분 6자리 종목코드. 매수는 이 목록만 허용 |
| MAX_ORDER_AMOUNT_KRW / MAX_ORDER_QTY | 500000 / 100 | 1회 매수 한도 |
| MAX_POSITION_PER_SYMBOL_KRW / MAX_TOTAL_POSITION_KRW | 1000000 / 3000000 | 포지션 한도 |
| DAILY_MAX_LOSS_KRW | 100000 | 당일 시작 평가금액 대비 손실 한도, 도달 시 HALT |
| STOP_LOSS_PCT | 5.0 | 평균단가 대비 하락률 손절(전략과 무관하게 먼저 평가) |
| MAX_CONSECUTIVE_ERRORS | 3 | 연속 API 오류 시 HALT |
| STRATEGY | observe | `observe`(주문 없음) / `composite`(종합점수 매매, 점수표는 strategy/composite.py 상단) |
| STRATEGY_BUY_SCORE / STRATEGY_SELL_SCORE | 70 / 40 | 매수 기준 점수 / 보유 종목 전량 매도 기준 점수 |
| STRATEGY_BUY_KRW | 0 | 1회 매수 예산 (0 = MAX_ORDER_AMOUNT_KRW). 85점 이상 100%, 그 외 50% |
| STRATEGY_ENTRY_AFTER | 09:10 | 이 시각 이후에만 신규 매수 |
| DAILY_MAX_BUY_KRW | 0 | 하루 매수 주문 누적 한도 (0 = 제한 없음, 매도 후 재매수도 누적) |
| DEMO_HOURLY / DEMO_DATE | false / 빈 값 | 1시간봉 데모(demo_hourly.py). 08:00 이후 최근 13거래일 1분봉으로 준비. DEMO_DATE(하루 또는 `시작~종료` 기간) 날짜에만 매시 판단·주문. 15:00 이후 신규 매수 없음, 15:15 보유분 전량 청산 |
| POLL_INTERVAL_SEC | 60 | 주기 |
| API_MIN_INTERVAL_SEC | 1.0 | 호출 간 최소 간격 (0.5 미만 거부) |
| ORDER_START / ORDER_END | 09:00 / 15:20 | 주문 허용 시간(KST) |
| EXTRA_HOLIDAYS | 빈 값 | 수동 휴장일(YYYY-MM-DD, 쉼표) |
| DAILY_SUMMARY_TIME | 15:40 | 일일 요약 전송 시각 |

## 안전장치 동작

- **청산 우선 순서(2026-11-13 사고 후 변경):** 매 주기에 잔고 -> 손절/장마감 청산 -> 체결조회 -> 시세/전략 순으로 처리한다.
  체결조회(`inquire-daily-ccld`) 실패는 HALT 사유가 아니다(경고 로그, 10회 연속이면 텔레그램 알림 1회). 11/13 에 이 조회가
  일시적으로 `MCA00124` 를 내 연속 오류 3회로 HALT 가 걸리고 15:15 청산이 막혔다.
- 점수가 기준 근처인데 매수가 막히면 `buy_blocked` 로그에 사유(구름/SMA60/RSI)가 남는다.

- 전략은 `OrderIntent` 만 반환한다. 모든 주문은 `Guard.check()` 를 통과해야 전송된다.
- 주문 거부 사유: HALT 상태, 장 시간 밖/주말/휴장일, **휴장 여부 확인 불가**, 일일 손실 한도, 수량·금액·예수금·종목/전체 포지션 한도, 감시종목 외 매수, 보유 초과 매도.
- 매도(손절 포함)에는 금액 한도를 적용하지 않는다(청산이 막히지 않도록).
- **HALT**: `data/HALT` 파일로 영속화. 인증 실패, 일일 손실 도달, 연속 오류, 주문 결과 불명 시 생성되고 텔레그램으로 알린다. 모든 주문(손절 포함)이 막힌다. 원인 확인 후 `rm data/HALT` 하면 재개된다.
- 네트워크 오류(Wi-Fi 끊김 등)는 HALT 사유가 아니며 다음 주기에 재시도한다. 단, 주문 전송 중 네트워크 오류는 중복 주문 방지를 위해 HALT 한다.
- 시작 시 잔고/보유/당일 주문을 먼저 조회해 복원하고(실패 시 5분마다 재시도, 그동안 매매 안 함) 그 뒤에만 루프를 시작한다.

## 컨테이너

`restart: unless-stopped`, `TZ=Asia/Seoul`, 비root(uid 1000), read_only 루트, cap_drop ALL, no-new-privileges, 메모리 256MB 제한, 열린 포트 없음, 헬스체크(heartbeat 파일 신선도).

## 공식 저장소 대조 결과 (koreainvestment/open-trading-api, 2026-10-28 커밋 3a9be59 를 clone 해서 직접 확인)

**저장소 코드로 확인됨**
- 도메인, 토큰 발급(`/oauth2/tokenP`, 응답 `access_token`/`expires_in`/`access_token_token_expired`), 토큰 유효 1일, "6시간 이내 재발급은 기존 토큰 반환, 발급 시 알림톡 발송"(그래서 파일 캐시 재사용).
- TR ID: 시세 `FHKST01010100`/`FHKST03010100`, 잔고 `VTTC8434R`, 주문 `VTTC0012U`/`VTTC0011U`, 체결조회 `VTTC0081R`. 모의 변환 규칙(첫 글자 T/J/C -> V).
- 주문 본문 키(대문자, `SLL_TYPE`/`CNDT_PRIC` 포함), `ORD_DVSN` 00 지정가 / 01 시장가(시장가는 `ORD_UNPR=0`), 응답 `ODNO`.
- 잔고 필드(`hldg_qty`, `pchs_avg_pric`, `prpr`, `dnca_tot_amt`, `tot_evlu_amt`), 체결조회 필드(`odno`, `tot_ccld_qty`, `avg_prvs`, `rmn_qty`, `cncl_yn`, `sll_buy_dvsn_cd`), 일봉(`stck_bsop_date`, `stck_clpr`, `stck_oprc`, `stck_hgpr`, `stck_lwpr`, `acml_vol`), 휴장일(`opnd_yn` = 개장일, 주문 가능 여부 판단용).
- 잔고 조회 페이지: 모의 20건/실전 50건, 연속조회는 `tr_cont` M/F 와 `ctx_area_fk100`/`nk100`.
- 공식 strategy_builder 의 호출 간격: **모의 0.5초, 실전 0.05초**. 이 프로젝트는 모의 기본 1.0초(최소 0.5초 허용).
- 휴장일 API 는 "가급적 하루 1회 호출" 권고(원장 연동). 하루 단위 캐시.

**여전히 미확정 (실제 호출로 확인 필요)**
1. 모의 서버의 휴장일 API 지원과 TR ID: 공식 `_url_fetch` 규칙대로면 `VTCA0903R`. 코드는 `VTCA0903R` 후 `CTCA0903R` 순으로 시도하고, 둘 다 실패하면 주문하지 않는다(fail-closed). 필요 시 `EXTRA_HOLIDAYS` 로 보강.
2. 미체결 조회: `inquire-psbl-rvsecncl`(`TTTC0084R`)은 모의 TR ID 가 문서에 없다. 대신 당일 체결조회(`CCLD_DVSN=00` 전체)의 잔량으로 판단한다. (공식 strategy_builder 는 `VTTC8001R` + `CCLD_DVSN=01` 을 "미체결"로 쓰는데 신규 문서는 01=체결, 02=미체결이라 서로 어긋남 -> 전체 조회 후 `rmn_qty` 로 직접 판단하는 쪽이 안전.)
3. 인증 오류 코드(`EGW00121/123`)는 잠정값. 모의 호출 한도의 정확한 공식 수치는 저장소에 없음(README: "모의는 한도가 낮다"). `EGW00201` 은 백오프 재시도 처리.
4. 일목균형표는 일봉 최소 약 78개가 필요(조회 한 번에 최대 100건).
5. VKOSPI: 저장소에 언급 없음. 데이터 공급자 미정이라 미구현. `Snapshot.vkospi` 는 항상 None.

**공식 샘플 코드의 알려진 문제 (그대로 쓰지 않음)**: `reAuth()` 의 `timedelta.seconds` 비교 오류, `changeTREnv()` 의 `_smartSleep` 전역 미반영(실제 모의 대기 0.1초), `inquire_daily_ccld` 재귀 호출 인자 순서 불일치.

## 주의

- 주문 전송 코드는 모의 계좌로 검증 전이다. `DRY_RUN=true` 로 먼저 충분히 관찰할 것.
- 실전 전환은 별도 지시 시에만 구현한다.
