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
from . import sharepoint_io as sp

log = logging.getLogger("app.state_sync")

_lock = threading.RLock()
_etag: str | None = None  # ETag of the remote DB blob we last read/wrote


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
        return _hydrate_from_seed()
    try:
        db.load_blob(blob)
    except db.IntegrityError as e:
        log.error("Remote state DB failed integrity check (%s); NOT loading it. "
                  "Starting from seed/empty; remote left untouched for recovery.", e)
        return False
    with _lock:
        _etag = tag
    log.info("Hydrated in-memory DB from SharePoint (%d bytes, etag=%s).", len(blob), tag)
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
            wrote = True
            log.info("Pushed state DB to SharePoint (%d bytes, etag=%s).", len(blob), new_tag)
        except sp.PreconditionFailed:
            # Either a concurrent writer (had _etag) or a create race (no _etag).
            _handle_conflict(blob)
        except sp.SharePointError as e:
            log.error("Failed to push state DB (%s); will retry on next write.", e)
            return False

        # CSV mirror is best-effort readability; failures never block the DB push.
        try:
            _export_csv_mirror()
        except Exception as e:  # noqa: BLE001
            log.warning("CSV mirror export/upload failed (%s); DB blob is still authoritative.", e)
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
        log.critical("Converged in-memory DB to remote; conflicting edits preserved "
                     "in the conflicts/ folder for manual merge.")
    except (sp.SharePointError, db.IntegrityError) as e:
        log.critical("Failed to converge to remote after conflict (%s).", e)


# --- CSV mirror (human-readable) --------------------------------------------

def _rows_to_csv(header: list[str], rows) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(header)
    for r in rows:
        w.writerow(list(r))
    return buf.getvalue().encode("utf-8")


def _export_csv_mirror() -> None:
    conn = db.connect(row_factory=True)
    try:
        _upload_definitions_csv(conn)
        _upload_kappa_csv(conn)
        _upload_coding_results_csv(conn)
    finally:
        conn.close()


def _safe_query(conn, sql: str):
    try:
        return conn.execute(sql).fetchall()
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
    rows = _safe_query(conn,
        "SELECT model, code_name, kappa, n_documents, n_human_present, n_llm_present "
        "FROM kappa k WHERE id=(SELECT MAX(id) FROM kappa "
        "WHERE model=k.model AND code_name=k.code_name) ORDER BY model, code_name")
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
         "n_human_present", "n_llm_present", "meets_target_>0.80"], out)
    sp.upload_bytes(_csv_remote("kappa.csv"), data)


def _upload_coding_results_csv(conn) -> None:
    rows = _safe_query(conn,
        "SELECT model, document_title, code_name, quote, reason FROM coding_results cr "
        "WHERE run_id=(SELECT run_id FROM coding_results WHERE model=cr.model "
        "ORDER BY id DESC LIMIT 1) ORDER BY model, document_title, code_name")
    if rows is None:
        return
    data = _rows_to_csv(
        ["Model", "Document title", "Code name", "Quote", "Reason"],
        ((r["model"], r["document_title"], r["code_name"], r["quote"], r["reason"]) for r in rows))
    sp.upload_bytes(_csv_remote("coding_results.csv"), data)


# --- tests ------------------------------------------------------------------

def _reset_for_tests() -> None:
    global _etag
    with _lock:
        _etag = None
