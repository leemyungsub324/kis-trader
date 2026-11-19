"""하루 매매 점검용 사실 모음. 표준 라이브러리만 사용(호스트 python3 로 바로 실행, API 호출 없음).

  python3 scripts/daily_report.py              # 로그에 남은 마지막 거래일
  python3 scripts/daily_report.py 2026-11-19   # 특정 날짜

읽는 것: logs/kis-trader.log(JSON 줄), data/trades.csv, data/state.json, data/daily/*.csv(장후 갱신 일봉)
판단은 하지 않고 숫자만 정리한다. 해석은 .claude/skills/daily-review 가 맡는다.
"""
from __future__ import annotations

import csv
import json
import os
import re
import sys
from collections import Counter, defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG = os.path.join(ROOT, "logs", "kis-trader.log")
DATA = os.path.join(ROOT, "data")
INDEX = "069500"

RE_ORDER = re.compile(r"주문 (\w+) (buy|sell) (\d{6}) (\d+)주 @(\S+) \[(\w+)\]")
RE_FILL = re.compile(r"체결 (\w+) (\d{6}) (buy|sell) (\d+)주 @(\d+) \((.*)\)")
RE_SCAN = re.compile(r"스캔 완료: 국면 (\w+), 후보 (\d+)건")


def pct(x):
    return "n/a" if x is None else f"{x * 100:+.2f}%"


def won(x):
    return f"{x:,.0f}"


def load_log():
    rows = []
    if not os.path.exists(LOG):
        return rows
    with open(LOG, encoding="utf-8") as f:
        for line in f:
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return rows


def load_daily(code):
    p = os.path.join(DATA, "daily", f"{code}.csv")
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as f:
        return [{k: (v if k == "date" else float(v)) for k, v in r.items()} for r in csv.DictReader(f)]


def names():
    p = os.path.join(DATA, "universe.csv")
    out = {}
    if os.path.exists(p):
        with open(p, encoding="utf-8") as f:
            r = csv.reader(f)
            next(r, None)
            for row in r:
                if len(row) > 1:
                    out[row[0].zfill(6)] = row[1]
    return out


def bar_on(bars, day):
    """day 의 일봉과 그 위치. 없으면 (None, -1)."""
    for i, b in enumerate(bars):
        if b["date"] == day:
            return b, i
    return None, -1


def feats(bars, i):
    """i 번째 날 기준 지표: 등락, 5일 수익률, 20일 고점 대비, 거래량 배수, RSI14, MA5/20/60."""
    if i < 0:
        return {}
    c = [b["close"] for b in bars[: i + 1]]
    v = [b["volume"] for b in bars[: i + 1]]
    f = {"close": c[-1], "chg": c[-1] / c[-2] - 1 if len(c) > 1 else None,
         "ret5": c[-1] / c[-6] - 1 if len(c) > 5 else None,
         "dd20": c[-1] / max(c[-20:]) - 1 if c else None,
         "vol_x": v[-1] / (sum(v[-21:-1]) / 20) if len(v) > 20 and sum(v[-21:-1]) else None}
    for n in (5, 20, 60):
        f[f"ma{n}"] = sum(c[-n:]) / n if len(c) >= n else None
    if len(c) > 15:
        g = [max(c[k] - c[k - 1], 0) for k in range(len(c) - 14, len(c))]
        l_ = [max(c[k - 1] - c[k], 0) for k in range(len(c) - 14, len(c))]
        f["rsi14"] = 100.0 if sum(l_) == 0 else 100 - 100 / (1 + sum(g) / sum(l_))
    # 최근 5일 안의 '악재성 폭락'(하루 -8% 이하 & 거래량 3배 이상)
    f["crash5"] = []
    for k in range(max(1, i - 4), i + 1):
        ck = bars[k]["close"] / bars[k - 1]["close"] - 1
        vk = bars[k]["volume"] / (sum(b["volume"] for b in bars[max(0, k - 20):k]) / max(1, min(20, k)))
        if ck <= -0.08 and vk >= 3:
            f["crash5"].append(f"{bars[k]['date']} {ck * 100:.1f}% 거래량{vk:.1f}배")
    return f


def regime(f):
    if not f.get("ma60"):
        return "NEUTRAL"
    if f["close"] > f["ma60"] and f["ma20"] > f["ma60"]:
        return "BULL"
    if f["close"] < f["ma60"] and f["ma20"] < f["ma60"]:
        return "BEAR"
    return "NEUTRAL"


def main():
    logs = load_log()
    days = sorted({r["ts"][:10] for r in logs if "ts" in r})
    day = sys.argv[1] if len(sys.argv) > 1 else (days[-1] if days else "")
    today = [r for r in logs if r.get("ts", "").startswith(day)]
    nm = names()
    label = lambda c: f"{c} {nm.get(c, '')}".strip()
    P = print

    P(f"# 매매 점검 자료 {day}")
    P(f"(로그 기록일: {', '.join(days[-7:])})")

    # ---------------------------------------------------------------- 1. 운영
    P("\n## 1. 운영 상태")
    starts = [r for r in today if r["msg"].startswith("시작")]
    P(f"- 프로그램 시작 {len(starts)}회: " + ("; ".join(f"{r['ts'][11:16]} {r['msg']}" for r in starts) or "없음(전날부터 계속 실행)"))
    lv = Counter(r["level"] for r in today)
    P(f"- 로그 수준별 건수: {dict(lv)}")
    issues = Counter()
    for r in today:
        if r["level"] in ("WARNING", "ERROR", "CRITICAL"):
            issues[(r["level"], re.sub(r"\d{6}", "######", r["msg"])[:160])] += 1
    for (lvl, m), n in issues.most_common(15):
        P(f"  - [{lvl}] ×{n} {m}")
    keys = ("휴장", "개장", "지연", "STOP", "손실 한도", "건너뜀", "진입 포기", "시간 초과", "종가 진입 후보", "브리핑")
    notes = [r for r in today if r["level"] == "INFO" and any(k in r["msg"] for k in keys)]
    brief = sum(1 for r in notes if "브리핑 전송" in r["msg"])
    P(f"- 시황 브리핑 전송 {brief}회")
    for r in notes:
        if "브리핑 전송" not in r["msg"]:
            P(f"  - {r['ts'][11:16]} {r['msg']}")
    scans = [RE_SCAN.search(r["msg"]) for r in today]
    scans = [m for m in scans if m]
    P("- 장후 스캔: " + (", ".join(f"국면 {m.group(1)} 후보 {m.group(2)}건" for m in scans) or "기록 없음(15:40 이후 미실행?)"))

    # ---------------------------------------------------------------- 2. 주문·체결
    P("\n## 2. 주문과 체결")
    orders, fills = defaultdict(list), defaultdict(list)
    for r in today:
        m = RE_ORDER.search(r["msg"])
        if m:
            orders[m.group(3)].append(dict(t=r["ts"][11:19], strat=m.group(1), side=m.group(2), qty=int(m.group(4)),
                                           px=m.group(5), kind=m.group(6), dry="[모의실행]" in r["msg"]))
        m = RE_FILL.search(r["msg"])
        if m:
            fills[m.group(2)].append(dict(t=r["ts"][11:19], strat=m.group(1), side=m.group(3), qty=int(m.group(4)),
                                          px=float(m.group(5)), why=m.group(6)))
    codes = sorted(set(orders) | set(fills))
    if not codes:
        P("- 오늘 주문·체결 없음")
    for c in codes:
        bars = load_daily(c)
        b, _ = bar_on(bars, day)
        ohlc = f"시{won(b['open'])} 고{won(b['high'])} 저{won(b['low'])} 종{won(b['close'])}" if b else "일봉 없음"
        P(f"\n### {label(c)} ({ohlc})")
        filled_at = Counter()
        for x in fills[c]:
            filled_at[(x["side"], int(x["px"]))] += x["qty"]
        for o in orders[c]:
            touched = ""
            if b and o["px"].isdigit():
                px = int(o["px"])
                got = filled_at[(o["side"], px)]
                if o["side"] == "buy":
                    hit = b["low"] < px                     # 1틱이라도 뚫렸으면 체결이 정상
                    touched = (" ← 저가 도달" if b["low"] <= px else f" ← 미도달(저가와 {(b['low'] / px - 1) * 100:.1f}% 차이)")
                else:
                    hit = b["high"] > px
                    touched = (" ← 고가 도달" if b["high"] >= px else f" ← 미도달(고가와 {(px / b['high'] - 1) * 100:.1f}% 차이)")
                if o["kind"] == "entry":
                    touched += f", 같은 가격 체결 {got}주"
                    if hit and got == 0:
                        touched += " ⚠ 가격이 뚫렸는데 미체결(체결 동기화·주문 상태 확인)"
            P(f"- 주문 {o['t']} {o['kind']} {o['side']} {o['qty']}주 @{o['px']}{touched}")
        for x in fills[c]:
            P(f"- 체결 {x['t']} {x['side']} {x['qty']}주 @{won(x['px'])} ({x['why']})")
        bq = sum(o["qty"] for o in orders[c] if o["side"] == "buy" and o["kind"] == "entry")
        fq = sum(x["qty"] for x in fills[c] if x["side"] == "buy")
        if bq:
            P(f"- 매수 체결률 {fq}/{bq}주 ({fq / bq:.0%})")

    # ---------------------------------------------------------------- 3. 손익
    P("\n## 3. 손익")
    trades = []
    tp = os.path.join(DATA, "trades.csv")
    if os.path.exists(tp):
        with open(tp, encoding="utf-8") as f:
            trades = list(csv.DictReader(f))
    cost = {"fee": 0.00015, "tax": 0.0020}
    pos = defaultdict(lambda: [0, 0.0])          # code -> [qty, 평단]
    realized = []
    for t in trades:
        if t["time"][:10] > day:
            break
        c, q, px = t["code"], int(t["qty"]), float(t["price"])
        p = pos[c]
        if t["side"] == "buy":
            p[1] = (p[0] * p[1] + q * px) / (p[0] + q)
            p[0] += q
        else:
            pnl = q * (px * (1 - cost["fee"] - cost["tax"]) - p[1] * (1 + cost["fee"]))
            if t["time"][:10] == day:
                realized.append((c, q, p[1], px, pnl, t["why"]))
            p[0] -= q
    if realized:
        for c, q, e, x, pnl, why in realized:
            P(f"- 실현 {label(c)} {q}주 평단 {won(e)} → {won(x)} ({x / e - 1:+.2%}) 손익 {won(pnl)}원 [{why}]")
        P(f"- 실현 합계 {won(sum(r[4] for r in realized))}원 (수수료·거래세 추정 반영)")
    else:
        P("- 오늘 실현 손익 없음(매도 체결 없음)")
    held = {c: p for c, p in pos.items() if p[0] > 0}
    tot_val = tot_cost = day_pnl = 0.0
    for c, (q, e) in sorted(held.items()):
        bars = load_daily(c)
        b, i = bar_on(bars, day)
        if not b:
            P(f"- 보유 {label(c)} {q}주 평단 {won(e)} (당일 일봉 없음)")
            continue
        prev = bars[i - 1]["close"] if i > 0 else b["close"]
        bought_today = sum(int(t["qty"]) for t in trades if t["code"] == c and t["side"] == "buy" and t["time"][:10] == day)
        tot_val += q * b["close"]
        tot_cost += q * e
        day_pnl += (q - bought_today) * (b["close"] - prev) + sum(
            int(t["qty"]) * (b["close"] - float(t["price"])) for t in trades
            if t["code"] == c and t["side"] == "buy" and t["time"][:10] == day)
        P(f"- 보유 {label(c)} {q}주 평단 {won(e)} 종가 {won(b['close'])} 평가 {b['close'] / e - 1:+.2%} "
          f"({won(q * (b['close'] - e))}원), 당일 {b['close'] / prev - 1:+.2%}")
    if held:
        P(f"- 보유 합계: 매입 {won(tot_cost)} → 평가 {won(tot_val)} ({won(tot_val - tot_cost)}원, "
          f"{(tot_val / tot_cost - 1) if tot_cost else 0:+.2%}), 오늘 하루 변동 {won(day_pnl)}원")
    st = {}
    if os.path.exists(os.path.join(DATA, "state.json")):
        with open(os.path.join(DATA, "state.json"), encoding="utf-8") as f:
            st = json.load(f)
    same = st.get("date") == day
    if not same:
        P(f"- (state.json 은 {st.get('date')} 기준이라 이 날짜의 엔진 상태 비교는 생략)")
    if same and st.get("equity0"):
        P(f"- 장 시작 계좌 평가액(equity0): {won(st['equity0'])}원 (state.json 기준일 {st.get('date')})")
    sp = st.get("positions", {}) if same else {}
    if sp:
        P(f"- 엔진 보유 상태(state.json {st.get('date')}): " + "; ".join(
            f"{label(c)} {p['qty']}주 {p['days']}일째 손절 {won(p['stop'])} 익절주문 {'있음' if p.get('tp_order') else '없음'}"
            for c, p in sp.items()))
        diff = {c for c in sp} ^ set(held)
        if diff:
            P(f"  - ⚠ trades.csv 와 state.json 보유 종목 불일치: {sorted(diff)}")

    # ---------------------------------------------------------------- 4. 시장
    P("\n## 4. 시장 상황 (KODEX 200 일봉)")
    ib = load_daily(INDEX)
    b, i = bar_on(ib, day)
    if b:
        f = feats(ib, i)
        P(f"- 종가 {won(f['close'])} 당일 {pct(f['chg'])}, 장중폭 {(b['high'] - b['low']) / ib[i - 1]['close'] * 100:.1f}%, "
          f"5일 {pct(f['ret5'])}, 20일고점대비 {pct(f['dd20'])}, 국면 {regime(f)}")
        P(f"- MA5 {won(f['ma5'])} MA20 {won(f['ma20'])} MA60 {won(f['ma60'])}")
        P("- 최근 5일: " + ", ".join(f"{x['date'][5:]} {x['close'] / ib[k - 1]['close'] - 1:+.1%}"
                                    for k, x in enumerate(ib) if i - 4 <= k <= i))
    else:
        P("- 당일 지수 일봉 없음(장후 스캔 전이거나 휴장)")

    # ---------------------------------------------------------------- 5. 신호 점검
    P("\n## 5. 보유·후보 종목 신호 점검 (오늘 종가 기준)")
    mf = feats(ib, i) if b else {}
    check = [(c, "보유") for c in sorted(held)] + [
        (p["code"], f"내일후보({p['strat']})" + (" — 이미 보유라 엔진이 건너뜀" if p["code"] in held else ""))
        for p in st.get("plans", []) if st.get("plan_date") == day]
    if not check:
        P("- 점검 대상 없음")
    for c, role in check:
        bars = load_daily(c)
        _, k = bar_on(bars, day)
        f = feats(bars, k)
        if not f:
            P(f"- {label(c)} [{role}] 일봉 없음")
            continue
        rel = f["ret5"] - mf["ret5"] if f.get("ret5") is not None and mf.get("ret5") is not None else None
        flags = []
        if f["crash5"]:
            flags.append("최근5일 악재성폭락 " + ", ".join(f["crash5"]))
        if mf.get("ret5") is not None and mf["ret5"] > 0 and role.startswith("내일"):
            flags.append(f"지수는 5일 {pct(mf['ret5'])} 반등 중(낡은 급락 신호 가능)")
        if f.get("ma5") and f["close"] > f["ma5"]:
            flags.append("종가>5일선(반등 진행)")
        P(f"- {label(c)} [{role}] 종가 {won(f['close'])} 당일 {pct(f['chg'])} 5일 {pct(f['ret5'])} "
          f"(지수대비 {pct(rel)}) RSI14 {f.get('rsi14', 0):.0f} 거래량 {f['vol_x'] or 0:.1f}배"
          + (" | " + " / ".join(flags) if flags else ""))

    # ---------------------------------------------------------------- 6. 누적
    P("\n## 6. 누적 거래 (trades.csv 전체)")
    by_day = Counter(t["time"][:10] for t in trades)
    P("- 일자별 체결 건수: " + (", ".join(f"{d} {n}" for d, n in sorted(by_day.items())) or "없음"))
    sells = [t for t in trades if t["side"] == "sell"]
    P(f"- 누적 매수 {len(trades) - len(sells)}건, 매도 {len(sells)}건, 청산 사유: {dict(Counter(t['why'] for t in sells))}")


if __name__ == "__main__":
    main()
