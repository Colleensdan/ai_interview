"""A quote filed under the wrong interview sends that interview back, alone.

Pins the 2026-11 fix. Models coding 25 interviews per request sometimes reused
a sentence from one interview as the evidence for another (86 of 25,400 stored
occurrences; e.g. gpt-5-mini coding ``finished`` in an interview that ended
after three turns, quoting a neighbour's closing message). Now every quote is
checked against the interview it names; a failing interview -- and the one the
quote came from, if its "absent" is suspect too -- is coded again on its own, up
to ``config.RECODE_ATTEMPTS`` times; an interview that still fails has no
verdict and is left out of that code's kappa, never scored absent.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import config
from models.base import CodeHit, ModelAdapter, RawReply
from pipeline import coding, provenance, storage
from pipeline.agreement import per_code_kappa
from pipeline.coding import (CodeResult, apply_recodes, code_and_check,
                             interviews_to_recode, recode_label)
from pipeline.interviews import Interview
from pipeline.matrices import build_count_matrix, majority_vote
from pipeline.quote_check import quote_in_text

from test_job_status_push import _Inputs, _Store, stubbed  # noqa: F401  (fixture)
from test_reproducibility import (_Model, _latest_run, _line_of,  # noqa: F401
                                  pipeline_env)
from test_state_sync import fake_sp  # noqa: F401  (fixture)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr("pipeline.coding.time.sleep", lambda _s: None)


# --- the check -------------------------------------------------------------------

TEXT = ("[INTERVIEWER] How do you feel about it?\n"
        "[INTERVIEWEE] I'm honestly worried about the climate, and I recycle a lot.\n"
        "[INTERVIEWER] On a scale of 1 to 5?\n"
        "[INTERVIEWEE] I'd say 4. Fourteen people asked me the same thing.\n")


@pytest.mark.parametrize("quote", [
    "I'm honestly worried about the climate, and I recycle a lot.",
    "i'm HONESTLY worried about the climate -- and I recycle a lot",       # case, punctuation
    "I\x039m honestly worried about the climate, and I recycle a lot.",    # gpt-5-mini apostrophe
    "I\x039m honestly worried",                                           # ... in a short quote
    "I\x0e2\n0\x0e2\n0\x1am honestly worried",                              # ... a longer garble
    "I'm honestly worried about the climate... I'd say 4.",                # stitched, elided
    "4",                                                                   # short, whole word
])
def test_ways_models_legitimately_quote_are_accepted(quote):
    assert quote_in_text(quote, TEXT)


@pytest.mark.parametrize("quote", [
    "Thank you very much for participating in this interview.",           # not in it
    "I'm honestly worried about the economy, and I drive a lot.",          # half of it
    "1 4",                                                                 # short, not as written
])
def test_quotes_not_in_the_interview_are_rejected(quote):
    assert not quote_in_text(quote, TEXT)


def test_a_short_quote_must_match_whole_words():
    """'4' is in '14' as characters; a short quote must not match loosely."""
    assert not quote_in_text("4", "There were 14 of them.")
    assert quote_in_text("don't know", "Hmm, I don't know really.")
    assert not quote_in_text("don't know", "I do know.")


def test_a_punctuation_only_quote_is_compared_as_written():
    assert quote_in_text("????", "[INTERVIEWEE] ????")
    assert not quote_in_text("????", "[INTERVIEWEE] !!!!")


def test_the_reported_case_is_rejected_where_it_was_filed_and_found_at_its_source():
    """The case that started this: gpt-5-mini's ``finished`` quote for
    R_8SDAggEgb0XnRIg (three turns, no closing) is R_8So7GGRp1xyr8tt's."""
    d = Path(config.INTERVIEWS_DIR)
    filed, source = d / "R_8SDAggEgb0XnRIg.txt", d / "R_8So7GGRp1xyr8tt.txt"
    if not (filed.exists() and source.exists()):
        pytest.skip("transcripts not on disk")
    quote = ("Thank you very much for participating in this interview. That was the "
             "last question. Please continue with the remaining sections of the "
             "questionnaire. Thank you for your answers and for your time devoted "
             "to this research project!")
    assert not quote_in_text(quote, filed.read_text(encoding="utf-8"))
    assert quote_in_text(quote, source.read_text(encoding="utf-8"))


# --- which interviews are re-coded -------------------------------------------------

A = Interview("a.txt", "1", "[INTERVIEWEE] The prices of organic food are far too high for me.")
B = Interview("b.txt", "2", "[INTERVIEWEE] I mostly buy whatever is on offer that week.")
C = Interview("c.txt", "3", "[INTERVIEWEE] I mostly buy whatever is on offer that week.")
D = Interview("d.txt", "4", "[INTERVIEWEE] Local markets are my favourite place to shop.")
BATCHES = [[A, B], [C, D]]


def _hit(doc, quote, code="c"):
    return CodeHit(doc.title, code, quote, "r")


def test_valid_quotes_send_nothing_back():
    assert interviews_to_recode([_hit(A, "organic food are far too high")], BATCHES) == []


def test_a_borrowed_quote_sends_back_the_interview_it_was_filed_under_and_its_source():
    """B's "absent" is as suspect as A's "present": the reply put B's evidence
    on A and said nothing about B."""
    assert interviews_to_recode([_hit(A, "whatever is on offer that week")],
                                BATCHES) == ["a.txt", "b.txt"]


def test_a_source_that_already_has_a_valid_occurrence_is_not_re_coded():
    hits = [_hit(A, "whatever is on offer that week"),
            _hit(B, "I mostly buy whatever is on offer")]
    assert interviews_to_recode(hits, BATCHES) == ["a.txt"]


def test_a_source_in_another_batch_is_not_a_source():
    """The model never saw C alongside A, so C cannot be where A's quote came from."""
    assert interviews_to_recode([_hit(A, "Local markets are my favourite place")],
                                BATCHES) == ["a.txt"]


def test_a_quote_found_in_several_other_interviews_names_no_source():
    batches = [[A, B, C]]
    assert interviews_to_recode([_hit(A, "whatever is on offer that week")],
                                batches) == ["a.txt"]


# --- the re-coding loop ------------------------------------------------------------

def _fetcher(*answers):
    """fetch() that serves *answers* in order and records what it was asked."""
    asked, queue = [], list(answers)

    def fetch(iv, attempt):
        asked.append((iv.title, attempt))
        return queue.pop(0)
    return asked, fetch


def test_a_re_coding_that_passes_replaces_everything_the_batch_said_about_it():
    batch = [_hit(A, "whatever is on offer that week"),     # borrowed
             _hit(A, "organic food are far too high"),      # genuine, same reply
             _hit(B, "I mostly buy whatever is on offer")]  # B coded validly
    asked, fetch = _fetcher([_hit(A, "far too high for me")])
    res = apply_recodes(batch, BATCHES, fetch, note=lambda _m: None)
    assert asked == [("a.txt", 1)]
    assert res.recoded == ["a.txt"] and res.unresolved == []
    assert sorted((h.document_title, h.quote) for h in res.hits) == [
        ("a.txt", "far too high for me"), ("b.txt", "I mostly buy whatever is on offer")]


def test_an_empty_answer_alone_is_a_verdict_absent():
    asked, fetch = _fetcher([])
    res = apply_recodes([_hit(A, "Local markets are my favourite place")], BATCHES,
                        fetch, note=lambda _m: None)
    assert res.hits == [] and res.unresolved == [] and res.recoded == ["a.txt"]


def test_a_failing_re_coding_is_asked_again_and_the_passing_attempt_is_kept(monkeypatch):
    monkeypatch.setattr(config, "RECODE_ATTEMPTS", 3)
    asked, fetch = _fetcher([_hit(A, "invented words never said")],
                            [_hit(A, "organic food are far too high")])
    res = apply_recodes([_hit(A, "Local markets are my favourite place")], BATCHES,
                        fetch, note=lambda _m: None)
    assert asked == [("a.txt", 1), ("a.txt", 2)]
    assert [h.quote for h in res.hits] == ["organic food are far too high"]
    assert res.unresolved == []


def test_an_interview_that_never_passes_has_no_verdict(monkeypatch):
    monkeypatch.setattr(config, "RECODE_ATTEMPTS", 3)
    bad = [_hit(A, "invented words never said")]
    asked, fetch = _fetcher(bad, bad, bad, bad)
    res = apply_recodes([_hit(A, "Local markets are my favourite place"),
                         _hit(B, "I mostly buy whatever")], BATCHES,
                        fetch, note=lambda _m: None)
    assert asked == [("a.txt", 1), ("a.txt", 2), ("a.txt", 3)]
    assert res.unresolved == ["a.txt"]
    assert [h.document_title for h in res.hits] == ["b.txt"]   # nothing kept for A


class _Scripted(ModelAdapter):
    """Batch reply borrows B's sentence for A; alone, A is coded honestly."""

    name = "scripted"

    def __init__(self):
        self.requests = []

    def is_available(self):
        return True

    def complete(self, request):
        self.requests.append(request)
        occ = []
        if request.document_titles == ("a.txt", "b.txt"):
            occ = [{"document_title": "a.txt",
                    "quote": "whatever is on offer that week", "reason": "r"}]
        elif request.document_titles == ("a.txt",):
            occ = [{"document_title": "a.txt", "quote": "far too high", "reason": "r"}]
        return RawReply(json.dumps({"occurrences": occ}), "stop", {})


def test_code_and_check_asks_the_model_with_one_interview_and_records_it():
    model, log = _Scripted(), []
    res = code_and_check(model, "c", "def", BATCHES, note=lambda _m: None,
                         recorder=lambda req, label, *rest: log.append(
                             (label, req.document_titles)))
    assert res.recoded == ["a.txt", "b.txt"] and res.unresolved == []
    assert [(h.document_title, h.quote) for h in res.hits] == [("a.txt", "far too high")]
    alone = [r for r in model.requests if len(r.document_titles) == 1]
    assert [r.document_titles for r in alone] == [("a.txt",), ("b.txt",)]
    assert "b.txt" not in alone[0].merged_document
    assert (recode_label("a.txt", 1), ("a.txt",)) in log
    assert (recode_label("b.txt", 1), ("b.txt",)) in log


def test_the_check_and_its_budget_are_part_of_a_runs_provenance(monkeypatch):
    """A resume under a different rule would mix two experiments in one run."""
    def prov():
        return provenance.build(codes=[], sample=[], adapters=[], definitions="current",
                                definitions_src=None, definitions_max_version=None)
    before = prov()
    monkeypatch.setattr("pipeline.quote_check.QUOTE_CHECK_VERSION", 99)
    monkeypatch.setattr(config, "RECODE_ATTEMPTS", 7)
    diff = provenance.mismatches(before, prov())
    assert "quote_check_version: 1 -> 99" in diff
    assert "recode_attempts: 3 -> 7" in diff


# --- no verdict: neither present nor absent ------------------------------------------

def test_an_unresolved_interview_has_no_cell():
    m = build_count_matrix([_hit(B, "q")], ["c"], ["a.txt", "b.txt"], {"c": ["a.txt"]})
    assert m == {"c": {"b.txt": 1}}


def test_a_model_without_a_verdict_abstains_from_the_vote():
    rows = [{"c": {"d": 1}}, {"c": {"d": 1}}, {"c": {}}]
    assert majority_vote(rows, ["c"], ["d"]) == {"c": {"d": 1}}     # 2 of 2, not 2 of 3
    rows = [{"c": {"d": 1}}, {"c": {"d": 0}}, {"c": {}}]
    assert majority_vote(rows, ["c"], ["d"]) == {"c": {"d": 0}}     # 1 of 2: a tie
    assert majority_vote([{"c": {}}, {"c": {}}], ["c"], ["d"]) == {"c": {}}


def test_an_interview_without_a_verdict_is_left_out_of_kappa():
    pairs = [("a.txt", "1"), ("b.txt", "2"), ("c.txt", "3")]
    human = {"c": {"1": 1, "2": 0, "3": 1}}
    kr = per_code_kappa(["c"], human, {"c": {"b.txt": 0, "c.txt": 1}}, pairs, {"c": "c"})[0]
    assert (kr.n_documents, kr.n_human_present, kr.n_llm_present, kr.kappa) == (2, 1, 1, 1.0)
    # The trap: the same cell read as "absent" scores a disagreement.
    as_absent = per_code_kappa(["c"], human, {"c": {"a.txt": 0, "b.txt": 0, "c.txt": 1}},
                               pairs, {"c": "c"})[0]
    assert as_absent.n_documents == 3 and as_absent.kappa < 1.0


def test_no_verdict_is_stored_as_no_row_and_written_blank(mem_db, tmp_path):
    conn = storage.init_db(config.DB_PATH)
    m = build_count_matrix([], ["c"], ["a.txt", "b.txt"], {"c": ["a.txt"]})
    storage.persist_code(conn, "r", "m", "c", m, ["a.txt", "b.txt"], [], None)
    assert storage.load_count_matrices(conn, "r") == {"m": {"c": {"b.txt": 0}}}
    storage.write_matrix_csv(tmp_path / "m.csv", m, ["c"], ["a.txt", "b.txt"])
    assert (tmp_path / "m.csv").read_text().splitlines()[1] == "c,,0"
    # The human matrix keeps its meaning: a missing cell there is "not coded".
    storage.insert_ground_truth_matrix(conn, {"c": {"1": 1}}, ["c"], ["1", "2"])
    assert conn.execute("SELECT document, count FROM ground_truth_matrix ORDER BY document"
                        ).fetchall() == [("1", 1), ("2", 0)]


# --- the website's re-analysis --------------------------------------------------------

def test_a_re_analysis_leaves_an_unresolved_interview_out_of_kappa(stubbed, monkeypatch):  # noqa: F811
    from app import jobs
    monkeypatch.setattr(jobs, "code_and_check", lambda *a, **k: CodeResult(
        [CodeHit("R_a.txt", "A: trust", "q", "r")], recoded=["R_b.txt"],
        unresolved=["R_b.txt"]))
    jobs._create_job("j", "reanalyze", total=1, scope="one")
    jobs._run("j", _Store(), _Inputs(), ["A: trust"], "one")
    conn = storage.init_db(config.DB_PATH)
    run_id = conn.execute("SELECT run_id FROM kappa ORDER BY id DESC LIMIT 1").fetchone()[0]
    assert conn.execute("SELECT document, count FROM count_matrix WHERE run_id=?",
                        (run_id,)).fetchall() == [("R_a.txt", 1)]
    assert conn.execute("SELECT n_documents FROM kappa WHERE run_id=?",
                        (run_id,)).fetchone()[0] == 1


# --- end to end: run, store, verify ---------------------------------------------------

def _sample():
    from pipeline.interviews import load_interviews, select_sample
    return select_sample(load_interviews(config.INTERVIEWS_DIR))


def _borrowing_reply(sample):
    """Batch 1's reply files a sentence unique to interview 0 under interview 1
    as well (the pattern seen in stored replies), plus interview 0's own hit."""
    t0, t1 = sample[0], sample[1]
    line = next(ln for ln in t0.text.splitlines()
                if len(ln) > 60 and not quote_in_text(ln, t1.text))
    return json.dumps({"occurrences": [
        {"document_title": t0.title, "quote": line, "reason": "r"},
        {"document_title": t1.title, "quote": line, "reason": "r"}]})


FABRICATED = json.dumps({"occurrences": [{
    "document_title": "%s", "quote": "words that were never spoken in any interview "
    "at all, by anyone, ever", "reason": "r"}]})


def _run_with(pipeline_env, monkeypatch, script):
    monkeypatch.setattr(pipeline_env, "available_adapters",
                        lambda: [_Model(script=script)])
    pipeline_env.run(label="qc", max_codes=1)
    conn = storage.init_db(config.DB_PATH)
    return conn, _latest_run(conn)


def test_a_borrowed_quote_is_re_coded_alone_and_the_run_verifies(pipeline_env, monkeypatch):
    import verify_run
    sample = _sample()
    code = pipeline_env.load_codes("codebook")[0][0].name
    conn, run_id = _run_with(pipeline_env, monkeypatch,
                             {(code, sample[0].title): [_borrowing_reply(sample)]})
    labels = [r[0] for r in conn.execute(
        "SELECT batch_label FROM raw_responses WHERE run_id=? AND final=1 "
        "AND batch_label LIKE 'recode:%'", (run_id,))]
    assert labels == [recode_label(sample[1].title, 1)]   # interview 0 already had its own hit
    # Alone, the model quotes interview 1's own text, which now stands.
    stored = conn.execute("SELECT quote FROM coding_results WHERE run_id=? AND "
                          "document_title=?", (run_id, sample[1].title)).fetchall()
    assert len(stored) == 1 and quote_in_text(stored[0][0], sample[1].text)
    report = verify_run.verify(conn, run_id)
    assert report.ok, report.render()

    # A stored re-coding reply the quote check does not call for is not part
    # of any derivation the audit can reproduce.
    cols = [r[1] for r in conn.execute("PRAGMA table_info(raw_responses)") if r[1] != "id"]
    row = dict(zip(cols, conn.execute(
        f"SELECT {','.join(cols)} FROM raw_responses WHERE run_id=? AND batch_label=?",
        (run_id, labels[0])).fetchone()))
    row.update(batch_label=recode_label(sample[2].title, 1),
               document_titles=json.dumps([sample[2].title]))
    conn.execute(f"INSERT INTO raw_responses ({','.join(row)}) VALUES "
                 f"({','.join('?' * len(row))})", tuple(row.values()))
    conn.commit()
    report = verify_run.verify(conn, run_id)
    assert any("does not call for" in f for f in report.failures), report.render()


def test_an_unresolved_interview_is_stored_without_a_verdict_and_verifies(
        pipeline_env, monkeypatch):
    import verify_run
    sample = _sample()
    code = pipeline_env.load_codes("codebook")[0][0].name
    t1 = sample[1].title
    conn, run_id = _run_with(pipeline_env, monkeypatch, {
        (code, sample[0].title): [_borrowing_reply(sample)],
        (code, t1): [FABRICATED % t1] * config.RECODE_ATTEMPTS})
    cells = dict(conn.execute("SELECT document, count FROM count_matrix WHERE run_id=? "
                              "AND model='raw-model'", (run_id,)).fetchall())
    assert t1 not in cells and len(cells) == len(sample) - 1
    n_docs = conn.execute("SELECT n_documents FROM kappa WHERE run_id=? AND "
                          "model='raw-model'", (run_id,)).fetchone()[0]
    assert n_docs == len(sample) - 1
    assert conn.execute("SELECT COUNT(*) FROM raw_responses WHERE run_id=? AND final=1 "
                        "AND batch_label LIKE ?", (run_id, f"recode:{t1}:%")
                        ).fetchone()[0] == config.RECODE_ATTEMPTS
    report = verify_run.verify(conn, run_id)
    assert report.ok, report.render()

    # Without the stored re-coding replies the result cannot be re-derived.
    conn.execute("DELETE FROM raw_responses WHERE run_id=? AND batch_label=?",
                 (run_id, recode_label(t1, 2)))
    conn.commit()
    report = verify_run.verify(conn, run_id)
    assert not report.ok
    assert any("no stored reply for re-coding" in f for f in report.failures)


def test_verify_rejects_a_stored_misfiled_quote(pipeline_env, monkeypatch):
    """A run whose stored result kept a borrowed quote -- as every run did
    before this check -- no longer re-derives."""
    import verify_run
    sample = _sample()
    code = pipeline_env.load_codes("codebook")[0][0].name
    conn, run_id = _run_with(pipeline_env, monkeypatch,
                             {(code, sample[0].title): [_borrowing_reply(sample)]})
    # Put back what the batch reply said and drop the re-coding, as an
    # unchecked run would have stored it.
    reply = json.loads(_borrowing_reply(sample))["occurrences"][1]
    conn.execute("DELETE FROM coding_results WHERE run_id=? AND document_title=?",
                 (run_id, sample[1].title))
    conn.execute("INSERT INTO coding_results (run_id, model, document_title, code_name, "
                 "quote, reason) VALUES (?, 'raw-model', ?, ?, ?, 'r')",
                 (run_id, sample[1].title, code, reply["quote"]))
    conn.execute("DELETE FROM raw_responses WHERE run_id=? AND batch_label LIKE 'recode:%'",
                 (run_id,))
    conn.commit()
    report = verify_run.verify(conn, run_id)
    assert not report.ok
