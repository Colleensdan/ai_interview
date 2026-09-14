"""Re-scoring a run must replace its results, not stack another copy on top.

The 2026-09-06 run was resumed to append two more models. The resume voted over
only those two, wrote the result alongside the original seven-model vote, and the
run summary averaged the two into 0.6917 -- a figure lower than several of the
individual models and not obviously wrong on sight. By then the majority-vote
matrix held six copies of itself.
"""

from __future__ import annotations

import config
from pipeline import storage
from pipeline.agreement import KappaResult


def _kappa_rows(model, value):
    return [(model, KappaResult("anger/irritation", value, 104, 5, 5))]


def test_insert_kappa_replaces_the_runs_previous_rows(mem_db):
    conn = storage.init_db(config.DB_PATH)
    storage.insert_kappa(conn, "run_a", _kappa_rows("majority_vote", 0.74))
    storage.insert_kappa(conn, "run_a", _kappa_rows("majority_vote", 0.64))
    rows = conn.execute("SELECT kappa FROM kappa WHERE run_id='run_a'").fetchall()
    assert len(rows) == 1, f"re-scoring stacked {len(rows)} rows instead of replacing"
    assert rows[0][0] == 0.64, "the newest score should win"
    conn.close()


def test_insert_kappa_leaves_other_runs_alone(mem_db):
    conn = storage.init_db(config.DB_PATH)
    storage.insert_kappa(conn, "run_a", _kappa_rows("m", 0.10))
    storage.insert_kappa(conn, "run_b", _kappa_rows("m", 0.20))
    storage.insert_kappa(conn, "run_a", _kappa_rows("m", 0.30))
    kept = conn.execute("SELECT kappa FROM kappa WHERE run_id='run_b'").fetchone()
    assert kept[0] == 0.20, "replacing one run must not touch another"
    conn.close()


def test_insert_majority_matrix_replaces_the_runs_previous_vote(mem_db):
    conn = storage.init_db(config.DB_PATH)
    codes, docs = ["c1"], ["d1", "d2"]
    storage.insert_majority_matrix(conn, "run_a", {"c1": {"d1": 1, "d2": 0}}, codes, docs)
    storage.insert_majority_matrix(conn, "run_a", {"c1": {"d1": 0, "d2": 1}}, codes, docs)
    rows = conn.execute(
        "SELECT document, count FROM majority_vote_matrix WHERE run_id='run_a' "
        "ORDER BY document").fetchall()
    assert len(rows) == len(docs), f"vote stacked {len(rows)} rows for {len(docs)} cells"
    assert [r[1] for r in rows] == [0, 1], "the newest vote should win"
    conn.close()


def test_load_count_matrices_returns_every_model_in_the_run(mem_db):
    """What lets a resume vote over models coded by an earlier invocation."""
    conn = storage.init_db(config.DB_PATH)
    codes, docs = ["c1"], ["d1"]
    storage.insert_count_matrix(conn, "run_a", "model_one", {"c1": {"d1": 1}}, codes, docs)
    storage.insert_count_matrix(conn, "run_a", "model_two", {"c1": {"d1": 0}}, codes, docs)
    storage.insert_count_matrix(conn, "run_b", "model_three", {"c1": {"d1": 1}}, codes, docs)

    got = storage.load_count_matrices(conn, "run_a")
    assert sorted(got) == ["model_one", "model_two"], "a resume would vote over the wrong set"
    assert got["model_one"]["c1"]["d1"] == 1
    assert "model_three" not in got, "another run's models must not leak in"
    conn.close()
