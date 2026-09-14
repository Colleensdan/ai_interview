"""Publish a completed local run to the live SharePoint app database.

The live app's single durable store is ``<state dir>/coding.sqlite`` on
SharePoint. That one file holds BOTH the analysis results and the reviewers'
code definitions, so publishing results means writing the file the definitions
live in. Everything here is built around that fact.

Safety design
-------------
* **Merge, never replace.** The live DB is downloaded, the run's rows are
  inserted into it, and the result is uploaded. The live file is never
  overwritten with a locally-built database, which would delete every
  definition version the reviewers have written.
* **Definitions are checked, not trusted.** ``code_definitions`` is fingerprinted
  before and after the merge and the upload is refused if the fingerprint moves.
  A bug in this script therefore cannot silently edit or drop a definition.
* **Full runs only.** ``runs.is_full`` is written by run_pipeline.py at run time;
  a scope-limited run (``--max-codes`` / ``--max-docs``) is refused, because its
  kappas cover a truncated code or document set and would read in the app as
  real results. There is deliberately no flag to override this.
* **Backup first.** The current live DB is downloaded to the local backup
  directory before anything is written, every time.
* **Optimistic concurrency.** The upload carries ``If-Match`` on the ETag the
  download returned, so a concurrent writer (the running app) causes a clean
  failure instead of a lost update.
* **Idempotent.** Re-syncing the same run replaces that run's rows rather than
  duplicating them.

    python sync_to_live.py --dry-run              # newest full run, no writes
    python sync_to_live.py
    python sync_to_live.py --run-id 20260906_173140_full_newgt_curdefs
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
import time
from pathlib import Path

import config
import db as _db
from app import sharepoint_io as sp
from pipeline import storage

BACKUP_ROOT = Path("/home/colleen/ai_interview_backups")

#: Tables that carry a run_id and are therefore synced per run.
RUN_TABLES = ("coding_results", "count_matrix", "majority_vote_matrix", "kappa")


def _remote_db() -> str:
    return f"{config.SHAREPOINT_STATE_DIR}/{config.SHAREPOINT_DB_NAME}"


def definitions_fingerprint(conn: sqlite3.Connection) -> str:
    """Hash of every definition row's identity and text.

    Deliberately covers ``definition`` and ``version`` but NOT the ``kappa`` /
    ``model`` columns: this script does not touch those, but the app writes them
    legitimately, and a fingerprint that moved whenever the app recorded a kappa
    would cry wolf. What must never change here is the definition TEXT.
    """
    rows = conn.execute(
        "SELECT id, code_name, version, definition, is_current "
        "FROM code_definitions ORDER BY id").fetchall()
    h = hashlib.sha256()
    for r in rows:
        h.update(repr(tuple(r)).encode("utf-8"))
    return f"{len(rows)}:{h.hexdigest()}"


def open_local(path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def load_remote(blob: bytes) -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.deserialize(_db._as_rollback_image(blob))
    conn.row_factory = sqlite3.Row
    if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise RuntimeError("live database failed its integrity check; aborting")
    return conn


def pick_run(local: sqlite3.Connection, run_id: str | None) -> sqlite3.Row:
    if run_id:
        row = local.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            raise SystemExit(
                f"No metadata for run {run_id!r}. Runs recorded locally:\n  "
                + "\n  ".join(r["run_id"] for r in
                              local.execute("SELECT run_id FROM runs ORDER BY run_id")))
    else:
        row = local.execute(
            "SELECT * FROM runs WHERE is_full=1 ORDER BY run_id DESC LIMIT 1").fetchone()
        if row is None:
            raise SystemExit("No full run recorded locally — nothing to publish.")
    if not row["is_full"]:
        raise SystemExit(
            f"Refusing to publish {row['run_id']!r}: it is a partial run "
            f"({row['n_codes']} code(s), {row['n_documents']} document(s)). Pilot "
            f"and test runs are never synced to the live app — their kappas cover "
            f"a truncated set and would read there as real results.")
    return row


def copy_rows(local, remote, table: str, run_id: str) -> int:
    """Replace *table*'s rows for *run_id* in the remote DB with the local ones."""
    cols = [r[1] for r in local.execute(f"PRAGMA table_info({table})")
            if r[1] != "id"]           # let the remote assign its own ids
    remote.execute(f"DELETE FROM {table} WHERE run_id=?", (run_id,))
    rows = local.execute(
        f"SELECT {','.join(cols)} FROM {table} WHERE run_id=?", (run_id,)).fetchall()
    remote.executemany(
        f"INSERT INTO {table} ({','.join(cols)}) "
        f"VALUES ({','.join('?' * len(cols))})",
        [tuple(r) for r in rows])
    return len(rows)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run-id", default=None,
                    help="Run to publish. Default: the newest FULL local run.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Report what would be published; write nothing.")
    args = ap.parse_args()

    if not sp.configured():
        raise SystemExit("SharePoint is not configured (see .env); nothing to sync to.")

    local = open_local(config.DB_PATH)
    run = pick_run(local, args.run_id)
    run_id = run["run_id"]
    print(f"run {run_id}")
    print(f"  full={bool(run['is_full'])}  codes={run['n_codes']}  "
          f"documents={run['n_documents']}  definitions={run['definitions']}")
    print(f"  models: {run['models']}")

    counts = {t: local.execute(
        f"SELECT COUNT(*) FROM {t} WHERE run_id=?", (run_id,)).fetchone()[0]
        for t in RUN_TABLES}
    gt_rows = local.execute("SELECT COUNT(*) FROM ground_truth_matrix").fetchone()[0]
    for t, n in counts.items():
        print(f"    {t:24s} {n:>8,} rows")
    print(f"    {'ground_truth_matrix':24s} {gt_rows:>8,} rows (snapshot, replaces remote)")
    if not any(counts.values()):
        raise SystemExit(f"Run {run_id!r} has no rows to publish.")

    # --- backup the live DB before touching it, every time -------------------
    stamp = time.strftime("%Y%m%d_%H%M%S")
    blob, etag = sp.download_with_etag(_remote_db())
    bdir = BACKUP_ROOT / f"presync_{stamp}"
    bdir.mkdir(parents=True, exist_ok=True)
    (bdir / "coding.sqlite").write_bytes(blob)
    (bdir / "MANIFEST.json").write_text(json.dumps({
        "taken_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "remote": _remote_db(), "etag": etag, "bytes": len(blob),
        "sha256": hashlib.sha256(blob).hexdigest(),
        "about_to_publish": run_id,
    }, indent=2))
    print(f"\n  backed up live DB -> {bdir}  ({len(blob):,} B, etag {etag})")

    remote = load_remote(blob)
    before = definitions_fingerprint(remote)
    n_defs = remote.execute("SELECT COUNT(*) FROM code_definitions").fetchone()[0]
    print(f"  live DB holds {n_defs} definition version(s); fingerprint {before[:24]}...")

    if args.dry_run:
        print("\n--dry-run: nothing written.")
        return 0

    # --- merge ---------------------------------------------------------------
    # The live DB predates the runs table; create anything missing before merging.
    remote.executescript(storage._SCHEMA)
    moved = {t: copy_rows(local, remote, t, run_id) for t in RUN_TABLES}
    remote.execute("DELETE FROM ground_truth_matrix WHERE source='human_ground_truth'")
    remote.executemany(
        "INSERT INTO ground_truth_matrix (code_name, document, count) VALUES (?,?,?)",
        [tuple(r) for r in local.execute(
            "SELECT code_name, document, count FROM ground_truth_matrix")])
    # Carry the run metadata across so the live DB records what it was given.
    cols = [r[1] for r in local.execute("PRAGMA table_info(runs)")]
    remote.execute("DELETE FROM runs WHERE run_id=?", (run_id,))
    remote.execute(
        f"INSERT INTO runs ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
        tuple(run[c] for c in cols))
    remote.commit()

    # --- the guard that matters ---------------------------------------------
    after = definitions_fingerprint(remote)
    if after != before:
        raise SystemExit(
            "ABORTED: the merge changed code_definitions "
            f"({before[:24]}... -> {after[:24]}...). Nothing was uploaded; the "
            f"live database is untouched and a backup is at {bdir}.")
    if remote.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise SystemExit("ABORTED: merged database failed its integrity check; "
                         "nothing uploaded.")
    print(f"  definitions unchanged by the merge ({n_defs} versions, fingerprint match)")

    # --- publish -------------------------------------------------------------
    out = remote.serialize()
    remote.close()
    try:
        new_etag = sp.upload_bytes(_remote_db(), out, if_match=etag)
    except sp.PreconditionFailed:
        raise SystemExit(
            "ABORTED: the live database changed while we were merging (the app "
            "probably wrote to it). Nothing was overwritten. Re-run to retry.")
    print(f"  uploaded {len(out):,} B (etag {new_etag})")

    # --- verify what actually landed -----------------------------------------
    check = load_remote(sp.download_bytes(_remote_db()))
    ok = definitions_fingerprint(check) == before
    served = {t: check.execute(
        f"SELECT COUNT(*) FROM {t} WHERE run_id=?", (run_id,)).fetchone()[0]
        for t in RUN_TABLES}
    check.close()
    print("\nverified on the live copy:")
    print(f"  definitions intact: {ok}")
    for t in RUN_TABLES:
        mark = "ok" if served[t] == moved[t] else "MISMATCH"
        print(f"  {t:24s} {served[t]:>8,} rows  {mark}")
    if not ok or any(served[t] != moved[t] for t in RUN_TABLES):
        print(f"\n!! Verification failed. Restore from {bdir}/coding.sqlite")
        return 1
    print(f"\nDone. Run {run_id} is live. Pre-sync backup: {bdir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
