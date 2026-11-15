from kis_trader.config import Settings
from kis_trader.kis.tr import tr_ids


def test_paper_tr_ids(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "paper")
    t = tr_ids(Settings())
    assert (t.balance, t.buy, t.sell, t.ccld) == ("VTTC8434R", "VTTC0012U", "VTTC0011U", "VTTC0081R")


def test_live_tr_ids(monkeypatch):
    monkeypatch.setenv("TRADING_MODE", "live")
    t = tr_ids(Settings())
    assert (t.balance, t.buy, t.sell, t.ccld) == ("TTTC8434R", "TTTC0012U", "TTTC0011U", "TTTC0081R")
    assert t.holiday == ("CTCA0903R",)
