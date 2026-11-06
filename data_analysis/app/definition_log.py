"""Write-once log of definition edits on SharePoint (memory mode).

Why this exists
---------------
Every definition version used to be made durable by re-uploading the whole
results database (``state/coding.sqlite``, ~18 MB) on each save. That made
saves slow, used up SharePoint's 200-version history of the one file that
matters most, and meant a save and any other writer could collide on it.

Now each save writes **one small new file** to ``state/definition_log/``. The
database keeps every version it already holds; the log only adds to it.

Data-safety rules, in order of importance
-----------------------------------------
* **Nothing is ever overwritten or deleted.** Entries are created with
  ``sharepoint_io.create_bytes`` (``conflictBehavior=fail``), so the service
  itself refuses to replace an existing file. This module has no delete.
* **Durable before visible.** A save writes its entry and reads it back before
  the version is added to the in-memory database. If the write fails, the save
  fails and nothing changes.
* **Merging never drops a version.** At boot (and after a conflict reload) the
  log is merged into the database: an entry already present with the same text
  is recognised and skipped; an entry whose (code, version) is taken by
  *different* text is kept under the next free version number and logged
  CRITICAL, rather than either text being discarded.
* **Fail closed.** If the log cannot be read at boot, the app still serves
  reads, but saves are refused (:class:`LogUnavailable`) until a restart reads
  it, so no edit is made against an incomplete history.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import re
import sqlite3
import threading
import uuid

import config
import db
from . import sharepoint_io as sp

log = logging.getLogger("app.definition_log")

SCHEMA_VERSION = 1

_APPLIED_SCHEMA = """
CREATE TABLE IF NOT EXISTS definition_log_applied (
    file_name  TEXT PRIMARY KEY,
    code_name  TEXT NOT NULL,
    version    INTEGER NOT NULL,      -- version the entry landed at in this DB
    applied_at TEXT DEFAULT (datetime('now'))
);
"""

_lock = threading.RLock()
_ready = False          # log read successfully this session: saves allowed


class LogUnavailable(RuntimeError):
    """The definition log could not be read or written; saves are refused."""


class BadEntry(ValueError):
    """A log file that is not a valid entry (left in place, never applied)."""


def log_dir() -> str:
    return f"{config.SHAREPOINT_STATE_DIR}/definition_log"


def ready() -> bool:
    return _ready


def _reset_for_tests() -> None:
    global _ready
    with _lock:
        _ready = False


# --- entries -----------------------------------------------------------------

def utc_now() -> str:
    """Timestamp in SQLite's ``datetime('now')`` format (UTC)."""
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def entry_name(code_name: str, version: int, created_at: str) -> str:
    # Timestamp first so a folder listing sorts chronologically; a random
    # suffix so two entries can never share a name. The code name is only a
    # readable hint: the authoritative name is inside the file.
    stamp = re.sub(r"[^0-9]", "", created_at)
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", code_name).strip("_")[:60] or "code"
    return f"{stamp}__{safe}__v{version}__{uuid.uuid4().hex[:8]}.json"


def encode(code_name: str, version: int, definition: str, created_at: str,
           based_on_version: int | None) -> bytes:
    return json.dumps({
        "schema": SCHEMA_VERSION,
        "code_name": code_name,
        "version": version,
        "definition": definition,
        "created_at": created_at,
        "based_on_version": based_on_version,
    }, ensure_ascii=False, indent=1).encode("utf-8")


def decode(data: bytes) -> dict:
    try:
        e = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BadEntry(f"not JSON: {exc}") from exc
    if not isinstance(e, dict) or e.get("schema") != SCHEMA_VERSION:
        raise BadEntry(f"unknown schema: {e.get('schema') if isinstance(e, dict) else e!r}")
    for k, t in (("code_name", str), ("version", int), ("definition", str),
                 ("created_at", str)):
        if not isinstance(e.get(k), t):
            raise BadEntry(f"field {k!r} missing or not {t.__name__}")
    if not e["definition"].strip() or e["version"] < 1:
        raise BadEntry("empty definition or bad version")
    return e


def write_entry(code_name: str, version: int, definition: str, created_at: str,
                based_on_version: int | None) -> str:
    """Create the entry on SharePoint and read it back. Returns its file name.

    Raises :class:`LogUnavailable` (nothing recorded) on any failure.
    """
    if not _ready:
        raise LogUnavailable(
            "The definition log could not be read when the app started, so "
            "saving is switched off to protect the edit history. Restart the "
            "app; reading still works.")
    name = entry_name(code_name, version, created_at)
    data = encode(code_name, version, definition, created_at, based_on_version)
    path = f"{log_dir()}/{name}"
    try:
        sp.create_bytes(path, data)
        back = sp.download_bytes(path)
    except sp.SharePointError as exc:
        raise LogUnavailable(f"Could not record the edit on SharePoint ({exc}). "
                             "Nothing was saved; try again.") from exc
    if back != data:
        raise LogUnavailable(f"SharePoint returned different content for {name}; "
                             "the edit was not applied. Try again.")
    log.info("Recorded definition %r v%d as %s.", code_name, version, name)
    return name


# --- merge -------------------------------------------------------------------

def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(_APPLIED_SCHEMA)


def merge_entry(conn: sqlite3.Connection, file_name: str, e: dict) -> str:
    """Merge one decoded entry into *conn*. Returns 'added', 'present' or 'relocated'.

    Never updates or deletes an existing definition row; only inserts.
    """
    code, version, text = e["code_name"], e["version"], e["definition"]
    # Already here: the same text at the logged version (written by this app),
    # or the same text with the entry's own timestamp (merged before, possibly
    # under a relocated version number).
    same = conn.execute(
        "SELECT version FROM code_definitions WHERE code_name=? AND definition=? "
        "AND (version=? OR created_at=?) ORDER BY version=? DESC LIMIT 1",
        (code, text, version, e["created_at"], version)).fetchone()
    if same:
        outcome, landed = "present", same[0]
    else:
        taken = conn.execute(
            "SELECT 1 FROM code_definitions WHERE code_name=? AND version=?",
            (code, version)).fetchone()
        if taken:
            landed = (conn.execute(
                "SELECT MAX(version) FROM code_definitions WHERE code_name=?",
                (code,)).fetchone()[0] or 0) + 1
            outcome = "relocated"
            log.critical("Definition log entry %s is %r v%d, but v%d already holds "
                         "different text. Keeping both: the logged text is added as "
                         "v%d.", file_name, code, version, version, landed)
        else:
            landed, outcome = version, "added"
        conn.execute(
            "INSERT INTO code_definitions "
            "(code_name, version, definition, kappa, model, is_current, created_at) "
            "VALUES (?, ?, ?, NULL, NULL, 0, ?)",
            (code, landed, text, e["created_at"]))
    conn.execute(
        "INSERT OR IGNORE INTO definition_log_applied (file_name, code_name, version) "
        "VALUES (?, ?, ?)", (file_name, code, landed))
    return outcome


def mark_current(conn: sqlite3.Connection, codes) -> None:
    """Make the highest version of each code its current one."""
    for code in codes:
        top = conn.execute("SELECT MAX(version) FROM code_definitions WHERE code_name=?",
                           (code,)).fetchone()[0]
        conn.execute("UPDATE code_definitions SET is_current=(version=?) WHERE code_name=?",
                     (top, code))


def apply_entries(conn: sqlite3.Connection, entries: list[tuple[str, bytes]]) -> dict:
    """Merge downloaded ``(file_name, bytes)`` entries into *conn*, in name order.

    Entries already recorded in ``definition_log_applied`` are skipped. Invalid
    files are reported and left alone. One transaction: all or nothing.
    """
    ensure_schema(conn)
    done = {r[0] for r in conn.execute("SELECT file_name FROM definition_log_applied")}
    counts = {"added": 0, "present": 0, "relocated": 0, "skipped": 0, "bad": []}
    touched = set()
    with conn:
        for name, data in sorted(entries):
            if name in done:
                counts["skipped"] += 1
                continue
            try:
                e = decode(data)
            except BadEntry as exc:
                log.error("Ignoring definition log file %s: %s", name, exc)
                counts["bad"].append(name)
                continue
            counts[merge_entry(conn, name, e)] += 1
            touched.add(e["code_name"])
        mark_current(conn, touched)
    return counts


def load_and_apply() -> dict:
    """Read the whole log from SharePoint and merge it into the live in-memory DB.

    Sets the module ready (saves allowed) only if the log was read completely.
    """
    global _ready
    with _lock:
        try:
            items = [i for i in sp.list_folder(log_dir())
                     if not i["is_folder"] and i["name"].endswith(".json")]
            conn = db.connect()
            try:
                ensure_schema(conn)
                done = {r[0] for r in conn.execute(
                    "SELECT file_name FROM definition_log_applied")}
                entries = [(i["name"], sp.download_bytes(i["path"]))
                           for i in items if i["name"] not in done]
                counts = apply_entries(conn, entries)
            finally:
                conn.close()
        except sp.SharePointError as exc:
            _ready = False
            log.critical("Could not read the definition log (%s). Saving definitions "
                         "is OFF for this session; restart to retry.", exc)
            return {"error": str(exc)}
        _ready = True
        counts["listed"] = len(items)
        log.info("Definition log merged: %s", counts)
        return counts
