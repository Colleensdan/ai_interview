"""Shared test fixtures.

Puts the ``data_analysis`` package root on sys.path (so ``import config``,
``import db``, ``from app import ...`` resolve exactly as they do under
``python -m uvicorn`` in production), and provides an isolated in-memory DB per
test.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import os  # noqa: E402

import config  # noqa: E402  (loads data_analysis/.env)
import db  # noqa: E402

# No test may reach the real SharePoint, where the live study data lives.
# config has just loaded .env, so remove the SharePoint credentials from this
# process for the whole session: anything that gets past a test's fake --
# including a background thread that outlives its test -- fails to
# authenticate instead of writing to the live folder.
for _k in ("TENANT_ID", "CLIENT_ID", "CLIENT_SECRET",
           "SP_HOSTNAME", "SP_SITE_PATH", "SP_LIBRARY_NAME"):
    os.environ.pop(_k, None)


@pytest.fixture(autouse=True)
def _no_snapshot_timer(monkeypatch):
    """The app's daily-snapshot thread is never started under test."""
    from app import state_sync
    monkeypatch.setattr(state_sync, "start_snapshot_timer", lambda: None)


@pytest.fixture
def mem_db(monkeypatch):
    """Fresh, isolated shared-cache in-memory DB for one test.

    Uses a unique URI and resets the process anchor before and after so tests
    never share state.
    """
    db._reset_anchor_for_tests()
    uri = f"file:test_{uuid.uuid4().hex}?mode=memory&cache=shared"
    monkeypatch.setattr(config, "MEMORY_DB", True, raising=False)
    monkeypatch.setattr(config, "DB_PATH", uri, raising=False)
    yield uri
    db._reset_anchor_for_tests()
