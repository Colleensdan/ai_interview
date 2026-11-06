"""Durable state sync between the in-memory DB and SharePoint (memory mode).

SharePoint holds the single authoritative copy of the operational database as a
serialized SQLite blob (``state/coding.sqlite``) plus a human-readable CSV mirror
(``state/csv/*.csv``). On Render nothing is persisted to disk; this module is how
in-memory changes become durable.

Data-safety design:

* **Hydrate at boot** — download the blob and its ETag, verify integrity, load
  into memory. If the blob is missing/unreachable we start from the seed/empty
  DB (graceful — never crash the boot).
* **Flush on every durable write** — serialize the DB and upload it with an
  ``If-Match`` ETag so we only overwrite the exact version we last read.
* **Conflict never clobbers** — if the ETag no longer matches (another instance
  wrote concurrently — should not happen with a single pinned instance), we save
  our version to a unique ``state/conflicts/`` copy, log CRITICAL, and converge
  our memory to the remote. Both versions are preserved; nothing is silently
  lost.
* **Definition edits go to a write-once log** (``app/definition_log.py``),
  not into this blob; the log is merged in after every load.
* **Shrink guard** — a push is refused if any definition version known to
  exist is missing from the database being uploaded.
* **Exports off the request path** — the CSV mirror and Excel workbook are
  rebuilt by a background worker after each push; only the blob is waited on.
* **Corruption guard** — we never upload a blob that fails an integrity check,
  and never load one that does (see ``db.load_blob``).
"""

from __future__ import annotations

import io
import csv
import logging
import sqlite3
import threading
import time

import config
import db
from pipeline import export
from . import definition_log
from . import sharepoint_io as sp

log = logging.getLogger("app.state_sync")

_lock = threading.RLock()
_etag: str | None = None  # ETag of the remote DB blob we last read/wrote
# Every (code, version) known to be in the remote blob or the definition log.
# push_state refuses to upload a database that lacks any of them.
_known_defs: set[tuple[str, int]] = set()


# --- remote paths -----------------------------------------------------------

def _db_remote() -> str:
    return f"{config.SHAREPOINT_STATE_DIR}/{config.SHAREPOINT_DB_NAME}"


def _csv_remote(name: str) -> str:
    return f"{config.SHAREPOINT_STATE_DIR}/{config.SHAREPOINT_CSV_SUBDIR}/{name}"


def _conflict_remote() -> str:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    return f"{config.SHAREPOINT_STATE_DIR}/conflicts/coding-{stamp}-{int(time.time()*1000)%1000}.sqlite"


# --- integrity of an outgoing blob ------------------------------------------

def _sane(blob: bytes) -> bool:
    """True if *blob* is a valid, non-empty results DB (safe to publish)."""
    if not blob:
        return False
    priv = sqlite3.connect(":memory:")
    try:
        priv.deserialize(blob)
        if priv.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            return False
        tables = {r[0] for r in priv.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        # Must contain at least one of our real tables — guards against an empty
        # DB clobbering a good remote copy.
        return bool(tables & {"code_definitions", "kappa", "coding_results"})
    except sqlite3.DatabaseError:
        return False
    finally:
        priv.close()


def _defs_in(conn: sqlite3.Connection) -> set[tuple[str, int]]:
    try:
        return {(r[0], r[1]) for r in conn.execute(
            "SELECT code_name, version FROM code_definitions")}
    except sqlite3.OperationalError:
        return set()   # table not created yet


def _defs_in_blob(blob: bytes) -> set[tuple[str, int]]:
    priv = sqlite3.connect(":memory:")
    try:
        priv.deserialize(blob)
        return _defs_in(priv)
    finally:
        priv.close()


def _defs_in_memory() -> set[tuple[str, int]]:
    conn = db.connect()
    try:
        return _defs_in(conn)
    finally:
        conn.close()


def _after_load() -> None:
    """Merge the definition log into a freshly loaded DB and record what we hold."""
    global _known_defs
    definition_log.load_and_apply()
    _known_defs |= _defs_in_memory()


# --- hydrate ----------------------------------------------------------------

def hydrate() -> bool:
    """Load the authoritative DB from SharePoint into memory. Returns True on load.

    Graceful: any SharePoint/integrity problem logs and returns False, leaving
    the in-memory DB as-is (seed/empty) so the app still boots.
    """
    global _etag
    if not sp.configured():
        log.info("SharePoint not configured; skipping hydrate (local state only).")
        return False
    try:
        blob, tag = sp.download_with_etag(_db_remote())
    except sp.SharePointError as e:
        log.warning("No authoritative state DB (%s); trying legacy seed.", e)
        loaded = _hydrate_from_seed()
        if not loaded:
            # Merge the definition log (and so allow saves) only if the state
            # DB is confirmed absent -- a fresh deployment. If it merely could
            # not be read, saving stays off: edits made against an empty
            # database would be numbered as if no history existed.
            try:
                absent = sp.etag(_db_remote()) is None
            except sp.SharePointError:
                absent = False
            if absent:
                with _lock:
                    _after_load()
            else:
                log.critical("State DB exists but could not be loaded; saving "
                             "definitions is OFF this session.")
        return loaded
    try:
        db.load_blob(blob)
    except db.IntegrityError as e:
        log.error("Remote state DB failed integrity check (%s); NOT loading it. "
                  "Starting from seed/empty; remote left untouched for recovery.", e)
        return False
    with _lock:
        _etag = tag
        _known_defs.update(_defs_in_blob(blob))
        log.info("Hydrated in-memory DB from SharePoint (%d bytes, etag=%s).", len(blob), tag)
        _after_load()
    return True


def _hydrate_from_seed() -> bool:
    """Migration path: load the legacy ``coding_seed.sqlite`` on first boot.

    Leaves ``_etag`` None so the first push creates ``state/coding.sqlite`` (via
    the create-if-absent path), carrying existing analysis results forward.
    """
    global _etag
    seed_remote = f"{config.SHAREPOINT_DIR}/{config.SHAREPOINT_SEED_NAME}"
    try:
        seed = sp.download_bytes(seed_remote)
    except sp.SharePointError as e:
        log.info("No legacy seed either (%s); starting from empty DB.", e)
        return False
    try:
        db.load_blob(seed)
    except db.IntegrityError as e:
        log.error("Legacy seed failed integrity check (%s); starting from empty DB.", e)
        return False
    with _lock:
        _etag = None
        _after_load()
    log.info("Hydrated from legacy %s (%d bytes); state/%s will be created on first "
             "push.", config.SHAREPOINT_SEED_NAME, len(seed), config.SHAREPOINT_DB_NAME)
    return True


# --- push -------------------------------------------------------------------

def push_state() -> bool:
    """Flush the in-memory DB (and CSV mirror) to SharePoint. Never raises.

    Returns True if the DB blob was durably written this call.
    """
    global _etag
    if not sp.configured():
        return False
    with _lock:
        try:
            blob = db.dump_blob()
        except Exception as e:  # noqa: BLE001
            log.error("Could not serialize in-memory DB (%s); skipping push.", e)
            return False
        if not _sane(blob):
            log.error("Refusing to push an empty/corrupt DB image (safety guard).")
            return False
        missing = _known_defs - _defs_in_blob(blob)
        if missing:
            log.critical("Refusing to push: %d definition version(s) known to exist "
                         "are missing from the in-memory DB (e.g. %s). Nothing "
                         "uploaded.", len(missing), sorted(missing)[:5])
            return False

        wrote = False
        if _etag is None:
            # We have no known base version (never hydrated this session). Only
            # CREATE when the remote is absent — never blind-overwrite a blob we
            # failed to read, which could clobber real data after a transient
            # hydrate error.
            try:
                existing = sp.etag(_db_remote())
            except sp.SharePointError as e:
                log.error("Could not check remote before create (%s); skipping push "
                          "to avoid clobbering.", e)
                return False
            if existing is not None:
                log.critical("Remote state DB exists but wasn't loaded this session "
                             "(hydrate likely failed). Refusing to overwrite it — "
                             "preserving our version as a conflict copy instead.")
                _handle_conflict(blob)
                return False

        try:
            new_tag = sp.upload_bytes(_db_remote(), blob, if_match=_etag)
            _etag = new_tag
            _known_defs.update(_defs_in_blob(blob))
            wrote = True
            log.info("Pushed state DB to SharePoint (%d bytes, etag=%s).", len(blob), new_tag)
        except sp.PreconditionFailed:
            # Either a concurrent writer (had _etag) or a create race (no _etag).
            _handle_conflict(blob)
        except sp.SharePointError as e:
            log.error("Failed to push state DB (%s); will retry on next write.", e)
            return False

        # The CSV mirror and Excel export are best-effort readability copies.
        # They take seconds (several uploads, plus building the workbook), and
        # push_state runs inside the request that saved a definition, so they
        # are handed to a background worker: the caller waits only for the DB
        # blob, which is the copy that makes the write durable.
        _schedule_exports()
        return wrote


def _handle_conflict(our_blob: bytes) -> None:
    """Remote changed under us: preserve BOTH versions, converge memory to remote.

    Should not occur with a single pinned instance; this is the never-lose-data
    safety net for accidental concurrency (e.g. overlapping deploys).
    """
    global _etag
    log.critical("State DB conflict (ETag mismatch) — another writer changed the "
                 "remote. Saving our version to a conflict copy and converging to remote.")
    try:
        sp.upload_bytes(_conflict_remote(), our_blob)  # unique path; no If-Match
    except sp.SharePointError as e:
        log.critical("Could not even save the conflict copy (%s)! Data at risk — "
                     "leaving in-memory DB unchanged.", e)
        return
    # Converge memory to the remote so we stop diverging; our edits are safe in
    # the conflict copy for manual reconciliation.
    try:
        blob, tag = sp.download_with_etag(_db_remote())
        db.load_blob(blob)
        _etag = tag
        # The remote copy may predate edits held only in the definition log.
        _after_load()
        log.critical("Converged in-memory DB to remote; conflicting edits preserved "
                     "in the conflicts/ folder for manual merge.")
    except (sp.SharePointError, db.IntegrityError) as e:
        log.critical("Failed to converge to remote after conflict (%s).", e)


# --- background exports -----------------------------------------------------
#
# One worker at a time. Pushes that arrive while it runs set ``_export_pending``
# and the worker goes round again, so a burst of saves costs at most one extra
# export rather than one each.

_export_cv = threading.Condition()
_export_pending = False
_export_running = False
_export_full = False      # a pending request wants every export, not just definitions


def _schedule_exports(full: bool = True) -> None:
    global _export_pending, _export_running, _export_full
    with _export_cv:
        _export_pending = True
        _export_full = _export_full or full
        if _export_running:
            return
        _export_running = True
    threading.Thread(target=_export_worker, name="state-export", daemon=True).start()


def schedule_definitions_export() -> None:
    """Refresh the readable definitions.csv after an edit (background, best-effort)."""
    if sp.configured():
        _schedule_exports(full=False)


def _export_worker() -> None:
    global _export_pending, _export_running, _export_full
    try:
        while True:
            with _export_cv:
                if not _export_pending:
                    return
                _export_pending = False
                full, _export_full = _export_full, False
            _run_exports(full)
    finally:
        with _export_cv:
            _export_running = False
            _export_cv.notify_all()


def flush_exports(timeout: float | None = None) -> bool:
    """Wait for scheduled exports to finish. True if they did within *timeout*."""
    with _export_cv:
        return _export_cv.wait_for(
            lambda: not _export_running and not _export_pending, timeout)


def _run_exports(full: bool = True) -> None:
    # Snapshot under the push lock (milliseconds), then build the exports from a
    # private copy, so the slow part never holds up a save or reads the shared
    # database while a conflict is replacing it.
    try:
        with _lock:
            blob = db.dump_blob()
        snap = sqlite3.connect(":memory:")
        snap.deserialize(blob)
    except Exception as e:  # noqa: BLE001
        log.warning("Could not snapshot the DB for export (%s); exports skipped.", e)
        return
    try:
        if not full:
            try:
                snap.row_factory = sqlite3.Row
                _upload_definitions_csv(snap)
            except Exception as e:  # noqa: BLE001
                log.warning("definitions.csv upload failed (%s).", e)
            return
        try:
            _export_csv_mirror(snap)
        except Exception as e:  # noqa: BLE001
            log.warning("CSV mirror export/upload failed (%s); DB blob is still authoritative.", e)
        try:
            _upload_matrix_workbook(snap)
        except Exception as e:  # noqa: BLE001
            log.warning("Excel export upload failed (%s); DB blob is still authoritative.", e)
    finally:
        snap.close()


# --- CSV mirror (human-readable) --------------------------------------------

def _export_remote(name: str) -> str:
    return f"{config.SHAREPOINT_STATE_DIR}/export/{name}"


def _upload_matrix_workbook(conn: sqlite3.Connection) -> None:
    """Code x interview workbook for checking the AI coding by hand."""
    conn.row_factory = None
    data = export.workbook_bytes(conn)
    sp.upload_bytes(_export_remote(export.WORKBOOK_NAME), data)


def _rows_to_csv(header: list[str], rows) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for r in rows:
        w.writerow(list(r))
    return buf.getvalue().encode("utf-8")


def _export_csv_mirror(conn: sqlite3.Connection) -> None:
    conn.row_factory = sqlite3.Row
    _upload_definitions_csv(conn)
    _upload_kappa_csv(conn)
    _upload_coding_results_csv(conn)


def _safe_query(conn, sql: str, params: tuple = ()):
    try:
        return conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        return None  # table not created yet


def _upload_definitions_csv(conn) -> None:
    rows = _safe_query(conn,
        "SELECT code_name, version, definition, kappa, model, is_current, created_at "
        "FROM code_definitions ORDER BY code_name, version")
    if rows is None:
        return
    data = _rows_to_csv(
        ["code_name", "version", "definition", "kappa", "model", "is_current", "created_at"],
        (tuple(r) for r in rows))
    sp.upload_bytes(_csv_remote("definitions.csv"), data)


def _upload_kappa_csv(conn) -> None:
    # Excluded codes are filtered out for the same reason DataStore.latest_kappa
    # filters them: a code withdrawn from the codebook can still have a kappa row
    # from an earlier run, and MAX(id) returns it. This export is a separate
    # query from the app's read path, so it needs the guard of its own -- without
    # it the published averages sit below the ones the analysis reports.
    excluded = tuple(config.EXCLUDED_CODES)
    sql = ("SELECT model, code_name, kappa, n_documents, n_human_present, n_llm_present "
           "FROM kappa k WHERE id=(SELECT MAX(id) FROM kappa "
           "WHERE model=k.model AND code_name=k.code_name)")
    if excluded:
        sql += f" AND code_name NOT IN ({','.join('?' * len(excluded))})"
    sql += " ORDER BY model, code_name"
    rows = _safe_query(conn, sql, excluded)
    if rows is None:
        return
    out = []
    for r in rows:
        k = r["kappa"]
        meets = (k is not None) and k > config.KAPPA_TARGET
        out.append((r["model"], r["code_name"], "" if k is None else round(k, 4),
                    r["n_documents"], r["n_human_present"], r["n_llm_present"], meets))
    data = _rows_to_csv(
        ["Model", "Code", "Cohen's kappa", "n_documents",
         "n_human_present", "n_llm_present",
         f"meets_target_>{config.KAPPA_TARGET:g}"], out)
    sp.upload_bytes(_csv_remote("kappa.csv"), data)


def _upload_coding_results_csv(conn) -> None:
    # Each (model, code) from the run its kappa came from, as the app shows it
    # (see DataStore.latest_runs). This used to take each model's newest run
    # with any quotes, which drops every code re-analysed since and pairs a
    # code the newest run found nowhere with an older run's quotes.
    rows = _safe_query(conn,
        "SELECT model, document_title, code_name, quote, reason, run_id "
        "FROM coding_results ORDER BY model, document_title, code_name, id")
    if rows is None:
        return
    runs = export.latest_kappa_runs(conn)
    rows = [r for r in rows
            if runs.get((r["model"], r["code_name"]), (None,))[0] == r["run_id"]]
    data = _rows_to_csv(
        ["Model", "Document title", "Code name", "Quote", "Reason"],
        ((r["model"], r["document_title"], r["code_name"], r["quote"], r["reason"]) for r in rows))
    sp.upload_bytes(_csv_remote("coding_results.csv"), data)


# --- tests ------------------------------------------------------------------

# --- daily snapshot -----------------------------------------------------------
#
# One write-once copy of the whole in-memory DB per day under
# state/backups/YYYY-MM-DD/, so recovery never depends on SharePoint's limited
# version history. Created with create_bytes: an existing snapshot is never
# replaced. Nothing here deletes old snapshots; prune by hand if ever needed.

SNAPSHOT_EVERY_S = 6 * 3600


def _snapshot_remote(day: str) -> str:
    return f"{config.SHAREPOINT_STATE_DIR}/backups/{day}/{config.SHAREPOINT_DB_NAME}"


def snapshot_today() -> bool:
    """Write today's snapshot if there is none yet. True if one was written."""
    if not sp.configured():
        return False
    day = time.strftime("%Y-%m-%d", time.gmtime())
    with _lock:
        blob = db.dump_blob()
    if not _sane(blob):
        log.error("Not snapshotting an empty/corrupt DB image.")
        return False
    try:
        sp.create_bytes(_snapshot_remote(day), blob)
    except sp.AlreadyExists:
        return False
    except sp.SharePointError as e:
        log.warning("Daily snapshot failed (%s); will retry.", e)
        return False
    log.info("Wrote daily snapshot %s (%d bytes).", _snapshot_remote(day), len(blob))
    return True


def start_snapshot_timer() -> None:
    def loop():
        while True:
            try:
                snapshot_today()
            except Exception as e:  # noqa: BLE001
                log.warning("Daily snapshot error (%s).", e)
            time.sleep(SNAPSHOT_EVERY_S)
    threading.Thread(target=loop, name="state-snapshot", daemon=True).start()


def _reset_for_tests() -> None:
    global _etag, _known_defs
    flush_exports(timeout=30)
    definition_log._reset_for_tests()
    with _lock:
        _etag = None
        _known_defs = set()
