"""recode_live.py: apply the quote check to the results the live site shows.

It must change only the interviews the check sends back, publish the outcome
as a new run without touching the source run or any definition text, and
refuse a code whose definition changed after its result was coded.
"""

from __future__ import annotations

import json
import sqlite3

import pytest

import config
import recode_live
from models.base import ModelAdapter, RawReply
from pipeline import storage
from pipeline.agreement import per_code_kappa
from pipeline.interviews import Interview
from pipeline.matrices import build_count_matrix
from pipeline.quote_check import quote_in_text
import sync_to_live

from test_state_sync import fake_sp  # noqa: F401  (fixture)

MODEL = "m"
SRC = "20261109_120000_reanalyze"
CODE = "finished"
SAMPLE = [
    Interview("a.txt", "1", "[INTERVIEWEE] I would buy it again.\n[INTERVIEWER] That was the "
                            "last question. Thank you for your time devoted to this project!"),
    Interview("b.txt", "2", "[INTERVIEWEE] I have to go now, sorry."),
    Interview("c.txt", "3", "[INTERVIEWEE] Prices matter most to me.\n[INTERVIEWER] That was "
                            "the last question. Thank you, and goodbye from the whole team."),
]
TITLES = [iv.title for iv in SAMPLE]
GT = {CODE: {"1": 1, "2": 0, "3": 1}}
CLOSING_A = "That was the last question. Thank you for your time devoted to this project!"


class _Honest(ModelAdapter):
    """Alone, b.txt is (correctly) not finished."""

    name = MODEL

    def __init__(self):
        self.asked = []

    def is_available(self):
        return True

    def complete(self, request):
        self.asked.append(request.document_titles)
        return RawReply(json.dumps({"occurrences": []}), "stop", {})


def _live_blob(def_created="2026-11-09 11:59:00"):
    """A live DB whose MODEL result for CODE files a.txt's closing under b.txt."""
    conn = sqlite3.connect(":memory:")
    conn.executescript(storage._SCHEMA)
    conn.executescript(
        "CREATE TABLE code_definitions (id INTEGER PRIMARY KEY AUTOINCREMENT, code_name TEXT, "
        "version INT, definition TEXT, kappa REAL, model TEXT, is_current INT, created_at TEXT);")
    hits = [("a.txt", CLOSING_A), ("b.txt", CLOSING_A),
            ("c.txt", "Thank you, and goodbye from the whole team.")]
    conn.executemany("INSERT INTO coding_results (run_id, model, document_title, code_name, "
                     "quote, reason) VALUES (?, ?, ?, ?, ?, 'r')",
                     [(SRC, MODEL, d, CODE, q) for d, q in hits])
    counts = {"a.txt": 1, "b.txt": 1, "c.txt": 1}
    conn.executemany("INSERT INTO count_matrix (run_id, model, code_name, document, count) "
                     "VALUES (?, ?, ?, ?, ?)", [(SRC, MODEL, CODE, d, n) for d, n in counts.items()])
    kr = per_code_kappa([CODE], GT, {CODE: counts}, [(iv.title, iv.key) for iv in SAMPLE])[0]
    conn.execute("INSERT INTO kappa (run_id, model, code_name, kappa, n_documents, "
                 "n_human_present, n_llm_present) VALUES (?, ?, ?, ?, ?, ?, ?)",
                 (SRC, MODEL, CODE, kr.kappa, kr.n_documents, kr.n_human_present,
                  kr.n_llm_present))
    conn.execute("INSERT INTO code_definitions (code_name, version, definition, kappa, model, "
                 "is_current, created_at) VALUES (?, 1, 'the interview ended', ?, ?, 1, ?)",
                 (CODE, kr.kappa, MODEL, def_created))
    conn.commit()
    return conn.serialize(), kr.kappa


@pytest.fixture
def live(fake_sp, monkeypatch, tmp_path):  # noqa: F811
    monkeypatch.setattr(recode_live, "load_interviews", lambda _d: SAMPLE)
    monkeypatch.setattr(recode_live, "select_sample", lambda ivs: list(ivs))
    monkeypatch.setattr(recode_live, "load_ground_truth_counts", lambda *_a: (GT, ["1", "2", "3"]))
    monkeypatch.setattr(recode_live, "BACKUP_ROOT", tmp_path)
    monkeypatch.setattr("pipeline.coding.time.sleep", lambda _s: None)
    model = _Honest()
    monkeypatch.setattr(recode_live, "available_adapters", lambda: [model])

    def run(def_created="2026-11-09 11:59:00", *args):
        blob, k = _live_blob(def_created)
        fake_sp.upload(sync_to_live._remote_db(), blob)
        monkeypatch.setattr("sys.argv", ["recode_live.py", "--model", MODEL, *args])
        code = recode_live.main()
        after = sync_to_live.load_remote(fake_sp.download_bytes(sync_to_live._remote_db()))
        return code, after, k, blob
    run.model = model
    return run


def test_only_the_misfiled_interview_changes_in_a_new_run(live):
    code, after, k_before, _ = live()
    assert code == 0
    assert live.model.asked == [("b.txt",)]        # alone, and nothing else
    run_id = after.execute("SELECT run_id FROM runs WHERE label='recode'").fetchone()[0]
    counts = dict(after.execute("SELECT document, count FROM count_matrix WHERE run_id=?",
                                (run_id,)).fetchall())
    assert counts == {"a.txt": 1, "b.txt": 0, "c.txt": 1}
    quotes = after.execute("SELECT document_title, quote FROM coding_results WHERE run_id=? "
                           "ORDER BY document_title", (run_id,)).fetchall()
    assert [d for d, _ in quotes] == ["a.txt", "c.txt"]
    assert all(quote_in_text(q, SAMPLE[TITLES.index(d)].text) for d, q in quotes)
    k_after = after.execute("SELECT kappa FROM kappa WHERE run_id=?", (run_id,)).fetchone()[0]
    assert k_after == 1.0 and k_before < 1.0
    # the source run is untouched; the definition's kappa follows the repair
    assert after.execute("SELECT COUNT(*) FROM coding_results WHERE run_id=?",
                         (SRC,)).fetchone()[0] == 3
    assert tuple(after.execute("SELECT definition, kappa FROM code_definitions").fetchone()) \
        == ("the interview ended", 1.0)
    # the re-coding replies are kept locally, under the new run
    local = storage.init_db(config.DB_PATH)
    assert local.execute("SELECT batch_label, final FROM raw_responses WHERE run_id=?",
                         (run_id,)).fetchall() == [("recode:b.txt:1", 1)]


def test_a_definition_changed_after_the_run_is_refused_and_nothing_is_written(live):
    code, after, _, blob = live(def_created="2026-11-09 12:30:00")
    assert code == 1
    assert live.model.asked == []
    assert after.serialize() == sync_to_live.load_remote(blob).serialize()


def test_a_dry_run_calls_no_model_and_writes_nothing(live):
    code, after, _, blob = live("2026-11-09 11:59:00", "--dry-run")
    assert code == 0 and live.model.asked == []
    assert after.serialize() == sync_to_live.load_remote(blob).serialize()
