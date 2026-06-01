"""Foundation: Database write queue + read path.

Silent-drop angle: a failed write must never vanish. A *transient* failure has
to retry and ultimately surface its result; a *permanent* failure has to raise
on the awaited future, never silently no-op.
"""

from __future__ import annotations

import sqlite3

import pytest


async def _make_kv(db) -> None:
    fut = await db.enqueue_write(
        "CREATE TABLE kv (k TEXT PRIMARY KEY, v TEXT NOT NULL)"
    )
    await fut


async def test_enqueue_write_returns_lastrowid(db):
    fut = await db.enqueue_write(
        "CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT, v TEXT)"
    )
    await fut
    fut = await db.enqueue_write("INSERT INTO t (v) VALUES (?)", ["first"])
    rowid = await fut
    assert rowid == 1
    fut = await db.enqueue_write("INSERT INTO t (v) VALUES (?)", ["second"])
    assert await fut == 2


async def test_read_sees_committed_write(db):
    await _make_kv(db)
    fut = await db.enqueue_write("INSERT INTO kv (k, v) VALUES (?, ?)", ["a", "1"])
    await fut
    # Reads bypass the queue (WAL); after awaiting the write future the value is visible.
    assert await db.fetch_val("SELECT v FROM kv WHERE k = ?", ["a"]) == "1"
    row = await db.fetch_one("SELECT k, v FROM kv WHERE k = ?", ["a"])
    assert row == {"k": "a", "v": "1"}


async def test_transient_write_retries_then_succeeds(db, monkeypatch):
    """A write that fails once (e.g. a momentary lock) must be retried, not dropped."""
    await _make_kv(db)
    conn = db._write_connection
    real_execute = conn.execute
    calls = {"n": 0}

    async def flaky(sql, params=()):
        if "INSERT INTO kv" in sql and calls["n"] == 0:
            calls["n"] += 1
            raise sqlite3.OperationalError("database is locked")
        return await real_execute(sql, params)

    monkeypatch.setattr(conn, "execute", flaky)

    fut = await db.enqueue_write("INSERT INTO kv (k, v) VALUES (?, ?)", ["b", "2"])
    rowid = await fut  # must resolve despite the first-attempt failure

    assert rowid is not None
    assert calls["n"] == 1, "expected exactly one transient failure before retry succeeded"
    monkeypatch.undo()  # restore execute for the read path
    assert await db.fetch_val("SELECT v FROM kv WHERE k = ?", ["b"]) == "2"


async def test_permanent_write_failure_raises_not_silent(db):
    """A permanently-failing write must raise on its future — never swallow."""
    fut = await db.enqueue_write("INSERT INTO does_not_exist (x) VALUES (1)")
    with pytest.raises(Exception):
        await fut


async def test_health_reports_wal_and_tables(db):
    health = await db.health()
    assert health["status"] == "ok"
    assert health["journal_mode"].lower() == "wal"
    assert health["tables"] > 0
