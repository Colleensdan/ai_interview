"""An interrupted model must keep its completed codes.

Nothing was persisted until a model finished all 49 codes, so a run stopped by a
quota ceiling at code 30 lost all thirty and a resume paid for them again. With a
weekly token allowance that is the difference between finishing a model and never
finishing one.
"""

from __future__ import annotations

import config
from models.base import CodeHit
from pipeline import storage

RUN, MODEL = "run_x", "test-model"
DOCS = ["a.txt", "b.txt"]


def _store(conn, code, counts, hits=()):
    storage.insert_count_matrix(conn, RUN, MODEL, {code: counts}, [code], DOCS)
    if hits:
        storage.insert_coding_results(conn, RUN, MODEL, list(hits))


def test_completed_codes_are_visible_immediately(mem_db):
    """What a resume reads to decide where to restart."""
    conn = storage.init_db(config.DB_PATH)
    assert storage.codes_already_coded(conn, RUN, MODEL) == set()
    _store(conn, "code_one", {"a.txt": 1, "b.txt": 0},
           [CodeHit("a.txt", "code_one", "q", "r")])
    assert storage.codes_already_coded(conn, RUN, MODEL) == {"code_one"}
    conn.close()


def test_a_code_that_found_nothing_still_counts_as_done(mem_db):
    """Otherwise every empty code is re-paid for on each resume."""
    conn = storage.init_db(config.DB_PATH)
    _store(conn, "empty_code", {"a.txt": 0, "b.txt": 0})
    assert "empty_code" in storage.codes_already_coded(conn, RUN, MODEL)
    conn.close()


def test_recoding_one_code_replaces_it_and_leaves_the_others(mem_db):
    conn = storage.init_db(config.DB_PATH)
    _store(conn, "code_one", {"a.txt": 1, "b.txt": 0},
           [CodeHit("a.txt", "code_one", "first", "r")])
    _store(conn, "code_two", {"a.txt": 0, "b.txt": 1},
           [CodeHit("b.txt", "code_two", "other", "r")])
    _store(conn, "code_one", {"a.txt": 0, "b.txt": 1},
           [CodeHit("b.txt", "code_one", "second", "r")])

    mat = storage.load_count_matrices(conn, RUN)[MODEL]
    assert mat["code_one"] == {"a.txt": 0, "b.txt": 1}, "re-coding should replace"
    assert mat["code_two"] == {"a.txt": 0, "b.txt": 1}, "and not disturb its neighbour"

    quotes = sorted(h.quote for h in storage.load_coding_results(conn, RUN, MODEL))
    assert quotes == ["other", "second"], f"stale occurrences survived: {quotes}"
    conn.close()


def test_other_models_and_runs_are_untouched(mem_db):
    conn = storage.init_db(config.DB_PATH)
    _store(conn, "code_one", {"a.txt": 1, "b.txt": 1})
    storage.insert_count_matrix(conn, RUN, "other-model", {"code_one": {"a.txt": 9, "b.txt": 9}},
                                ["code_one"], DOCS)
    storage.insert_count_matrix(conn, "run_y", MODEL, {"code_one": {"a.txt": 7, "b.txt": 7}},
                                ["code_one"], DOCS)
    _store(conn, "code_one", {"a.txt": 0, "b.txt": 0})

    assert storage.load_count_matrices(conn, RUN)["other-model"]["code_one"]["a.txt"] == 9
    assert storage.load_count_matrices(conn, "run_y")[MODEL]["code_one"]["a.txt"] == 7
    conn.close()


# --- end to end, through the real pipeline ---------------------------------

class _StubAdapter:
    """Codes normally, then dies mid-run once `die_after` codes are complete.

    Raises KeyboardInterrupt rather than a normal exception so it propagates out
    of the worker exactly as a kill or a Ctrl-C does, leaving no chance to
    persist afterwards. That is the failure this whole mechanism exists for: the
    real run was killed by a quota ceiling with five codes finished and lost.
    """

    def __init__(self, die_after=None):
        self.name = "stub-model"
        self.die_after = die_after
        self.codes_seen: list[str] = []

    def code_one(self, request):
        if request.code_name not in self.codes_seen:
            self.codes_seen.append(request.code_name)
        # counted per code, not per batch: one code spans five batch calls
        if self.die_after is not None and len(self.codes_seen) > self.die_after:
            raise KeyboardInterrupt("killed mid-run")
        return [CodeHit(document_title=t, code_name=request.code_name,
                        quote="q", reason="r")
                for t in request.document_titles[:1]]


def test_an_interrupted_model_keeps_its_finished_codes(mem_db, monkeypatch, tmp_path):
    """Dying part-way must not discard the codes already paid for."""
    import pytest
    import run_pipeline

    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)
    dying = _StubAdapter(die_after=2)
    monkeypatch.setattr(run_pipeline, "available_adapters", lambda: [dying])

    with pytest.raises(KeyboardInterrupt):
        run_pipeline.run(label="resume_test", max_codes=4)

    conn = storage.init_db(config.DB_PATH)
    run_id = conn.execute(
        "SELECT run_id FROM runs ORDER BY run_id DESC LIMIT 1").fetchone()[0]
    stored = storage.codes_already_coded(conn, run_id, dying.name)
    conn.close()
    assert len(stored) == 2, (
        f"the two completed codes should have survived the kill, got {sorted(stored)}")

    # Resuming must code only what is missing, not pay for the stored ones again.
    healthy = _StubAdapter()
    monkeypatch.setattr(run_pipeline, "available_adapters", lambda: [healthy])
    run_pipeline.run(label="resume_test", max_codes=4, resume_run_id=run_id)

    repaid = set(healthy.codes_seen) & stored
    assert not repaid, f"resume re-paid for codes already stored: {sorted(repaid)}"

    conn = storage.init_db(config.DB_PATH)
    final = storage.codes_already_coded(conn, run_id, dying.name)
    conn.close()
    assert len(final) == 4, f"resume should complete the model, got {len(final)} codes"
