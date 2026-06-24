"""One-off: create the SharePoint "Test Data" folder and upload local test data.

Reuses the existing SharePoint auth/credentials (same env vars as
code/sharepoint.py) via app/sharepoint_io.py. Uploads the interviews, codebook,
ground truth, and the count table ("Code Transcript Table" == CountData.xlsx),
plus the existing results DB as a seed so a fresh Render disk can self-populate.

Run from data_analysis/:
    python upload_test_data.py

Reports clearly if the app registration is read-only (HTTP 403 on upload).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import config
from app import sharepoint_io as sp

IGNORE = config.IGNORE_SUFFIXES


def _ignored(name: str) -> bool:
    return any(name.endswith(suf) for suf in IGNORE)


def main() -> int:
    parser = argparse.ArgumentParser(description="Upload test data to SharePoint 'Test Data'.")
    parser.add_argument(
        "--seed-only", action="store_true",
        help="Only re-upload coding_seed.sqlite (the results DB). Use after a "
             "full re-run so a fresh Render disk re-seeds with the new data.",
    )
    args = parser.parse_args()

    base = config.SHAREPOINT_DIR  # "Test Data" (configurable)
    root = config.TEMPLATE_ROOT
    if not sp.configured():
        print("SharePoint env vars are not all set; aborting.", file=sys.stderr)
        return 2

    # (remote_path, local_path) pairs.
    uploads: list[tuple[str, Path]] = []

    if not args.seed_only:
        interviews = root / "Interviews"
        for p in sorted(interviews.iterdir()) if interviews.is_dir() else []:
            if p.is_file() and not _ignored(p.name) and p.suffix.lower() == ".docx":
                uploads.append((f"{base}/Interviews/{p.name}", p))

        for fname in ("Codebook.xlsx", "Ground Truth.xlsx", "CountData.xlsx"):
            f = root / fname
            if f.is_file():
                uploads.append((f"{base}/{fname}", f))

    # Seed DB so the deployed app can populate /var/data on first boot.
    if Path(config.DB_PATH).is_file():
        uploads.append((f"{base}/coding_seed.sqlite", Path(config.DB_PATH)))
    elif args.seed_only:
        print(f"No DB at {config.DB_PATH} to upload.", file=sys.stderr)
        return 2

    if not uploads:
        print(f"Nothing to upload from {root}", file=sys.stderr)
        return 2

    print(f"Uploading {len(uploads)} file(s) to SharePoint folder '{base}/' ...")
    ok = 0
    for remote, local in uploads:
        try:
            sp.upload_bytes(remote, local.read_bytes())
            print(f"  ✓ {remote}  ({local.stat().st_size:,} bytes)")
            ok += 1
        except sp.SharePointError as e:
            msg = str(e)
            if "403" in msg:
                print("\n  ✗ 403 Forbidden — the app registration appears to be "
                      "READ-ONLY.\n    Grant Files.ReadWrite.All / Sites.ReadWrite.All "
                      "(application) and admin-consent, then re-run.", file=sys.stderr)
                print(f"    (failed on {remote})", file=sys.stderr)
                return 3
            print(f"  ✗ {remote}: {msg}", file=sys.stderr)
    print(f"\nDone: {ok}/{len(uploads)} uploaded into '{base}/'.")
    return 0 if ok == len(uploads) else 1


if __name__ == "__main__":
    raise SystemExit(main())
