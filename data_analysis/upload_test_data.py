"""Upload the configured input data to SharePoint, plus the results DB as a seed.

Reuses the existing SharePoint auth/credentials (same env vars as
code/sharepoint.py) via app/sharepoint_io.py. What gets uploaded — the
transcript directory and the three workbooks — follows config, so this tracks
whichever data set the pipeline is pointed at.

Run from data_analysis/:
    python upload_test_data.py --dry-run    # list what would be uploaded
    python upload_test_data.py

The destination is AICODE_SHAREPOINT_DIR. Note that AICODE_SHAREPOINT_STATE_DIR
defaults to "<that folder>/state", so pointing this at a new folder also moves
where the authoritative results database lives — set both explicitly.

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
    parser.add_argument(
        "--dry-run", action="store_true",
        help="List what would be uploaded and where, without sending anything.",
    )
    args = parser.parse_args()

    base = config.SHAREPOINT_DIR  # configurable destination folder
    root = config.TEMPLATE_ROOT
    if args.dry_run:
        print(f"DRY RUN — destination folder: {base!r}")
        print(f"          results state dir : {config.SHAREPOINT_STATE_DIR!r}")
    elif not sp.configured():
        print("SharePoint env vars are not all set; aborting.", file=sys.stderr)
        return 2

    # (remote_path, local_path) pairs.
    uploads: list[tuple[str, Path]] = []

    if not args.seed_only:
        # Names and locations come from config so this follows the data rather
        # than needing an edit each time an export is renamed.
        interviews = Path(config.INTERVIEWS_DIR)
        remote_dir = interviews.name
        for p in sorted(interviews.rglob("*")) if interviews.is_dir() else []:
            if (p.is_file() and not _ignored(p.name)
                    and p.name.lower().endswith(tuple(config.TRANSCRIPT_EXTS))):
                uploads.append((f"{base}/{remote_dir}/{p.name}", p))

        for f in (Path(config.CODEBOOK_PATH),
                  Path(config.GROUND_TRUTH_QUOTES_PATH),
                  Path(config.GROUND_TRUTH_COUNTS_PATH)):
            if f.is_file():
                uploads.append((f"{base}/{f.name}", f))

    # Seed DB so a fresh deployment can populate itself on first boot.
    if Path(str(config.DB_PATH)).is_file():
        uploads.append((f"{base}/{config.SHAREPOINT_SEED_NAME}", Path(str(config.DB_PATH))))
    elif args.seed_only:
        print(f"No DB at {config.DB_PATH} to upload.", file=sys.stderr)
        return 2

    if not uploads:
        print(f"Nothing to upload from {root}", file=sys.stderr)
        return 2

    if args.dry_run:
        total = sum(local.stat().st_size for _, local in uploads)
        print(f"\nWould upload {len(uploads)} file(s), {total:,} bytes:")
        for remote, local in uploads[:5]:
            print(f"  {remote}  ({local.stat().st_size:,} bytes)")
        if len(uploads) > 5:
            print(f"  ... and {len(uploads) - 5} more")
        return 0

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
