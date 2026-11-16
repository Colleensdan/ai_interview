"""A re-analysis job's final status must reach SharePoint in the same upload as
its results.

The job used to push the database first and mark itself done afterwards, so
SharePoint's copy kept every finished job as "running" until some later
re-analysis pushed again (job 05f72b4008d4, 2026-11-09). A restart in between
would load the job back as running.
"""

from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from app import jobs, state_sync
from pipeline.coding import CodeResult
from pipeline.interviews import Interview

from test_state_sync import _seed_db, fake_sp  # noqa: F401  (fixture)


class _Adapter:
    name = "fake-model"


class _Store:
    def current(self, code):
        return {"definition": f"definition of {code}"}

    def previous_kappa(self, code):
        return None

    def record_kappa(self, code, kappa, model):
        pass


class _Inputs:
    def interviews(self):
        return [Interview(title="R_a.txt", key="R_a", text="user: hello"),
                Interview(title="R_b.txt", key="R_b", text="user: goodbye")]

    def ground_truth_counts(self):
        return {"A: trust": {"R_a": 1, "R_b": 0}}, {"R_a", "R_b"}


def _pushed_job(fake_store, job_id: str) -> tuple:
    blob = fake_store.files[state_sync._db_remote()][0]
    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "pushed.sqlite"
        path.write_bytes(blob)
        conn = sqlite3.connect(path)
        try:
            return conn.execute(
                "SELECT status, message, results_json IS NOT NULL, error FROM jobs WHERE id=?",
                (job_id,)).fetchone()
        finally:
            conn.close()


@pytest.fixture
def stubbed(monkeypatch, fake_sp):  # noqa: F811
    monkeypatch.setattr(jobs, "available_adapters", lambda: [_Adapter()])
    monkeypatch.setattr(jobs, "select_sample", lambda ivs: ivs)
    monkeypatch.setattr(jobs, "code_and_check", lambda *a, **k: CodeResult([]))
    _seed_db()          # a realistic results DB: the push guard refuses an empty one
    jobs._ensure_tables()
    return fake_sp


def test_finished_job_is_pushed_as_done(stubbed):
    jobs._create_job("j-done", "reanalyze", total=1, scope="one")
    jobs._run("j-done", _Store(), _Inputs(), ["A: trust"], "one")

    assert jobs.status("j-done")["status"] == "done"
    assert _pushed_job(stubbed, "j-done") == ("done", "Complete", 1, None)


def test_failed_job_is_pushed_as_error(stubbed, monkeypatch):
    monkeypatch.setattr(jobs, "available_adapters", lambda: [])
    jobs._create_job("j-err", "reanalyze", total=1, scope="one")
    jobs._run("j-err", _Store(), _Inputs(), ["A: trust"], "one")

    status, _, _, error = _pushed_job(stubbed, "j-err")
    assert status == "error"
    assert "No model adapters" in error
