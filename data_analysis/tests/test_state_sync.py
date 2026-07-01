"""Write-back safety: round-trip durability, integrity guards, and that an
ETag conflict never clobbers data.

Uses a fake, versioned SharePoint (ETag + If-Match semantics) so we exercise the
real concurrency logic without network access.
"""

from __future__ import annotations

import csv
import io

import pytest

import config
import db
from app import sharepoint_io as sp
from app import state_sync


class FakeStore:
    """In-memory stand-in for the SharePoint drive, with ETag/If-Match."""

    def __init__(self):
        self.files: dict[str, tuple[bytes, str]] = {}  # path -> (content, etag)
        self._n = 0

    def upload(self, path, content, if_match=None):
        cur = self.files.get(path)
        if if_match is not None and cur is not None and cur[1] != if_match:
            raise sp.PreconditionFailed("etag mismatch")
        self._n += 1
        tag = f"etag-{self._n}"
        self.files[path] = (bytes(content), tag)
        return tag

    def download_with_etag(self, path):
        if path not in self.files:
            raise sp.SharePointError(f"404 {path}")
        return self.files[path]

    def download_bytes(self, path):
        if path not in self.files:
            raise sp.SharePointError(f"404 {path}")
        return self.files[path][0]

    def etag(self, path):
        return self.files[path][1] if path in self.files else None


@pytest.fixture
def fake_sp(monkeypatch, mem_db):
    store = FakeStore()
    monkeypatch.setattr(sp, "configured", lambda: True)
    monkeypatch.setattr(sp, "upload_bytes",
                        lambda path, content, if_match=None: store.upload(path, content, if_match))
    monkeypatch.setattr(sp, "download_with_etag", store.download_with_etag)
    monkeypatch.setattr(sp, "download_bytes", store.download_bytes)
    monkeypatch.setattr(sp, "etag", store.etag)
    state_sync._reset_for_tests()
    yield store
    state_sync._reset_for_tests()


def _seed_db():
    """Create a minimal but realistic DB in the shared in-memory database."""
    c = db.connect()
    c.executescript(
        "CREATE TABLE code_definitions(code_name TEXT, version INT, definition TEXT,"
        " kappa REAL, model TEXT, is_current INT, created_at TEXT);"
        "CREATE TABLE kappa(id INTEGER PRIMARY KEY, run_id TEXT, model TEXT, code_name TEXT,"
        " kappa REAL, n_documents INT, n_human_present INT, n_llm_present INT);"
    )
    c.execute("INSERT INTO code_definitions VALUES ('A: trust',1,'def v1',NULL,NULL,1,'t')")
    c.execute("INSERT INTO kappa(run_id,model,code_name,kappa,n_documents,n_human_present,n_llm_present)"
              " VALUES ('r1','m','A: trust',0.9,7,5,5)")
    c.commit()
    c.close()


DB_REMOTE = None  # resolved per test via state_sync._db_remote()


def test_push_then_restart_hydrate_recovers_data(fake_sp):
    _seed_db()
    assert state_sync.push_state() is True
    assert state_sync._db_remote() in fake_sp.files

    # Simulate a Render restart: wipe in-memory DB entirely.
    db._reset_anchor_for_tests()
    state_sync._reset_for_tests()
    c = db.connect()
    import sqlite3
    with pytest.raises(sqlite3.OperationalError):
        c.execute("SELECT * FROM code_definitions")
    c.close()

    # Hydrate from SharePoint — the edit must come back.
    assert state_sync.hydrate() is True
    c = db.connect(row_factory=True)
    assert c.execute("SELECT definition FROM code_definitions").fetchone()["definition"] == "def v1"
    c.close()


def test_edit_after_push_is_durable_after_restart(fake_sp):
    _seed_db()
    state_sync.push_state()
    # Make an edit and push again.
    c = db.connect()
    c.execute("INSERT INTO code_definitions VALUES ('A: trust',2,'def v2 EDITED',NULL,NULL,1,'t')")
    c.execute("UPDATE code_definitions SET is_current=0 WHERE version=1")
    c.commit(); c.close()
    assert state_sync.push_state() is True

    db._reset_anchor_for_tests()
    state_sync._reset_for_tests()
    assert state_sync.hydrate() is True
    c = db.connect(row_factory=True)
    cur = c.execute("SELECT definition FROM code_definitions WHERE is_current=1").fetchone()
    assert cur["definition"] == "def v2 EDITED"
    c.close()


def test_push_refuses_empty_or_corrupt_db(fake_sp):
    # No tables at all -> not sane -> must NOT upload (would clobber a good remote).
    assert state_sync.push_state() is False
    assert state_sync._db_remote() not in fake_sp.files


def test_push_does_not_overwrite_good_remote_with_empty(fake_sp):
    _seed_db()
    state_sync.push_state()
    good = fake_sp.files[state_sync._db_remote()]
    # Now wipe memory to empty and try to push — remote must be preserved.
    db._reset_anchor_for_tests()
    state_sync._reset_for_tests()
    db.connect()  # empty DB
    assert state_sync.push_state() is False
    assert fake_sp.files[state_sync._db_remote()] == good


def test_etag_conflict_preserves_both_and_does_not_clobber(fake_sp):
    _seed_db()
    state_sync.push_state()  # etag now etag-... , remote has our v1
    db_remote = state_sync._db_remote()
    remote_before = fake_sp.files[db_remote]

    # Another writer changes the remote (new etag) behind our back.
    other_blob = fake_sp.files[db_remote][0]
    fake_sp.files[db_remote] = (other_blob + b"", "etag-OTHER")

    # We make a local edit and push -> should hit 412 conflict handling.
    c = db.connect()
    c.execute("INSERT INTO code_definitions VALUES ('A: trust',2,'LOCAL EDIT',NULL,NULL,1,'t')")
    c.commit(); c.close()
    result = state_sync.push_state()

    # Main remote NOT overwritten by our blob (still the other writer's etag).
    assert fake_sp.files[db_remote][1] == "etag-OTHER"
    # Our version preserved in a conflicts/ copy — nothing lost.
    conflict_paths = [p for p in fake_sp.files if "/conflicts/" in p]
    assert len(conflict_paths) == 1
    assert result is False  # main DB was not durably written this call


def test_graceful_when_sharepoint_not_configured(monkeypatch, mem_db):
    monkeypatch.setattr(sp, "configured", lambda: False)
    state_sync._reset_for_tests()
    _seed_db()
    assert state_sync.push_state() is False   # no-op, no raise
    assert state_sync.hydrate() is False


def test_hydrate_rejects_corrupt_remote_without_clobbering_memory(fake_sp):
    _seed_db()
    # Put a corrupt blob at the remote DB path.
    fake_sp.files[state_sync._db_remote()] = (b"not a sqlite db at all", "etag-bad")
    # Hydrate should refuse and keep our in-memory data.
    assert state_sync.hydrate() is False
    c = db.connect(row_factory=True)
    assert c.execute("SELECT definition FROM code_definitions").fetchone()["definition"] == "def v1"
    c.close()


def test_transient_hydrate_failure_does_not_clobber_existing_remote(fake_sp):
    # Remote already holds real data with an ETag we never read (hydrate "failed").
    _seed_db()
    real_remote = db.dump_blob()
    fake_sp.files[state_sync._db_remote()] = (real_remote, "etag-REAL")
    state_sync._reset_for_tests()  # _etag is None (as if hydrate failed)

    # Our in-memory DB is just the seed; pushing must NOT overwrite the real blob.
    result = state_sync.push_state()
    assert result is False
    assert fake_sp.files[state_sync._db_remote()][1] == "etag-REAL"  # untouched
    # Our version preserved as a conflict copy — nothing lost.
    assert any("/conflicts/" in p for p in fake_sp.files)


def test_hydrate_falls_back_to_legacy_seed_then_creates_state(fake_sp):
    # Build a legacy seed blob with real content and place it at coding_seed.sqlite.
    _seed_db()
    seed_blob = db.dump_blob()
    seed_remote = f"{config.SHAREPOINT_DIR}/{config.SHAREPOINT_SEED_NAME}"
    fake_sp.files[seed_remote] = (seed_blob, "seed-etag")

    # Fresh memory + no state/coding.sqlite yet -> hydrate uses the seed.
    db._reset_anchor_for_tests()
    state_sync._reset_for_tests()
    assert state_sync._db_remote() not in fake_sp.files
    assert state_sync.hydrate() is True
    c = db.connect(row_factory=True)
    assert c.execute("SELECT definition FROM code_definitions").fetchone()["definition"] == "def v1"
    c.close()

    # First push must CREATE state/coding.sqlite (migration complete).
    assert state_sync.push_state() is True
    assert state_sync._db_remote() in fake_sp.files


def test_first_boot_creates_remote_when_absent(fake_sp):
    _seed_db()
    state_sync._reset_for_tests()  # _etag None, remote absent
    assert state_sync._db_remote() not in fake_sp.files
    assert state_sync.push_state() is True
    assert state_sync._db_remote() in fake_sp.files


def test_csv_mirror_uploaded_with_readable_content(fake_sp):
    _seed_db()
    state_sync.push_state()
    defs_path = state_sync._csv_remote("definitions.csv")
    assert defs_path in fake_sp.files
    content = fake_sp.files[defs_path][0].decode("utf-8")
    reader = list(csv.reader(io.StringIO(content)))
    assert reader[0] == ["code_name", "version", "definition", "kappa", "model",
                         "is_current", "created_at"]
    assert any(row[0] == "A: trust" and row[2] == "def v1" for row in reader[1:])

    kappa_path = state_sync._csv_remote("kappa.csv")
    assert kappa_path in fake_sp.files
    khead = list(csv.reader(io.StringIO(fake_sp.files[kappa_path][0].decode())))[0]
    assert khead[:3] == ["Model", "Code", "Cohen's kappa"]
