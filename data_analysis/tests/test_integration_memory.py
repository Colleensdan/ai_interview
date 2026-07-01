"""End-to-end: the RAM-only app must never lose data across a restart.

Drives the real FastAPI app (auth, startup hydrate, definition edit, write-back)
in memory mode against a faithful fake SharePoint that (a) serves the real sample
inputs and (b) versions the state blob with ETags. The headline test edits a
definition, simulates a full Render restart (in-memory DB wiped), and asserts the
edit is still there — proving SharePoint is the durable source of truth.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import config
import db
from app import sharepoint_io as sp
from app import state_sync

TEMPLATE = Path(config.TEMPLATE_ROOT)
pytestmark = pytest.mark.skipif(
    not TEMPLATE.is_dir(), reason="sample input data not present")


class FakeSharePoint:
    """Serves inputs from the local sample tree; versions state blobs with ETags."""

    def __init__(self, root: Path, base: str):
        self.root = root
        self.base = base
        self.state: dict[str, tuple[bytes, str]] = {}
        self._n = 0

    def configured(self):
        return True

    def _local(self, remote: str) -> Path:
        rel = remote[len(self.base):].lstrip("/")
        return self.root / rel

    def list_folder(self, remote: str):
        d = self._local(remote)
        if not d.is_dir():
            return []
        out = []
        for p in sorted(d.iterdir()):
            out.append({"name": p.name, "is_folder": p.is_dir(),
                        "path": f"{remote.rstrip('/')}/{p.name}"})
        return out

    def download_bytes(self, remote: str) -> bytes:
        if remote in self.state:
            return self.state[remote][0]
        p = self._local(remote)
        if not p.is_file():
            raise sp.SharePointError(f"404 {remote}")
        return p.read_bytes()

    def download_with_etag(self, remote: str):
        if remote in self.state:
            return self.state[remote]
        raise sp.SharePointError(f"404 {remote}")

    def etag(self, remote: str):
        return self.state[remote][1] if remote in self.state else None

    def upload_bytes(self, remote: str, content: bytes, if_match=None):
        cur = self.state.get(remote)
        if if_match is not None and cur is not None and cur[1] != if_match:
            raise sp.PreconditionFailed("etag mismatch")
        self._n += 1
        tag = f"e{self._n}"
        self.state[remote] = (bytes(content), tag)
        return tag


@pytest.fixture
def fake_env(monkeypatch, mem_db):
    fake = FakeSharePoint(TEMPLATE, config.SHAREPOINT_DIR)
    for name in ("configured", "list_folder", "download_bytes",
                 "download_with_etag", "etag", "upload_bytes"):
        monkeypatch.setattr(sp, name, getattr(fake, name))
    monkeypatch.setattr(config, "AUTH_USERNAME", "u", raising=False)
    monkeypatch.setattr(config, "AUTH_PASSWORD", "p", raising=False)
    state_sync._reset_for_tests()
    return fake


def _login(client):
    r = client.post("/login", data={"username": "u", "password": "p", "next": "/"},
                    follow_redirects=False)
    assert r.status_code == 302


def _some_code(client) -> str:
    ov = client.get("/api/overview").json()
    entries = ov["success"] + ov["fail"]
    assert entries, "overview returned no codes"
    return entries[0]["code"]


def test_edit_survives_full_restart(fake_env):
    import app.main as main

    # --- boot #1: edit a definition -------------------------------------
    with TestClient(main.app) as c:
        _login(c)
        code = _some_code(c)
        r = c.post("/api/definition", json={"code": code, "definition": "EDITED IN TEST v2"})
        assert r.status_code == 200
        assert r.json()["version"] >= 2
        # The edit was flushed to SharePoint.
        assert state_sync._db_remote() in fake_env.state
        # And a readable CSV mirror exists.
        assert state_sync._csv_remote("definitions.csv") in fake_env.state

    # --- simulate a Render restart: wipe ALL in-memory state ------------
    db._reset_anchor_for_tests()
    state_sync._reset_for_tests()

    # --- boot #2: the edit must be hydrated back from SharePoint ---------
    with TestClient(main.app) as c:
        _login(c)
        d = c.get("/api/definition", params={"code": code}).json()
        assert d["current"]["definition"] == "EDITED IN TEST v2"
        assert d["current"]["version"] >= 2


def test_app_boots_and_serves_when_sharepoint_unavailable(monkeypatch, mem_db):
    # Memory mode but SharePoint not configured -> graceful: inputs from disk,
    # no hydrate/push, app still serves.
    monkeypatch.setattr(sp, "configured", lambda: False)
    monkeypatch.setattr(config, "AUTH_USERNAME", "u", raising=False)
    monkeypatch.setattr(config, "AUTH_PASSWORD", "p", raising=False)
    state_sync._reset_for_tests()
    import app.main as main
    with TestClient(main.app) as c:
        _login(c)
        assert c.get("/api/overview").status_code == 200


def test_definitions_csv_mirror_is_human_readable(fake_env):
    import csv
    import io
    import app.main as main
    with TestClient(main.app) as c:
        _login(c)
        code = _some_code(c)
        c.post("/api/definition", json={"code": code, "definition": "READABLE CHECK"})
    blob = fake_env.state[state_sync._csv_remote("definitions.csv")][0]
    rows = list(csv.reader(io.StringIO(blob.decode("utf-8"))))
    assert rows[0][:3] == ["code_name", "version", "definition"]
    assert any(r[0] == code and r[2] == "READABLE CHECK" for r in rows[1:])
