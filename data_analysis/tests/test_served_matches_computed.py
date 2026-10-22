"""The kappa the app serves must be the kappa the pipeline computed.

This is the regression guard for the original bug report: the deployed app
showed a kappa that disagreed with the local `kappa.csv`. The cause was two
stores — Render kept its own SQLite seeded once and never rebuilt — and the fix
was to make SharePoint the single source of truth. That made "served equals
computed" an invariant rather than a coincidence, but nothing asserted it.

No model is called: kappa rows are written exactly as `run_pipeline` writes them
and then read back through the app's own accessor.
"""

from __future__ import annotations

import csv

import config
from app.data_access import DataStore
from app.inputs import InputStore
from pipeline import storage
from models.base import CodeHit
from pipeline.agreement import KappaResult

MODEL = "azure_openai-test"


def _rows():
    # 1/3 and 0.43955... are deliberately non-terminating: kappa.csv keeps 4
    # decimal places while the DB keeps full precision, so an exact-equality
    # check would pass on tidy fixtures and fail on real values.
    return [
        (MODEL, KappaResult("anger/irritation", 0.91, 10, 5, 5)),
        (MODEL, KappaResult("frustration", 1 / 3, 10, 6, 3)),
        (MODEL, KappaResult("disapproval", float("nan"), 10, 0, 0)),
    ]


def test_served_kappa_matches_the_written_csv(mem_db, tmp_path, monkeypatch):
    conn = storage.init_db(config.DB_PATH)
    rows = _rows()
    storage.insert_kappa(conn, "run_test", rows)
    csv_path = tmp_path / "kappa.csv"
    storage.write_kappa_csv(csv_path, rows)
    conn.close()

    store = DataStore(InputStore(from_disk=True))
    served = store.latest_kappa(MODEL)

    with open(csv_path, encoding="utf-8") as f:
        written = {r["Code"]: r["Cohen's kappa"] for r in csv.DictReader(f)
                   if r["Model"] == MODEL}

    assert written, "no kappa rows were written"
    for code, value in written.items():
        if value == "":          # NaN is written as an empty cell
            assert served.get(code) is None or served[code] != served[code]
        else:
            # The CSV keeps 4 decimal places, the DB full precision; agreement
            # is asserted at the precision the file actually claims.
            assert round(served[code], 4) == round(float(value), 4), \
                f"{code} disagrees: served={served[code]} csv={value}"


def test_ground_truth_snapshot_is_replaced_not_appended(mem_db):
    """Re-running must not duplicate the human rows.

    The ground-truth table carries no run_id — it is a snapshot of the input
    workbook — so an append would double all 5,824 rows on the second run and
    inflate the CSV mirror pushed to SharePoint.
    """
    conn = storage.init_db(config.DB_PATH)
    matrix = {"worry": {"d1": 1, "d2": 0}, "trust": {"d1": 0, "d2": 2}}
    for _ in range(3):
        storage.insert_ground_truth_matrix(conn, matrix, ["worry", "trust"], ["d1", "d2"])
    n = conn.execute("SELECT COUNT(*) FROM ground_truth_matrix").fetchone()[0]
    conn.close()
    assert n == 4


def test_excluded_codes_are_not_counted_as_failures(mem_db, monkeypatch):
    """The pass rate must be a fraction of what was actually scored.

    Codes on the exclusion list are never sent to the model, so leaving them in
    the denominator would report them as failures and understate the result.
    """
    storage.init_db(config.DB_PATH).close()
    store = DataStore(InputStore(from_disk=True))
    all_codes = [c.name for c in store.codebook()]
    assert len(all_codes) > 2
    # Procedural codes are listed apart from the pass/fail total; this test is
    # about exclusion only.
    monkeypatch.setattr(config, "PROCEDURAL_CODES", frozenset())

    monkeypatch.setattr(config, "EXCLUDED_CODES", [])
    assert DataStore(InputStore(from_disk=True)).overview(MODEL)["total"] == len(all_codes)

    monkeypatch.setattr(config, "EXCLUDED_CODES", all_codes[:2])
    ov = DataStore(InputStore(from_disk=True)).overview(MODEL)
    assert ov["total"] == len(all_codes) - 2
    assert ov["excluded"] == sorted(all_codes[:2])
    listed = {e["code"] for e in ov["success"] + ov["fail"]}
    assert not listed & set(all_codes[:2])


def test_kappa_target_drives_the_csv_header_and_the_overview(mem_db, tmp_path, monkeypatch):
    """The pass/fail threshold must come from one place, not two literals."""
    monkeypatch.setattr(config, "KAPPA_TARGET", 0.5)
    storage.write_kappa_csv(tmp_path / "k.csv", _rows())
    header = (tmp_path / "k.csv").read_text(encoding="utf-8").splitlines()[0]
    assert "meets_target_>0.5" in header

    conn = storage.init_db(config.DB_PATH)
    storage.insert_kappa(conn, "run_test", _rows())
    conn.close()
    overview = DataStore(InputStore(from_disk=True)).overview(MODEL)
    assert overview["target"] == 0.5
    # frustration = 0.33 fails at 0.5 but would pass a lower bar; anger = 0.91 passes.
    assert "frustration" in [e["code"] for e in overview["fail"]]
    assert "anger/irritation" in [e["code"] for e in overview["success"]]


def test_withdrawn_codes_are_not_served(mem_db, monkeypatch):
    """A code excluded from the codebook must not come back through kappa.

    `worry` was withdrawn in the 2026-09 ground-truth export, but the kappa row
    from the August run that scored it stays in the table, and the "most recent
    per code" lookup returns whatever is newest for that code -- which, for a
    code nothing has scored since, is that stale row.
    """
    monkeypatch.setattr(config, "EXCLUDED_CODES", ["worry"])
    conn = storage.init_db(config.DB_PATH)
    storage.insert_kappa(conn, "run_old", [
        (MODEL, KappaResult("worry", 0.2346, 10, 3, 3)),
        (MODEL, KappaResult("frustration", 0.81, 10, 5, 5)),
    ])
    conn.close()

    served = DataStore(InputStore(from_disk=True)).latest_kappa(MODEL)
    assert "frustration" in served, "a live code should still be served"
    assert "worry" not in served, "a withdrawn code leaked back into the app"


def test_quotes_come_from_the_run_the_kappa_came_from(mem_db):
    """A code's quotes and its kappa must describe the same run.

    Reported on the live site for "pro-environmental social norms increased":
    kappa .00 beside two quotes. The newest run found the code nowhere, so it
    wrote a kappa row but no coding_results rows; the quote lookup took the
    newest run *with quotes* and showed the previous run's two hits under the
    newest run's kappa.
    """
    code = "frustration"
    conn = storage.init_db(config.DB_PATH)
    storage.insert_coding_results(conn, "run_a", MODEL, [
        CodeHit("d1.txt", code, "an old quote", "old reason")], code_names=[code])
    storage.insert_kappa(conn, "run_a", [(MODEL, KappaResult(code, 0.66, 10, 1, 2))])
    storage.insert_coding_results(conn, "run_b", MODEL, [], code_names=[code])
    storage.insert_kappa(conn, "run_b", [(MODEL, KappaResult(code, 0.0, 10, 1, 0))])
    conn.close()

    store = DataStore(InputStore(from_disk=True))
    assert store.latest_kappa(MODEL)[code] == 0.0
    assert store.latest_runs(MODEL)[code] == "run_b"
    assert store.ai_hits(MODEL, "d1.txt", [code]) == []
    assert store.ai_hits(MODEL, "d1.txt") == []

    # And the other way round: once a newer run does find it, its quotes show.
    conn = storage.init_db(config.DB_PATH)
    storage.insert_coding_results(conn, "run_c", MODEL, [
        CodeHit("d1.txt", code, "a new quote", "new reason")], code_names=[code])
    storage.insert_kappa(conn, "run_c", [(MODEL, KappaResult(code, 1.0, 10, 1, 1))])
    conn.close()
    hits = store.ai_hits(MODEL, "d1.txt", [code])
    assert [h["quote"] for h in hits] == ["a new quote"]


def test_procedural_codes_are_listed_apart_and_both_means_served(mem_db, monkeypatch):
    """Procedural codes get their own list, stay out of the pass counts and the
    headline mean, and are folded into the second, all-codes mean."""
    monkeypatch.setattr(config, "EXCLUDED_CODES", [])
    monkeypatch.setattr(config, "PROCEDURAL_CODES", frozenset({"finished"}))
    conn = storage.init_db(config.DB_PATH)
    storage.insert_kappa(conn, "run_test", [
        (MODEL, KappaResult("frustration", 0.5, 10, 5, 5)),
        (MODEL, KappaResult("finished", 1.0, 10, 5, 5)),
    ])
    conn.close()
    ov = DataStore(InputStore(from_disk=True)).overview(MODEL)
    assert [e["code"] for e in ov["procedural"]] == ["finished"]
    assert ov["procedural"][0]["passes"] is True
    listed = {e["code"] for e in ov["success"] + ov["fail"]}
    assert "finished" not in listed and "frustration" in listed
    assert ov["mean_kappa"] == 0.5 and ov["n_kappa_scored"] == 1
    assert ov["mean_kappa_all"] == 0.75 and ov["n_kappa_scored_all"] == 2
