"""The definition log's data-safety rules (app/definition_log.py).

Each test names the rule it holds the code to. A fake, versioned SharePoint
stands in for the real one (see conftest: no test can reach the real one).
"""

from __future__ import annotations

import sqlite3

import pytest

import config
import db
import fold_definition_log as fold_tool
from app import definition_log as dl
from app import sharepoint_io as sp
from app import state_sync
from app.definitions import DefinitionStore


class FakeSP:
    def __init__(self):
        self.files: dict[str, tuple[bytes, str]] = {}
        self._n = 0
        self.fail_create = False

    def _put(self, path, content):
        self._n += 1
        self.files[path] = (bytes(content), f"e{self._n}")
        return f"e{self._n}"

    def upload_bytes(self, path, content, if_match=None):
        cur = self.files.get(path)
        if if_match is not None and cur is not None and cur[1] != if_match:
            raise sp.PreconditionFailed("etag mismatch")
        return self._put(path, content)

    def create_bytes(self, path, content):
        if self.fail_create:
            raise sp.SharePointError("simulated outage")
        if path in self.files:
            raise sp.AlreadyExists(path)
        return self._put(path, content)

    def download_bytes(self, path):
        if path not in self.files:
            raise sp.SharePointError(f"404 {path}")
        return self.files[path][0]

    def download_with_etag(self, path):
        if path not in self.files:
            raise sp.SharePointError(f"404 {path}")
        return self.files[path]

    def etag(self, path):
        return self.files[path][1] if path in self.files else None

    def list_folder(self, remote):
        prefix = remote.rstrip("/") + "/"
        return [{"name": p[len(prefix):], "is_folder": False, "path": p}
                for p in sorted(self.files)
                if p.startswith(prefix) and "/" not in p[len(prefix):]]

    def logs(self):
        return {p: c for p, (c, _) in self.files.items() if "/definition_log/" in p}


@pytest.fixture
def fsp(monkeypatch, mem_db):
    f = FakeSP()
    monkeypatch.setattr(sp, "configured", lambda: True)
    for name in ("upload_bytes", "create_bytes", "download_bytes",
                 "download_with_etag", "etag", "list_folder"):
        monkeypatch.setattr(sp, name, getattr(f, name))
    state_sync._reset_for_tests()
    yield f
    state_sync._reset_for_tests()


def _store(journal=True):
    return DefinitionStore(config.DB_PATH, journal=dl.write_entry if journal else None)


def _seed_remote(fsp, rows):
    """Put a state DB holding *rows* (code, version, text, is_current) on the fake."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        "CREATE TABLE code_definitions (id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " code_name TEXT NOT NULL, version INTEGER NOT NULL, definition TEXT NOT NULL,"
        " kappa REAL, model TEXT, is_current INTEGER NOT NULL DEFAULT 0,"
        " created_at TEXT DEFAULT (datetime('now')));"
        "CREATE TABLE kappa (id INTEGER PRIMARY KEY, run_id TEXT, model TEXT,"
        " code_name TEXT, kappa REAL);")
    conn.executemany("INSERT INTO code_definitions (code_name, version, definition,"
                     " is_current, created_at) VALUES (?,?,?,?,'2026-08-01 10:00:00')", rows)
    conn.commit()
    fsp.files[state_sync._db_remote()] = (conn.serialize(), "e0")
    conn.close()


def _history(code):
    c = db.connect(row_factory=True)
    try:
        return [(r["version"], r["definition"], r["is_current"]) for r in c.execute(
            "SELECT * FROM code_definitions WHERE code_name=? ORDER BY version", (code,))]
    finally:
        c.close()


# --- durable before visible -------------------------------------------------------

def test_save_writes_one_log_file_and_never_the_database(fsp):
    _seed_remote(fsp, [("trust", 1, "v1 text", 1)])
    state_sync.hydrate()
    db_before = fsp.files[state_sync._db_remote()]
    row = _store().save_new("trust", "v2 text")
    assert row["version"] == 2
    assert len(fsp.logs()) == 1
    assert fsp.files[state_sync._db_remote()] == db_before


def test_a_failed_log_write_changes_nothing(fsp):
    _seed_remote(fsp, [("trust", 1, "v1 text", 1)])
    state_sync.hydrate()
    fsp.fail_create = True
    with pytest.raises(dl.LogUnavailable):
        _store().save_new("trust", "never saved")
    assert _history("trust") == [(1, "v1 text", 1)]


def test_saves_refused_until_the_log_has_been_read(fsp):
    _seed_remote(fsp, [("trust", 1, "v1 text", 1)])
    db.load_blob(fsp.files[state_sync._db_remote()][0])   # loaded, log never read
    with pytest.raises(dl.LogUnavailable):
        _store().save_new("trust", "too early")
    assert not fsp.logs()


def test_an_unreadable_database_keeps_saving_off(fsp, monkeypatch):
    _seed_remote(fsp, [("trust", 1, "v1 text", 1)])
    monkeypatch.setattr(sp, "download_with_etag",
                        lambda p: (_ for _ in ()).throw(sp.SharePointError("timeout")))
    monkeypatch.setattr(sp, "download_bytes",
                        lambda p: (_ for _ in ()).throw(sp.SharePointError("timeout")))
    state_sync.hydrate()
    assert not dl.ready()


def test_identical_text_is_not_saved_again(fsp):
    _seed_remote(fsp, [("trust", 1, "same", 1)])
    state_sync.hydrate()
    r = _store().save_new("trust", "same")
    assert r["unchanged"] is True and r["version"] == 1
    assert not fsp.logs()


# --- the log carries edits across restarts ---------------------------------------

def test_restart_restores_logged_edits_in_order(fsp):
    _seed_remote(fsp, [("trust", 1, "v1", 1)])
    state_sync.hydrate()
    s = _store()
    s.save_new("trust", "v2")
    s.save_new("trust", "v3")
    db._reset_anchor_for_tests()
    state_sync._reset_for_tests()
    state_sync.hydrate()
    assert _history("trust") == [(1, "v1", 0), (2, "v2", 0), (3, "v3", 1)]


def test_merging_twice_adds_nothing(fsp):
    _seed_remote(fsp, [("trust", 1, "v1", 1)])
    state_sync.hydrate()
    _store().save_new("trust", "v2")
    once = _history("trust")
    report = dl.load_and_apply()
    assert _history("trust") == once
    assert report["added"] == 0


def test_entry_already_in_the_database_is_recognised(fsp):
    """After a re-analysis upload the database holds the logged rows too."""
    _seed_remote(fsp, [("trust", 1, "v1", 1)])
    state_sync.hydrate()
    _store().save_new("trust", "v2")
    state_sync.push_state()                         # DB now contains v2 as well
    conn = db.connect()
    conn.execute("DELETE FROM definition_log_applied")   # forget what was applied
    conn.commit()
    conn.close()
    db._reset_anchor_for_tests()
    state_sync._reset_for_tests()
    state_sync.hydrate()
    assert _history("trust") == [(1, "v1", 0), (2, "v2", 1)]


# --- merging never drops a version ---------------------------------------------

def test_a_version_collision_keeps_both_texts(fsp):
    _seed_remote(fsp, [("trust", 1, "v1", 0), ("trust", 2, "remote v2", 1)])
    entry = dl.encode("trust", 2, "logged v2", "2026-11-06 10:00:00", 1)
    fsp.files[f"{dl.log_dir()}/20261106100000__trust__v2__abcd1234.json"] = (entry, "x")
    state_sync.hydrate()
    texts = [t for _, t, _ in _history("trust")]
    assert "remote v2" in texts and "logged v2" in texts
    assert _history("trust")[-1] == (3, "logged v2", 1)
    # and a second merge does not relocate it again
    dl.load_and_apply()
    conn = db.connect()
    conn.execute("DELETE FROM definition_log_applied")
    conn.commit()
    conn.close()
    dl.load_and_apply()
    assert [t for _, t, _ in _history("trust")].count("logged v2") == 1


def test_a_corrupt_log_file_is_ignored_not_fatal(fsp):
    _seed_remote(fsp, [("trust", 1, "v1", 1)])
    fsp.files[f"{dl.log_dir()}/20261106__bad.json"] = (b"{not json", "x")
    state_sync.hydrate()
    assert dl.ready()
    assert _history("trust") == [(1, "v1", 1)]
    assert f"{dl.log_dir()}/20261106__bad.json" in fsp.files   # left in place


def test_log_files_are_never_overwritten(fsp):
    path = f"{dl.log_dir()}/x.json"
    fsp.files[path] = (b"original", "e")
    with pytest.raises(sp.AlreadyExists):
        sp.create_bytes(path, b"replacement")
    assert fsp.files[path][0] == b"original"


# --- shrink guard and conflicts ----------------------------------------------------

def test_push_refused_if_a_known_version_is_missing(fsp):
    _seed_remote(fsp, [("trust", 1, "v1", 0), ("trust", 2, "v2", 1)])
    state_sync.hydrate()
    before = fsp.files[state_sync._db_remote()]
    conn = db.connect()
    conn.execute("DELETE FROM code_definitions WHERE version=2")
    conn.commit()
    conn.close()
    assert state_sync.push_state() is False
    assert fsp.files[state_sync._db_remote()] == before


def test_conflict_reload_keeps_logged_edits(fsp):
    _seed_remote(fsp, [("trust", 1, "v1", 1)])
    state_sync.hydrate()
    _store().save_new("trust", "v2 in log")
    # another writer replaces the remote database (e.g. a results sync)
    blob, _ = fsp.files[state_sync._db_remote()]
    fsp.upload_bytes(state_sync._db_remote(), blob)
    state_sync.push_state()          # conflict: converge to remote, re-merge log
    assert _history("trust")[-1] == (2, "v2 in log", 1)


def test_seed_reports_how_many_codes_it_added(fsp):
    """Boot uploads the database only when seeding added something (main.py)."""
    s = _store(journal=False)
    assert s.seed([("trust", "x")], {}, None) == 1
    assert s.seed([("trust", "x")], {}, None) == 0


# --- daily snapshot ------------------------------------------------------------------

def test_daily_snapshot_is_written_once_and_never_replaced(fsp):
    _seed_remote(fsp, [("trust", 1, "v1", 1)])
    state_sync.hydrate()
    assert state_sync.snapshot_today() is True
    snaps = [p for p in fsp.files if "/backups/" in p]
    assert len(snaps) == 1
    first = fsp.files[snaps[0]]
    _store().save_new("trust", "v2")
    assert state_sync.snapshot_today() is False
    assert fsp.files[snaps[0]] == first


# --- rollback tool -----------------------------------------------------------------

def test_fold_adds_logged_edits_and_changes_no_existing_row(fsp):
    _seed_remote(fsp, [("trust", 1, "v1", 0), ("trust", 2, "v2", 1), ("other", 1, "o1", 1)])
    state_sync.hydrate()
    _store().save_new("trust", "v3")
    _store().save_new("other", "o2")
    blob = fsp.files[state_sync._db_remote()][0]
    entries = [(p.rsplit("/", 1)[1], c) for p, c in fsp.logs().items()]
    out, report = fold_tool.fold(blob, entries)
    assert report["added"] == 2 and report["rows_after"] == report["rows_before"] + 2
    conn = sqlite3.connect(":memory:")
    conn.deserialize(out)
    cur = dict(conn.execute("SELECT code_name, definition FROM code_definitions "
                            "WHERE is_current=1").fetchall())
    assert cur == {"trust": "v3", "other": "o2"}
    # folding the folded image again adds nothing
    _, again = fold_tool.fold(out, entries)
    assert again["added"] == 0 and again["relocated"] == 0
