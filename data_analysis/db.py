"""Shared SQLite connection helper.

Enables WAL journaling and a busy timeout on every connection so the handful of
concurrent readers/writers (web requests + the background job thread, ≤2 users)
don't hit "database is locked" errors. This only configures *how* connections
are opened — it does not touch Task 1's coding/analysis logic.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import config

_wal_enabled: set[str] = set()


def connect(db_path: str | Path | None = None, row_factory: bool = False) -> sqlite3.Connection:
    path = str(db_path or config.DB_PATH)
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=config.SQLITE_BUSY_TIMEOUT_MS / 1000)
    conn.execute(f"PRAGMA busy_timeout={config.SQLITE_BUSY_TIMEOUT_MS}")
    # WAL is a persistent DB property; set it once per path per process.
    if path not in _wal_enabled:
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
        except sqlite3.OperationalError:
            pass  # e.g. read-only FS; not fatal
        _wal_enabled.add(path)
    if row_factory:
        conn.row_factory = sqlite3.Row
    return conn
