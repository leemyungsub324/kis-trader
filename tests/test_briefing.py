from datetime import datetime

from kis_trader.briefing import collect, render, vol_label, won
from kis_trader.engine import Engine
from kis_trader.strategies import Reversal


class Broker:
    def __init__(self):
        self.calls = 0

    def index_price(self, code):
        self.calls += 1
        return {"0001": dict(price=5100.5, chg=-0.0359, open=5305.4, high=5327.99, low=5069.83, prev=5290.53,
                             value=16.4e12, up=263, down=615, upper=0, lower=0),
                "1001": dict(price=665.65, chg=-0.0486, open=705.88, high=708.34, low=664.98, prev=699.66,
                             value=4.9e12, up=282, down=1399, upper=8, lower=1)}[code]

    def volume_rank(self, kind):
        self.calls += 1
        if kind == "value":
            return [dict(code="000660", name="SK하이닉스", price=1118000, chg=-0.0806, value=4.8e12, vol_ratio=1.3),
                    dict(code="069500", name="KODEX 200", price=79900, chg=-0.039, value=1.7e12, vol_ratio=1.0),
                    dict(code="005935", name="삼성전자우", price=60000, chg=-0.05, value=1e11, vol_ratio=1.0)]
        return [dict(code="190510", name="나무가", price=9000, chg=0.30, value=5.94e10, vol_ratio=15.9),
                dict(code="143160", name="아이디스", price=9000, chg=0.037, value=3e8, vol_ratio=11.0)]

    def change_rank(self, down=False):
        self.calls += 1
        r = dict(code="345290", name="영광와이케이엠씨", price=8850, chg=-0.1955, value=3.1e11, high=0, low=0)
        u = dict(code="166480", name="코아스템켐온", price=1807, chg=0.30, value=2.8e9, high=0, low=0)  # 30억 미만
        return [r] if down else [u, dict(u, code="099990", name="KODEX 레버리지", value=1e12)]

    def price(self, code):
        return {"price": 124700}


class Notifier:
    enabled = True

    def __init__(self):
        self.sent = []

    def send(self, text):
        self.sent.append(text)


def test_collect_filters_funds_preferred_and_small_value():
    d = collect(Broker(), {"483650": {"strat": "reversal", "qty": 19, "entry": 120300.0, "days": 1}})
    assert [r["name"] for r in d["value"]] == ["SK하이닉스"]           # ETF·우선주 제외
    assert [r["name"] for r in d["surge"]] == ["나무가"]               # 거래대금 30억 미만 제외
    assert d["up"] == [] and [r["name"] for r in d["down"]] == ["영광와이케이엠씨"]
    assert d["pos"][0]["price"] == 124700


def test_render_fixed_template():
    d = collect(Broker(), {"483650": {"strat": "reversal", "qty": 19, "entry": 120300.0, "days": 1}})
    t = render(d, "11/16 10:00", {"코스피": 5120.0}, "BEAR")
    assert t.splitlines()[0] == "[시황 11/16 10:00] 국면 BEAR"
    assert "코스피 5,100.50 ▼3.59% | 1시간 ▼0.38%" in t
    assert "코스닥 665.65 ▼4.86%\n" in t                              # 직전 값 없으면 1시간 변화 생략
    assert "상승 545 / 하락 2,014 (상한 8, 하한 1)" in t
    assert "1. SK하이닉스 1,118,000 ▼8.06% 4.8조" in t
    assert "1. 나무가 9,000 ▲30.00% 15.9배 594억" in t
    assert "483650 reversal 19주 평단 120,300 → 124,700 (▲3.66%) 1일째" in t
    assert won(3.1e11) == "3,100억" and vol_label(0.049) == "매우 높음" and vol_label(0.005) == "낮음"


def test_engine_briefs_once_per_hour_within_market_hours(tmp_path):
    b, n = Broker(), Notifier()
    clk = [datetime(2026, 11, 16, 10, 0, 5)]
    e = Engine(b, [Reversal()], {"briefing": {"hours": [9, 10, 15]}}, data_dir=str(tmp_path),
               now=lambda: clk[0], notify=n)
    assert e.brief("10:00") is True
    assert e.brief("10:00") is False and e.brief("10:20") is False    # 같은 시각 중복 없음
    assert e.brief("11:00") is False                                   # 설정에 없는 시각
    assert e.brief("15:31") is False                                   # 정각 후 30분 지나면 생략
    clk[0] = datetime(2026, 11, 16, 15, 6)
    assert e.brief("15:06") is True                                    # 마감 스캔으로 늦어져도 전송
    assert len(n.sent) == 2 and "1시간" not in n.sent[1].splitlines()[1]   # 5시간 전 값은 비교 안 함


def test_briefing_off_without_config_or_notifier(tmp_path):
    e = Engine(Broker(), [Reversal()], {}, data_dir=str(tmp_path), notify=Notifier())
    assert e.brief("10:00") is False
    e2 = Engine(Broker(), [Reversal()], {"briefing": {}}, data_dir=str(tmp_path / "x"))
    assert e2.brief("10:00") is False
