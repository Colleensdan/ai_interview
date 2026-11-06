"""Versioned code definitions (spec 5.7).

Editing a definition never overwrites: the new text becomes the current version
and the prior one is archived (viewable later alongside the Cohen's kappa it
achieved). Stored in the same SQLite db as the Task 1 results.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS code_definitions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    code_name  TEXT NOT NULL,
    version    INTEGER NOT NULL,
    definition TEXT NOT NULL,
    kappa      REAL,                 -- kappa this definition achieved (NULL until analysed)
    model      TEXT,                 -- model the kappa was measured against
    is_current INTEGER NOT NULL DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);
"""


class DefinitionStore:
    def __init__(self, db_path: str | Path, journal=None):
        """``journal``, if given, makes each new version durable before it is
        added here: ``journal(code, version, definition, created_at,
        based_on_version) -> entry name``. It raises to refuse the save, in
        which case nothing is changed (see app.definition_log)."""
        self.db_path = str(db_path)
        self.journal = journal
        self._lock = threading.Lock()
        with self._conn() as c:
            c.executescript(_SCHEMA)

    def _conn(self) -> sqlite3.Connection:
        import db as _db

        return _db.connect(self.db_path, row_factory=True)

    def seed(self, codes: list[tuple[str, str]], kappa_lookup: dict[str, float | None],
             model: str | None) -> int:
        """Insert version 1 for any code not yet present. Returns how many.

        ``codes`` = list of (code_name, definition); ``kappa_lookup`` maps code
        name to its baseline Task 1 kappa (for ``model``).
        """
        added = 0
        with self._conn() as c:
            existing = {r["code_name"] for r in c.execute(
                "SELECT DISTINCT code_name FROM code_definitions")}
            for name, definition in codes:
                if name in existing:
                    continue
                c.execute(
                    "INSERT INTO code_definitions "
                    "(code_name, version, definition, kappa, model, is_current) "
                    "VALUES (?, 1, ?, ?, ?, 1)",
                    (name, definition, kappa_lookup.get(name), model),
                )
                added += 1
        return added

    def current(self, code_name: str) -> sqlite3.Row | None:
        with self._conn() as c:
            return c.execute(
                "SELECT * FROM code_definitions WHERE code_name=? AND is_current=1",
                (code_name,),
            ).fetchone()

    def history(self, code_name: str) -> list[dict]:
        """All versions for a code, newest first."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM code_definitions WHERE code_name=? ORDER BY version DESC",
                (code_name,),
            ).fetchall()
        return [dict(r) for r in rows]

    def all_current(self) -> dict[str, dict]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT * FROM code_definitions WHERE is_current=1").fetchall()
        return {r["code_name"]: dict(r) for r in rows}

    def save_new(self, code_name: str, definition: str) -> dict:
        """Archive the current version and make ``definition`` the new current.

        Text identical to the current version is not saved again: the current
        row is returned with ``unchanged=True``. With a journal, the new
        version is made durable first; if that fails nothing changes here.
        """
        with self._lock, self._conn() as c:
            cur = c.execute(
                "SELECT * FROM code_definitions WHERE code_name=? AND is_current=1",
                (code_name,)).fetchone()
            if cur is not None and cur["definition"] == definition:
                return {**dict(cur), "unchanged": True}
            top = c.execute(
                "SELECT MAX(version) AS v FROM code_definitions WHERE code_name=?",
                (code_name,),
            ).fetchone()
            next_version = (top["v"] or 0) + 1
            created_at = _utc_now()
            entry = None
            if self.journal is not None:
                entry = self.journal(code_name, next_version, definition, created_at,
                                     cur["version"] if cur is not None else None)
            c.execute("UPDATE code_definitions SET is_current=0 WHERE code_name=?",
                      (code_name,))
            c.execute(
                "INSERT INTO code_definitions "
                "(code_name, version, definition, kappa, model, is_current, created_at) "
                "VALUES (?, ?, ?, NULL, NULL, 1, ?)",
                (code_name, next_version, definition, created_at),
            )
            if entry is not None:
                c.execute(
                    "CREATE TABLE IF NOT EXISTS definition_log_applied ("
                    "file_name TEXT PRIMARY KEY, code_name TEXT NOT NULL, "
                    "version INTEGER NOT NULL, applied_at TEXT DEFAULT (datetime('now')))")
                c.execute("INSERT OR IGNORE INTO definition_log_applied "
                          "(file_name, code_name, version) VALUES (?, ?, ?)",
                          (entry, code_name, next_version))
            row = c.execute(
                "SELECT * FROM code_definitions WHERE code_name=? AND version=?",
                (code_name, next_version),
            ).fetchone()
        return dict(row)

    def record_kappa(self, code_name: str, kappa: float | None, model: str) -> None:
        """Set the kappa on the current version (after a re-analysis)."""
        with self._conn() as c:
            c.execute(
                "UPDATE code_definitions SET kappa=?, model=? "
                "WHERE code_name=? AND is_current=1",
                (kappa, model, code_name),
            )

    def previous_kappa(self, code_name: str) -> float | None:
        """Kappa of the version immediately before the current one."""
        with self._conn() as c:
            rows = c.execute(
                "SELECT kappa FROM code_definitions WHERE code_name=? "
                "ORDER BY version DESC LIMIT 2",
                (code_name,),
            ).fetchall()
        return rows[1]["kappa"] if len(rows) > 1 else None


def _utc_now() -> str:
    import datetime as _dt
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
