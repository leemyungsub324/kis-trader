from kis_trader.indicators import compute_all, ichimoku, rsi, sma


def test_sma():
    assert sma([1, 2, 3, 4, 5], 5) == 3
    assert sma([1, 2], 5) is None


def test_rsi_extremes():
    assert rsi(list(range(1, 40)), 14) == 100.0
    assert rsi(list(range(40, 1, -1)), 14) < 1
    assert rsi([1, 2, 3], 14) is None


def test_rsi_wilder_reference():
    # 널리 알려진 Wilder 예제 종가 (RSI(14) 마지막 값 약 70.5)
    closes = [44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08,
              45.89, 46.03, 45.61, 46.28, 46.28]
    assert abs(rsi(closes, 14) - 70.46) < 0.2


def _series(n):
    highs = [100 + i for i in range(n)]
    lows = [90 + i for i in range(n)]
    closes = [95 + i for i in range(n)]
    return highs, lows, closes


def test_ichimoku_needs_enough_bars():
    h, l, c = _series(60)
    ich = ichimoku(h, l, c)
    assert ich.tenkan is not None and ich.kijun is not None
    assert ich.span_b_now is None  # 52 + 26 봉 필요


def test_ichimoku_no_lookahead():
    h, l, c = _series(90)
    base = ichimoku(h, l, c)
    # 마지막 봉만 크게 바꿔도 '오늘 위치의 구름'은 26봉 전 데이터이므로 변하지 않아야 한다
    h2, l2, c2 = list(h), list(l), list(c)
    h2[-1], l2[-1], c2[-1] = 10_000, 1, 5_000
    changed = ichimoku(h2, l2, c2)
    assert changed.span_a_now == base.span_a_now
    assert changed.span_b_now == base.span_b_now
    assert changed.tenkan != base.tenkan


def test_compute_all_empty():
    ind = compute_all([], [], [])
    assert ind.last_close is None and ind.rsi14 is None
