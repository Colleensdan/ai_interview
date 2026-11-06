"""Fold the definition log into the live state database (for rollback).

The app keeps definition edits as write-once files in
``state/definition_log/`` and merges them in at boot. Code from before that
change does not know about the log, so before rolling back to it, run this to
copy every logged edit into ``state/coding.sqlite`` itself.

Safety:

* **Dry run by default.** ``--apply`` is needed to upload anything.
* **Backup first**, to ``~/ai_interview_backups/prefold_<stamp>/``, with the
  log files alongside.
* **Verified before upload.** The merged copy must pass ``integrity_check``;
  every row that was in the database must be byte-for-byte unchanged apart
  from which version is current; every valid log entry must be present; and
  the row count must grow by exactly the entries that were not already there.
* **If-Match** on the ETag that was downloaded, then a re-download to confirm.
* **The log is never modified or deleted.** Folding twice adds nothing.

Afterwards restart the app, so it loads the folded database.

    python fold_definition_log.py            # dry run: report only
    python fold_definition_log.py --apply
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

import config
from app import definition_log
from app import sharepoint_io as sp
from app import state_sync

BACKUP_ROOT = Path("/home/colleen/ai_interview_backups")

ROW = "id, code_name, version, definition, kappa, model, created_at"


def fold(blob: bytes, entries: list[tuple[str, bytes]]) -> tuple[bytes, dict]:
    """Return the folded database image and a report. Raises on any check."""
    conn = sqlite3.connect(":memory:")
    conn.deserialize(blob)
    before = {r[0]: r for r in conn.execute(f"SELECT {ROW} FROM code_definitions")}
    counts = definition_log.apply_entries(conn, entries)

    if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise RuntimeError("merged database fails integrity_check")
    after = {r[0]: r for r in conn.execute(f"SELECT {ROW} FROM code_definitions")}
    changed = [i for i, r in before.items() if after.get(i) != r]
    if changed:
        raise RuntimeError(f"{len(changed)} existing row(s) changed or vanished: {changed[:5]}")
    grew = len(after) - len(before)
    if grew != counts["added"] + counts["relocated"]:
        raise RuntimeError(f"row count grew by {grew}, expected "
                           f"{counts['added'] + counts['relocated']}")
    for name, data in entries:
        try:
            e = definition_log.decode(data)
        except definition_log.BadEntry:
            continue
        hit = conn.execute(
            "SELECT COUNT(*) FROM code_definitions WHERE code_name=? AND definition=? "
            "AND (version=? OR created_at=?)",
            (e["code_name"], e["definition"], e["version"], e["created_at"])).fetchone()[0]
        if hit < 1:
            raise RuntimeError(f"log entry {name} is not in the merged database")
    multi = conn.execute(
        "SELECT code_name FROM code_definitions WHERE is_current=1 "
        "GROUP BY code_name HAVING COUNT(*) != 1").fetchall()
    if multi:
        raise RuntimeError(f"codes without exactly one current version: {multi[:5]}")
    conn.commit()
    out = conn.serialize()
    conn.close()
    counts["rows_before"], counts["rows_after"] = len(before), len(after)
    return out, counts


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true", help="upload the folded database")
    args = ap.parse_args()

    remote = state_sync._db_remote()
    blob, tag = sp.download_with_etag(remote)
    items = [i for i in sp.list_folder(definition_log.log_dir())
             if not i["is_folder"] and i["name"].endswith(".json")]
    entries = [(i["name"], sp.download_bytes(i["path"])) for i in items]

    stamp = time.strftime("%Y%m%d_%H%M%S")
    bdir = BACKUP_ROOT / f"prefold_{stamp}"
    (bdir / "definition_log").mkdir(parents=True)
    (bdir / "coding.sqlite").write_bytes(blob)
    for name, data in entries:
        (bdir / "definition_log" / name).write_bytes(data)
    print(f"backup: {bdir}  ({len(blob):,} B, {len(entries)} log files, etag {tag})")

    out, report = fold(blob, entries)
    print("fold:", report)
    if not state_sync._sane(out):
        raise SystemExit("folded image failed the sanity check; nothing uploaded")
    if not args.apply:
        print("DRY RUN: nothing uploaded. Re-run with --apply.")
        return 0
    if report["added"] + report["relocated"] == 0:
        print("Nothing to fold; nothing uploaded.")
        return 0
    new_tag = sp.upload_bytes(remote, out, if_match=tag)
    check = sqlite3.connect(":memory:")
    check.deserialize(sp.download_bytes(remote))
    n = check.execute("SELECT COUNT(*) FROM code_definitions").fetchone()[0]
    if n != report["rows_after"]:
        print(f"WARNING: re-downloaded copy has {n} rows, expected {report['rows_after']}")
        return 1
    print(f"uploaded (etag {new_tag}); verified {n} definition rows. Restart the app.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
