"""Pull input data (and seed the results DB) from SharePoint at app startup.

Downloads the configured SharePoint folder (default "Test Data") into the local
input dir, and — only if the results DB doesn't exist yet — seeds it from
``<SHAREPOINT_DIR>/coding_seed.sqlite``. Designed for Render: a fresh persistent
disk self-populates on first boot.

Behaviour (per agreed defaults):
- Cache: skip the input download if data is already present, unless
  AICODE_SP_REFRESH is set.
- Graceful: if SharePoint is unreachable or scope is read-blocked, log a warning
  and continue with whatever is already on disk (never crash the boot).
"""

from __future__ import annotations

import logging
from pathlib import Path

import config
from . import sharepoint_io as sp

log = logging.getLogger("app.startup_sync")

SEED_DB_NAME = "coding_seed.sqlite"


def _ignored(name: str) -> bool:
    return any(name.endswith(suf) for suf in config.IGNORE_SUFFIXES)


def _local_for(remote_path: str, base: str, root: Path) -> Path:
    """Map a drive-relative remote path under *base* to a local path under *root*."""
    rel = remote_path[len(base):].lstrip("/")
    return root / rel


def _download_tree(base: str, remote: str, root: Path) -> int:
    count = 0
    for item in sp.list_folder(remote):
        if _ignored(item["name"]):
            continue
        if item["is_folder"]:
            count += _download_tree(base, item["path"], root)
        elif item["name"] != SEED_DB_NAME:  # seed handled separately
            dest = _local_for(item["path"], base, root)
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(sp.download_bytes(item["path"]))
            count += 1
    return count


def sync() -> None:
    if not sp.configured():
        log.info("SharePoint not configured; using local data only.")
        return

    base = config.SHAREPOINT_DIR
    root = Path(config.TEMPLATE_ROOT)

    # 1. Seed the results DB if it isn't there yet.
    db_path = Path(config.DB_PATH)
    if not db_path.exists():
        try:
            data = sp.download_bytes(f"{base}/{SEED_DB_NAME}")
            db_path.parent.mkdir(parents=True, exist_ok=True)
            db_path.write_bytes(data)
            log.info("Seeded results DB from SharePoint (%d bytes).", len(data))
        except sp.SharePointError as e:
            log.warning("Could not seed DB from SharePoint (%s); starting empty.", e)

    # 2. Download input data (cache unless refresh requested).
    interviews = Path(config.INTERVIEWS_DIR)
    have_data = interviews.is_dir() and any(interviews.glob("*.docx"))
    if have_data and not config.SHAREPOINT_REFRESH:
        log.info("Input data already present at %s; skipping download.", root)
        return
    try:
        root.mkdir(parents=True, exist_ok=True)
        n = _download_tree(base, base, root)
        log.info("Downloaded %d input file(s) from SharePoint '%s' to %s.", n, base, root)
    except sp.SharePointError as e:
        log.warning("SharePoint download failed (%s); using existing local data.", e)
