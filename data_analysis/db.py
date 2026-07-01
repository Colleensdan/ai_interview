"""Shared SQLite connection helper.

Two modes:

* **File mode** (local dev, offline pipeline): a normal on-disk database with
  WAL journaling and a busy timeout so the handful of concurrent readers/writers
  (web requests + the background job thread, ≤2 users) don't hit "database is
  locked" errors.

* **Memory mode** (Render, data-privacy): the database lives ONLY in process
  memory. ``config.DB_PATH`` is a shared-cache URI so every ``connect()`` in the
  process reaches the *same* in-memory database; a long-lived anchor connection
  keeps that database alive even when per-query connections close. Nothing is
  ever written to the container filesystem. The durable copy lives in SharePoint
  and is loaded/saved as a serialized blob (see :func:`load_blob` /
  :func:`dump_blob` and ``app/state_sync.py``).

This only configures *how* connections are opened — it does not touch Task 1's
coding/analysis logic.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import config

_wal_enabled: set[str] = set()

# Memory-mode anchor: one connection held open for the process lifetime so the
# shared-cache in-memory database is not destroyed when per-query connections
# close. Created lazily on the first memory-mode connect().
_anchor: sqlite3.Connection | None = None
_anchor_lock = threading.Lock()


def _is_memory_uri(path: str) -> bool:
    return path.startswith("file:") and "mode=memory" in path


def _ensure_anchor(uri: str) -> None:
    global _anchor
    if _anchor is not None:
        return
    with _anchor_lock:
        if _anchor is None:
            conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
            conn.execute(f"PRAGMA busy_timeout={config.SQLITE_BUSY_TIMEOUT_MS}")
            _anchor = conn


def connect(db_path: str | Path | None = None, row_factory: bool = False) -> sqlite3.Connection:
    path = str(db_path or config.DB_PATH)

    if _is_memory_uri(path):
        # Shared-cache in-memory: keep the anchor alive first, then hand out a
        # fresh connection to the same database. No WAL (not applicable to
        # memory); shared-cache locking + busy_timeout serialise concurrent use.
        _ensure_anchor(path)
        conn = sqlite3.connect(
            path, uri=True, timeout=config.SQLITE_BUSY_TIMEOUT_MS / 1000,
            check_same_thread=False,
        )
        conn.execute(f"PRAGMA busy_timeout={config.SQLITE_BUSY_TIMEOUT_MS}")
        if row_factory:
            conn.row_factory = sqlite3.Row
        return conn

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


# --- serialize / restore the in-memory database (memory mode only) -----------

class IntegrityError(RuntimeError):
    """A serialized DB blob failed PRAGMA integrity_check on load."""


_SQLITE_MAGIC = b"SQLite format 3\x00"


def _as_rollback_image(blob: bytes) -> bytes:
    """Normalise a WAL-mode DB image to rollback mode so deserialize() accepts it.

    ``sqlite3.Connection.deserialize()`` rejects WAL-journalled images
    ("unable to open database file"). A checkpointed WAL file holds all data in
    the main image, so flipping the file-format version bytes (offsets 18/19)
    from 2 (WAL) to 1 (rollback) yields a fully valid rollback-mode database.
    Files that are already rollback mode (including our own ``serialize()``
    output) are returned unchanged.
    """
    if len(blob) >= 20 and blob[:16] == _SQLITE_MAGIC and blob[18] == 2 and blob[19] == 2:
        b = bytearray(blob)
        b[18] = 1
        b[19] = 1
        return bytes(b)
    return blob


def dump_blob() -> bytes:
    """Serialize the current (in-memory) database to bytes for durable storage.

    Only valid in memory mode. Uses a fresh shared-cache connection, which sees
    the same database as every other connection in the process.
    """
    conn = connect()
    try:
        return conn.serialize()
    finally:
        conn.close()


def load_blob(blob: bytes) -> None:
    """Replace the in-memory database contents with a serialized ``blob``.

    Guards data integrity: the blob is first deserialized into a *private*
    in-memory connection and ``PRAGMA integrity_check`` is run; only if it passes
    is it copied (via SQLite ``backup()``) into the live shared-cache database.
    ``backup()`` into a shared-cache connection *is* visible to sibling
    connections (``deserialize()`` directly is not — it detaches from the cache),
    which is why we go through backup.

    Raises :class:`IntegrityError` if the blob is corrupt; the live database is
    left untouched in that case.
    """
    blob = _as_rollback_image(blob)
    priv = sqlite3.connect(":memory:")
    try:
        try:
            priv.deserialize(blob)
            result = priv.execute("PRAGMA integrity_check").fetchone()
        except sqlite3.DatabaseError as exc:
            # Malformed image: deserialize or the check itself raises.
            raise IntegrityError(f"database image is malformed: {exc}") from exc
        if not result or result[0] != "ok":
            raise IntegrityError(f"integrity_check failed: {result and result[0]}")
        dest = connect()
        try:
            priv.backup(dest)
        finally:
            dest.close()
    finally:
        priv.close()


def _reset_anchor_for_tests() -> None:
    """Drop the process anchor so a test can start from a fresh in-memory DB."""
    global _anchor
    with _anchor_lock:
        if _anchor is not None:
            _anchor.close()
            _anchor = None
    _wal_enabled.clear()
