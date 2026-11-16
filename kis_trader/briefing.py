"""매시 정각 시황 브리핑 (텔레그램). 정해진 양식에 숫자만 채운다(LLM 사용 안 함).

내용: 코스피·코스닥 지수와 장중 변동폭, 상승·하락 종목 수, 거래대금 상위,
급등·급락(거래대금 하한 적용), 거래량 급증 종목, 보유 종목 손익.
API 호출은 회당 6건 남짓(지수 2, 순위 4, 보유 종목 수만큼 현재가).
"""
from __future__ import annotations

import logging

from .data import is_common_stock

log = logging.getLogger("briefing")

# 등락률 순위에는 ETF·ETN이 섞여 나오므로 이름으로 거른다.
_FUND = ("KODEX", "TIGER", "RISE", "ACE", "SOL", "HANARO", "PLUS", "KIWOOM", "KOSEF", "TIMEFOLIO",
         "ARIRANG", "KBSTAR", "WOORI", "BNK", "마이다스", "히어로즈", "1Q", "ETN", "레버리지", "인버스", "선물")


def _stock(r: dict) -> bool:
    return is_common_stock(r["code"], r["name"]) and not any(k in r["name"].upper() for k in _FUND)


def won(v: float) -> str:
    """거래대금 표기: 1조 이상은 조, 아니면 억."""
    return f"{v / 1e12:.1f}조" if v >= 1e12 else f"{v / 1e8:,.0f}억"


def pct(x: float) -> str:
    return f"{'▲' if x > 0 else '▼' if x < 0 else ' '}{abs(x) * 100:.2f}%"


def vol_label(rng: float) -> str:
    """지수 장중 고저폭(전일 종가 대비)으로 변동성 수준 표시."""
    return "낮음" if rng < 0.01 else "보통" if rng < 0.02 else "높음" if rng < 0.035 else "매우 높음"


def collect(broker, positions: dict, min_value: float = 3e9, top: int = 5) -> dict:
    """브로커에서 브리핑 재료를 모은다. 실패한 항목은 비워 두고 나머지는 보낸다."""
    out = {"idx": {}, "value": [], "up": [], "down": [], "surge": [], "pos": []}
    for name, code in (("코스피", "0001"), ("코스닥", "1001")):
        try:
            out["idx"][name] = broker.index_price(code)
        except Exception as e:
            log.warning("지수 조회 실패 %s: %s", code, e)
    try:
        out["value"] = [r for r in broker.volume_rank("value") if _stock(r)][:top]
    except Exception as e:
        log.warning("거래대금 순위 조회 실패: %s", e)
    try:
        out["surge"] = [r for r in broker.volume_rank("surge")
                        if _stock(r) and r["value"] >= min_value][:top]
    except Exception as e:
        log.warning("거래량 급증 조회 실패: %s", e)
    for key, down in (("up", False), ("down", True)):
        try:
            out[key] = [r for r in broker.change_rank(down) if _stock(r) and r["value"] >= min_value][:top]
        except Exception as e:
            log.warning("등락률 순위 조회 실패: %s", e)
    for code, p in positions.items():
        try:
            px = broker.price(code)["price"]
        except Exception:
            px = 0
        out["pos"].append({"code": code, "strat": p["strat"], "qty": p["qty"], "entry": p["entry"],
                           "price": px, "days": p.get("days", 0)})
    return out


def render(d: dict, when: str, prev_idx: dict | None = None, regime: str = "") -> str:
    """collect() 결과를 텔레그램 문자열로. prev_idx: 직전 브리핑의 {지수명: 값} (1시간 변화 표시용)."""
    prev_idx = prev_idx or {}
    lines = [f"[시황 {when}]" + (f" 국면 {regime}" if regime else "")]
    up = down = upper = lower = 0
    for name, i in d["idx"].items():
        rng = (i["high"] - i["low"]) / i["prev"] if i["prev"] else 0
        h1 = f" | 1시간 {pct(i['price'] / prev_idx[name] - 1)}" if prev_idx.get(name) else ""
        lines.append(f"{name} {i['price']:,.2f} {pct(i['chg'])}{h1}")
        lines.append(f"  장중폭 {rng * 100:.1f}% (변동성 {vol_label(rng)}) 거래대금 {won(i['value'])}")
        up, down, upper, lower = up + i["up"], down + i["down"], upper + i["upper"], lower + i["lower"]
    if d["idx"]:
        lines.append(f"상승 {up:,} / 하락 {down:,} (상한 {upper}, 하한 {lower})")

    def block(title, rows, extra):
        if rows:
            lines.append("")
            lines.append(f"■ {title}")
            for n, r in enumerate(rows, 1):
                lines.append(f"{n}. {r['name']} {r['price']:,.0f} {pct(r['chg'])} {extra(r)}")

    block("거래대금 상위", d["value"], lambda r: won(r["value"]))
    block("급등", d["up"], lambda r: won(r["value"]))
    block("급락", d["down"], lambda r: won(r["value"]))
    block("거래량 급증(평균 대비)", d["surge"], lambda r: f"{r['vol_ratio']:.1f}배 {won(r['value'])}")
    if d["pos"]:
        lines.append("")
        lines.append("■ 보유")
        for p in d["pos"]:
            r = p["price"] / p["entry"] - 1 if p["price"] and p["entry"] else 0
            lines.append(f"{p['code']} {p['strat']} {p['qty']}주 평단 {p['entry']:,.0f} → "
                         f"{p['price']:,.0f} ({pct(r)}) {p['days']}일째")
    return "\n".join(lines)
