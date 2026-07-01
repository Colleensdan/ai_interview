"""Pull input data (and the results DB) from SharePoint at app startup.

Two modes (see config.MEMORY_DB):

* **File mode** — download the configured SharePoint folder to the local input
  dir and, only if the results DB doesn't exist yet, seed it from
  ``<SHAREPOINT_DIR>/coding_seed.sqlite``. Designed for a persistent disk that
  self-populates on first boot.

* **Memory mode (RAM-only)** — download the input files into process memory
  (never to disk) and hydrate the in-memory results DB from the authoritative
  ``state/coding.sqlite`` blob on **every** boot (via ``state_sync.hydrate``).
  SharePoint is the single durable store.

Both modes are graceful: if SharePoint is unreachable the app still boots with
whatever data is available.
"""

from __future__ import annotations

import logging
from pathlib import Path

import config
from . import sharepoint_io as sp
from . import state_sync
from .inputs import InputStore

log = logging.getLogger("app.startup_sync")

SEED_DB_NAME = "coding_seed.sqlite"


def _ignored(name: str) -> bool:
    return any(name.endswith(suf) for suf in config.IGNORE_SUFFIXES)


def _local_for(remote_path: str, base: str, root: Path) -> Path:
    """Map a drive-relative remote path under *base* to a local path under *root*."""
    rel = remote_path[len(base):].lstrip("/")
    return root / rel


def sync() -> InputStore:
    """Populate inputs (+ results DB) and return the InputStore the app reads from."""
    if config.MEMORY_DB:
        return _sync_memory()
    return _sync_disk()


# --- memory mode (RAM-only) -------------------------------------------------

def _download_into_store(base: str, remote: str, store: InputStore) -> int:
    count = 0
    for item in sp.list_folder(remote):
        if _ignored(item["name"]) or item["name"] == SEED_DB_NAME:
            continue
        if item["is_folder"]:
            count += _download_into_store(base, item["path"], store)
        else:
            rel = item["path"][len(base):].lstrip("/")
            if store.route(rel, sp.download_bytes(item["path"])):
                count += 1
    return count


def _sync_memory() -> InputStore:
    store = InputStore(from_disk=False)
    if not sp.configured():
        # Local dev running memory mode without SharePoint: fall back to reading
        # the on-disk sample inputs so the app is usable. (On Render, SharePoint
        # is always configured, so this branch never runs in production.)
        log.warning("Memory mode but SharePoint not configured; using local disk "
                    "inputs for this session.")
        return InputStore(from_disk=True)
    base = config.SHAREPOINT_DIR
    try:
        n = _download_into_store(base, base, store)
        log.info("Loaded %d input file(s) from SharePoint '%s' into memory.", n, base)
    except sp.SharePointError as e:
        log.warning("SharePoint input download failed (%s); inputs may be incomplete.", e)
    # Authoritative results DB: load from SharePoint on every boot.
    state_sync.hydrate()
    return store


# --- file mode (persistent disk) --------------------------------------------

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


def _sync_disk() -> InputStore:
    if not sp.configured():
        log.info("SharePoint not configured; using local data only.")
        return InputStore(from_disk=True)

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
        return InputStore(from_disk=True)
    try:
        root.mkdir(parents=True, exist_ok=True)
        n = _download_tree(base, base, root)
        log.info("Downloaded %d input file(s) from SharePoint '%s' to %s.", n, base, root)
    except sp.SharePointError as e:
        log.warning("SharePoint download failed (%s); using existing local data.", e)
    return InputStore(from_disk=True)
