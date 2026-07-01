"""Regression: the classic file-mode (on-disk DB) path still works.

The RAM-only refactor must not break local dev / persistent-disk deployments:
DataStore reads disk inputs, the DB is a real file, and no SharePoint push runs.
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


def test_file_mode_boots_serves_and_does_not_push(monkeypatch, tmp_path):
    # Force file mode with an isolated temp DB (don't touch the repo's DB).
    monkeypatch.setattr(config, "MEMORY_DB", False, raising=False)
    monkeypatch.setattr(config, "DB_PATH", str(tmp_path / "coding.sqlite"), raising=False)
    monkeypatch.setattr(sp, "configured", lambda: False)  # local inputs from disk
    monkeypatch.setattr(config, "AUTH_USERNAME", "u", raising=False)
    monkeypatch.setattr(config, "AUTH_PASSWORD", "p", raising=False)
    db._reset_anchor_for_tests()

    pushed = {"called": False}
    monkeypatch.setattr(state_sync, "push_state",
                        lambda: pushed.__setitem__("called", True) or False)

    import app.main as main
    with TestClient(main.app) as c:
        r = c.post("/login", data={"username": "u", "password": "p"}, follow_redirects=False)
        assert r.status_code == 302
        assert c.get("/api/overview").status_code == 200
        code = (c.get("/api/overview").json()["success"] +
                c.get("/api/overview").json()["fail"])[0]["code"]
        # An edit works and is stored in the on-disk DB.
        assert c.post("/api/definition",
                      json={"code": code, "definition": "file mode edit"}).status_code == 200
        d = c.get("/api/definition", params={"code": code}).json()
        assert d["current"]["definition"] == "file mode edit"

    # File mode must NOT push to SharePoint (durability is the disk).
    assert pushed["called"] is False
    assert (tmp_path / "coding.sqlite").exists()
