"""SQLite 상태/주문 기록 (볼륨 data/ 에 저장)."""
from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    qty INTEGER NOT NULL,
    limit_price INTEGER,
    ref_price INTEGER,
    dry_run INTEGER NOT NULL,
    status TEXT NOT NULL,           -- dry_run | submitted | filled | unknown | rejected
    odno TEXT,
    filled_qty INTEGER NOT NULL DEFAULT 0,
    avg_fill_price REAL,
    reason TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL, kind TEXT NOT NULL, detail TEXT
);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS holidays (day TEXT PRIMARY KEY, is_open INTEGER NOT NULL);
-- multi 전략: 보유(또는 매수 대기) 종목이 어느 카테고리 규칙으로 관리되는지
CREATE TABLE IF NOT EXISTS lots (
    symbol TEXT PRIMARY KEY,
    sleeve TEXT NOT NULL,           -- surge | vbreak | swing | dip
    opened TEXT NOT NULL,           -- 매수 주문 시각 (KST ISO)
    stop_pct REAL NOT NULL,         -- 평균단가 대비 손절 폭(%)
    atr REAL NOT NULL DEFAULT 0,    -- 진입 시점 ATR(14)
    high REAL NOT NULL DEFAULT 0,   -- 보유 중 최고가 (추적 손절용)
    half_done INTEGER NOT NULL DEFAULT 0
);
"""
# 기존 DB 에 없는 컬럼 추가 (orders.status 에 canceled 추가됨: 미체결 취소)
MIGRATIONS = ("ALTER TABLE orders ADD COLUMN orgno TEXT", "ALTER TABLE orders ADD COLUMN tag TEXT")


def _now() -> str:
    return datetime.now(KST).isoformat(timespec="seconds")


class Database:
    def __init__(self, path: Path):
        self._conn = sqlite3.connect(path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)
        for sql in MIGRATIONS:
            try:
                self._conn.execute(sql)
            except sqlite3.OperationalError:
                pass  # 이미 있는 컬럼
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # ── kv ──
    def kv_get(self, key: str) -> str | None:
        row = self._conn.execute("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None

    def kv_set(self, key: str, value: str) -> None:
        self._conn.execute(
            "INSERT INTO kv(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
        self._conn.commit()

    def day_start_equity(self, day: date, current_equity: int) -> int:
        """그날 첫 조회 시 평가금액을 기록하고, 이후(재시작 포함)에는 그 값을 돌려준다."""
        key = f"day_start_equity:{day.isoformat()}"
        v = self.kv_get(key)
        if v is None:
            self.kv_set(key, str(current_equity))
            return current_equity
        return int(v)

    # ── 휴장일 ──
    def get_holiday(self, d: date) -> bool | None:
        row = self._conn.execute("SELECT is_open FROM holidays WHERE day=?", (d.isoformat(),)).fetchone()
        return None if row is None else bool(row["is_open"])

    def set_holiday(self, d: date, is_open: bool) -> None:
        self._conn.execute(
            "INSERT INTO holidays(day,is_open) VALUES(?,?) ON CONFLICT(day) DO UPDATE SET is_open=excluded.is_open",
            (d.isoformat(), int(is_open)),
        )
        self._conn.commit()

    # ── 주문 ──
    def add_order(self, *, symbol: str, side: str, qty: int, limit_price: int | None,
                  ref_price: int, dry_run: bool, status: str, reason: str, odno: str | None = None,
                  tag: str | None = None) -> int:
        cur = self._conn.execute(
            "INSERT INTO orders(ts,symbol,side,qty,limit_price,ref_price,dry_run,status,odno,reason,tag)"
            " VALUES(?,?,?,?,?,?,?,?,?,?,?)",
            (_now(), symbol, side, qty, limit_price, ref_price, int(dry_run), status, odno, reason, tag),
        )
        self._conn.commit()
        return int(cur.lastrowid)

    def update_order(self, order_id: int, **fields) -> None:
        cols = ", ".join(f"{k}=?" for k in fields)
        self._conn.execute(f"UPDATE orders SET {cols} WHERE id=?", (*fields.values(), order_id))
        self._conn.commit()

    def pending_orders(self) -> list[sqlite3.Row]:
        return list(self._conn.execute(
            "SELECT * FROM orders WHERE status IN ('submitted','unknown','cancel_failed') AND dry_run=0"
        ))

    def count_orders_on(self, day: date) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT status, COUNT(*) n FROM orders WHERE substr(ts,1,10)=? GROUP BY status",
            (day.isoformat(),),
        )
        return {r["status"]: r["n"] for r in rows}

    def order_sides_on(self, day: date) -> set[tuple[str, str]]:
        """그날 기록된 (종목, 매수/매도) 조합. 거부(rejected)된 주문은 제외."""
        rows = self._conn.execute(
            "SELECT DISTINCT symbol, side FROM orders WHERE substr(ts,1,10)=? AND status != 'rejected'",
            (day.isoformat(),),
        )
        return {(r["symbol"], r["side"]) for r in rows}

    def order_sides_since(self, since: datetime) -> set[tuple[str, str]]:
        """since(KST) 이후 기록된 (종목, side). 거부 제외. 1시간봉 데모의 '이번 시간대 주문 여부'용."""
        rows = self._conn.execute(
            "SELECT DISTINCT symbol, side FROM orders WHERE ts >= ? AND status != 'rejected'",
            (since.astimezone(KST).isoformat(timespec="seconds"),),
        )
        return {(r["symbol"], r["side"]) for r in rows}

    def buy_amount_on(self, day: date) -> int:
        """그날 매수 주문 누적 금액(주문 기준, 거부 제외). 미체결분도 포함하는 보수적 값."""
        row = self._conn.execute(
            "SELECT COALESCE(SUM((CASE WHEN status='canceled' THEN filled_qty ELSE qty END)"
            " * COALESCE(limit_price, ref_price)), 0) v FROM orders"
            " WHERE substr(ts,1,10)=? AND side='buy' AND status != 'rejected'",
            (day.isoformat(),),
        ).fetchone()
        return int(row["v"] or 0)

    def open_orders(self, side: str) -> list[sqlite3.Row]:
        """전송됐고 아직 전량 체결·취소되지 않은 주문 (DB 기준)."""
        return list(self._conn.execute(
            "SELECT * FROM orders WHERE status='submitted' AND dry_run=0 AND side=?", (side,)))

    def has_order_since(self, symbol: str, side: str, since: datetime) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM orders WHERE symbol=? AND side=? AND ts >= ? AND status != 'rejected' LIMIT 1",
            (symbol, side, since.astimezone(KST).isoformat(timespec="seconds"))).fetchone()
        return row is not None

    def last_buy_tag(self, symbol: str) -> str | None:
        row = self._conn.execute(
            "SELECT tag FROM orders WHERE symbol=? AND side='buy' AND tag IS NOT NULL AND status != 'rejected'"
            " ORDER BY id DESC LIMIT 1", (symbol,)).fetchone()
        return row["tag"] if row else None

    # ── lots (multi 전략의 카테고리별 보유 관리) ──
    def lot_put(self, symbol: str, sleeve: str, stop_pct: float, atr: float, high: float = 0.0,
                opened: str | None = None) -> None:
        self._conn.execute(
            "INSERT INTO lots(symbol,sleeve,opened,stop_pct,atr,high,half_done) VALUES(?,?,?,?,?,?,0)"
            " ON CONFLICT(symbol) DO UPDATE SET sleeve=excluded.sleeve, opened=excluded.opened,"
            " stop_pct=excluded.stop_pct, atr=excluded.atr, high=excluded.high, half_done=0",
            (symbol, sleeve, opened or _now(), stop_pct, atr, high))
        self._conn.commit()

    def lot_update(self, symbol: str, **fields) -> None:
        cols = ", ".join(f"{k}=?" for k in fields)
        self._conn.execute(f"UPDATE lots SET {cols} WHERE symbol=?", (*fields.values(), symbol))
        self._conn.commit()

    def lot_delete(self, symbol: str) -> None:
        self._conn.execute("DELETE FROM lots WHERE symbol=?", (symbol,))
        self._conn.commit()

    def lots(self) -> dict[str, sqlite3.Row]:
        return {r["symbol"]: r for r in self._conn.execute("SELECT * FROM lots")}

    # ── 이벤트 ──
    def event(self, kind: str, detail: dict | str = "") -> None:
        text = detail if isinstance(detail, str) else json.dumps(detail, ensure_ascii=False, default=str)
        self._conn.execute("INSERT INTO events(ts,kind,detail) VALUES(?,?,?)", (_now(), kind, text))
        self._conn.commit()
