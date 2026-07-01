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

import config  # noqa: E402
import db  # noqa: E402


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
