"""최근 약 6개월 누적 거래대금 상위 종목 계산 (보통주 후보군 기준).

실행(컨테이너 안의 설정/토큰 사용, 비밀값은 출력하지 않음):
  sudo docker compose run --rm -T --no-deps kis-trader python - < scripts/top_turnover.py

방법: 후보군(대형주 고정 목록 + 가능하면 KIS 거래금액순위 상위)의 일봉 'acml_tr_pbmn'(누적 거래대금)을
      최근 183일 구간에서 합산해 정렬한다. 후보군에 없는 종목은 순위에 반영되지 않는다.
"""
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from kis_trader.config import Settings
from kis_trader.kis.auth import TokenManager
from kis_trader.kis.client import KisClient
from kis_trader.kis.errors import KisError

KST = ZoneInfo("Asia/Seoul")
BASE = "/uapi/domestic-stock/v1/quotations"

CANDIDATES = {
    "005930": "삼성전자", "000660": "SK하이닉스", "373220": "LG에너지솔루션", "207940": "삼성바이오로직스",
    "005380": "현대차", "000270": "기아", "012450": "한화에어로스페이스", "034020": "두산에너빌리티",
    "042660": "한화오션", "329180": "HD현대중공업", "105560": "KB금융", "035420": "NAVER",
    "035720": "카카오", "068270": "셀트리온", "005490": "POSCO홀딩스", "006400": "삼성SDI",
    "051910": "LG화학", "009150": "삼성전기", "028260": "삼성물산", "086790": "하나금융지주",
    "055550": "신한지주", "012330": "현대모비스", "010130": "고려아연", "247540": "에코프로비엠",
    "086520": "에코프로", "196170": "알테오젠", "034730": "SK", "096770": "SK이노베이션",
    "003670": "포스코퓨처엠", "267260": "HD현대일렉트릭", "298040": "효성중공업", "000150": "두산",
    "064350": "현대로템", "079550": "LIG넥스원", "011200": "HMM", "323410": "카카오뱅크",
}


def main() -> int:
    s = Settings()
    s.require_credentials()
    http = httpx.Client()
    client = KisClient(s, http, TokenManager(s, http, s.data_dir / s.token_filename))
    names = dict(CANDIDATES)

    # 1) 오늘 거래금액순위 상위를 후보에 추가 (모의 미지원이면 건너뜀)
    try:
        r = client.call("GET", f"{BASE}/volume-rank", "FHPST01710000", params={
            "FID_COND_MRKT_DIV_CODE": "J", "FID_COND_SCR_DIV_CODE": "20171", "FID_INPUT_ISCD": "0000",
            "FID_DIV_CLS_CODE": "1", "FID_BLNG_CLS_CODE": "3", "FID_TRGT_CLS_CODE": "111111111",
            "FID_TRGT_EXLS_CLS_CODE": "0110111101", "FID_INPUT_PRICE_1": "", "FID_INPUT_PRICE_2": "",
            "FID_VOL_CNT": "", "FID_INPUT_DATE_1": "",
        })
        added = 0
        for row in r.body.get("output") or []:
            code, nm = row.get("mksc_shrn_iscd"), row.get("hts_kor_isnm")
            if code and code not in names:
                names[code] = nm or code
                added += 1
        print(f"[순위 API] 후보 {added}개 추가", file=sys.stderr)
    except KisError as e:
        print(f"[순위 API 사용 불가, 고정 후보만 사용] {e}", file=sys.stderr)

    # 2) 종목별 최근 183일 거래대금 합산 (100건 제한 때문에 두 구간으로 나눠 조회)
    today = datetime.now(KST).date()
    ranges = [(today - timedelta(days=183), today - timedelta(days=92)),
              (today - timedelta(days=91), today)]
    totals: dict[str, tuple[float, int]] = {}
    for i, (code, nm) in enumerate(names.items(), 1):
        by_day: dict[str, float] = {}
        try:
            for a, b in ranges:
                r = client.call("GET", f"{BASE}/inquire-daily-itemchartprice", "FHKST03010100", params={
                    "FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code,
                    "FID_INPUT_DATE_1": a.strftime("%Y%m%d"), "FID_INPUT_DATE_2": b.strftime("%Y%m%d"),
                    "FID_PERIOD_DIV_CODE": "D", "FID_ORG_ADJ_PRC": "0",
                })
                for row in r.body.get("output2") or []:
                    if row.get("stck_bsop_date"):
                        by_day[row["stck_bsop_date"]] = float(row.get("acml_tr_pbmn") or 0)
        except KisError as e:
            print(f"  {code} {nm} 조회 실패: {e}", file=sys.stderr)
            continue
        totals[code] = (sum(by_day.values()), len(by_day))
        print(f"  ({i}/{len(names)}) {code} {nm} {len(by_day)}일", file=sys.stderr)

    print(f"\n최근 약 6개월 누적 거래대금 상위 (기준일 {today}, 후보 {len(totals)}종목)")
    print(f"{'순위':>3} {'코드':<7} {'종목':<14} {'누적(조원)':>10} {'일평균(억원)':>12} {'일수':>5}")
    for rank, (code, (tot, n)) in enumerate(sorted(totals.items(), key=lambda kv: -kv[1][0])[:15], 1):
        avg = tot / n if n else 0
        print(f"{rank:>3} {code:<7} {names[code]:<14} {tot / 1e12:>10.1f} {avg / 1e8:>12.0f} {n:>5}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
