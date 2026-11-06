"""Download a complete, verified copy of the live SharePoint data folder.

Read-only against SharePoint. Everything under the data folder (inputs, the
state database, conflict copies, CSV mirror, exports) is mirrored into
``~/ai_interview_backups/full_<stamp>/`` with a SHA-256 manifest, and every
SQLite file in it is opened and integrity-checked so a backup that will not
restore is noticed now rather than when it is needed.

    python backup_live.py
    python backup_live.py --copy-to /mnt/c/Users/<you>/Documents/ai_backups
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
import time
from pathlib import Path

import config
from app import sharepoint_io as sp

BACKUP_ROOT = Path("/home/colleen/ai_interview_backups")


def _walk(remote: str):
    for item in sp.list_folder(remote):
        if item["is_folder"]:
            yield from _walk(item["path"])
        else:
            yield item["path"]


def _check_sqlite(path: Path) -> dict:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        ok = conn.execute("PRAGMA integrity_check").fetchone()[0]
        out = {"integrity": ok}
        tables = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if "code_definitions" in tables:
            out["definition_versions"] = conn.execute(
                "SELECT COUNT(*) FROM code_definitions").fetchone()[0]
            out["codes"] = conn.execute(
                "SELECT COUNT(DISTINCT code_name) FROM code_definitions").fetchone()[0]
        for t in ("kappa", "coding_results"):
            if t in tables:
                out[t] = conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        return out
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--copy-to", type=Path,
                    help="also copy the finished backup here (a second location)")
    args = ap.parse_args()

    root = config.SHAREPOINT_DIR.strip("/")
    dest = BACKUP_ROOT / f"full_{time.strftime('%Y%m%d_%H%M%S')}"
    dest.mkdir(parents=True)
    manifest = {"source": root, "taken_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                "files": {}}
    for remote in _walk(root):
        rel = remote[len(root):].lstrip("/")
        data = sp.download_bytes(remote)
        local = dest / rel
        local.parent.mkdir(parents=True, exist_ok=True)
        local.write_bytes(data)
        entry = {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}
        if data[:16] == b"SQLite format 3\x00":
            entry["sqlite"] = _check_sqlite(local)
        manifest["files"][rel] = entry
        print(f"  {rel}  ({len(data):,} B)")

    (dest / "MANIFEST.json").write_text(json.dumps(manifest, indent=2))
    bad = [k for k, v in manifest["files"].items()
           if "sqlite" in v and v["sqlite"]["integrity"] != "ok"]
    print(f"\n{len(manifest['files'])} files -> {dest}")
    for k, v in manifest["files"].items():
        if "sqlite" in v:
            print(f"  {k}: {v['sqlite']}")
    if bad:
        print("INTEGRITY FAILURES:", bad)
        return 1

    if args.copy_to:
        target = args.copy_to / dest.name
        shutil.copytree(dest, target)
        for rel, v in manifest["files"].items():
            got = hashlib.sha256((target / rel).read_bytes()).hexdigest()
            if got != v["sha256"]:
                print(f"COPY MISMATCH: {rel}")
                return 1
        print(f"verified second copy -> {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
