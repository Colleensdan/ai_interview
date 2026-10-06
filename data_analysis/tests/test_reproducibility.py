"""A run's results must be traceable to the replies that produced them.

Pins the 2026-10 fixes: unusable replies are re-asked and then FAIL rather than
score as absent; every request attempt is stored verbatim; a code's counts,
quotes and reply status are written atomically; a resume cannot mix inputs;
a full run cannot come from uncommitted code; and verify_run.py re-derives
every stored number from the stored replies -- and notices tampering.
"""

from __future__ import annotations

import json
import sqlite3
import threading

import pytest

import config
from models.base import (CodeHit, CodingRequest, MalformedResponseError,
                         ModelAdapter, RawReply, TruncatedResponseError)
from pipeline import coding, provenance, storage
from pipeline.coding import call_with_retry

REQ = CodingRequest("c", "d", "=== DOCUMENT: A ===\ntext\n", ("A.txt",))
VALID = json.dumps({"occurrences": [{"document_title": "A.txt", "quote": "q", "reason": "r"}]})
OFF_TASK = json.dumps({"message": "Yes. Many people report that meditation has helped ..."})


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr("pipeline.coding.time.sleep", lambda _s: None)


class _Scripted(ModelAdapter):
    """Returns the scripted replies in order, then VALID forever."""

    name = "scripted"

    def __init__(self, *replies):
        self.replies, self.calls = list(replies), 0

    def is_available(self):
        return True

    def complete(self, request):
        self.calls += 1
        item = self.replies.pop(0) if self.replies else VALID
        if isinstance(item, BaseException):
            raise item
        return RawReply(item, "stop", {"temperature": 0})


def _recording():
    log = []
    return log, (lambda req, label, attempt, outcome, reply, detail:
                 log.append((attempt, outcome, getattr(reply, "content", None))))


# --- the retry layer -----------------------------------------------------------

def test_an_off_task_reply_is_re_asked_and_both_attempts_are_recorded():
    log, rec = _recording()
    hits = call_with_retry(_Scripted(OFF_TASK), REQ, note=lambda _m: None, recorder=rec)
    assert [h.quote for h in hits] == ["q"]
    assert [(a, o) for a, o, _ in log] == [(1, "malformed"), (2, "accepted")]
    assert log[0][2] == OFF_TASK, "the rejected reply itself must be stored"


def test_a_model_that_never_answers_validly_fails_instead_of_scoring_absent(monkeypatch):
    monkeypatch.setattr(config, "MALFORMED_RETRY_ATTEMPTS", 3)
    adapter = _Scripted(OFF_TASK, OFF_TASK, OFF_TASK, OFF_TASK)
    log, rec = _recording()
    with pytest.raises(MalformedResponseError):
        call_with_retry(adapter, REQ, note=lambda _m: None, recorder=rec)
    assert adapter.calls == 3
    assert [o for _, o, _ in log] == ["malformed"] * 3


def test_a_valid_empty_answer_is_never_re_asked():
    """Re-asking because of what a valid answer SAYS would bias the result."""
    adapter = _Scripted(json.dumps({"occurrences": []}))
    assert call_with_retry(adapter, REQ, note=lambda _m: None) == []
    assert adapter.calls == 1


def test_infrastructure_and_malformed_retries_have_separate_budgets(monkeypatch):
    monkeypatch.setattr(config, "MALFORMED_RETRY_ATTEMPTS", 2)
    adapter = _Scripted(RuntimeError("HTTP 503"), OFF_TASK, RuntimeError("HTTP 503"))
    log, rec = _recording()
    assert len(call_with_retry(adapter, REQ, note=lambda _m: None, attempts=3,
                               recorder=rec)) == 1
    assert [o for _, o, _ in log] == ["error", "malformed", "error", "accepted"]


def test_a_permanent_error_is_recorded_then_raised():
    log, rec = _recording()
    with pytest.raises(RuntimeError, match="403"):
        call_with_retry(_Scripted(RuntimeError("403 ModelNotAllowed")), REQ,
                        note=lambda _m: None, recorder=rec)
    assert [o for _, o, _ in log] == ["error"]


def test_a_single_document_that_still_truncates_fails_the_code():
    """It used to return [] -- i.e. 'absent' -- for that document."""

    class Doc:
        title, key, text = "A.txt", "A", "user:\nhello\n"

    adapter = _Scripted(TruncatedResponseError("too long"))
    with pytest.raises(TruncatedResponseError):
        coding.code_batch(adapter, "c", "d", [Doc()], note=lambda _m: None)


def test_a_split_batch_records_its_halves_under_distinct_labels():
    class Doc:
        def __init__(self, t):
            self.title, self.key, self.text = t, t, "user:\nhi\n"

    docs = [Doc("A.txt"), Doc("B.txt")]

    class Splitting(_Scripted):
        def complete(self, request):
            if len(request.document_titles) > 1:
                raise TruncatedResponseError("too long")
            return RawReply(json.dumps({"occurrences": []}), "stop")

    labels, lock = [], threading.Lock()

    def rec(req, label, attempt, outcome, reply, detail):
        with lock:
            labels.append((label, outcome))

    coding.code_batch(Splitting(), "c", "d", docs, note=lambda _m: None,
                      recorder=rec, batch_label="3")
    assert labels == [("3", "truncated"), ("3.0", "accepted"), ("3.1", "accepted")]


# --- storage -------------------------------------------------------------------

def _raw(conn, code, attempt_id, outcome="accepted"):
    storage.insert_raw_response(
        conn, run_id="r", invocation_id="i", model="m", code_name=code,
        code_attempt=attempt_id, batch_label="0", document_titles=["a.txt"],
        prompt_sha256="x", attempt=1, outcome=outcome, reply=RawReply(VALID))


def test_re_coding_to_zero_hits_clears_the_old_quotes(mem_db):
    conn = storage.init_db(config.DB_PATH)
    storage.persist_code(conn, "r", "m", "c", {"c": {"a.txt": 1}}, ["a.txt"],
                         [CodeHit("a.txt", "c", "old quote", "r")], None)
    storage.persist_code(conn, "r", "m", "c", {"c": {"a.txt": 0}}, ["a.txt"], [], None)
    assert conn.execute("SELECT COUNT(*) FROM coding_results").fetchone()[0] == 0
    assert conn.execute("SELECT count FROM count_matrix").fetchone()[0] == 0


def test_only_the_latest_attempts_accepted_replies_are_final(mem_db):
    conn = storage.init_db(config.DB_PATH)
    _raw(conn, "c", "first")
    storage.persist_code(conn, "r", "m", "c", {"c": {"a.txt": 1}}, ["a.txt"], [], "first")
    _raw(conn, "c", "second", "malformed")
    _raw(conn, "c", "second")
    storage.persist_code(conn, "r", "m", "c", {"c": {"a.txt": 1}}, ["a.txt"], [], "second")
    rows = conn.execute("SELECT code_attempt, outcome, final FROM raw_responses "
                        "ORDER BY id").fetchall()
    assert rows == [("first", "accepted", 0), ("second", "malformed", 0),
                    ("second", "accepted", 1)]


def test_persisting_a_code_is_all_or_nothing(mem_db):
    """A crash between the count-matrix write and the quotes write used to leave
    a code that resume treated as done but whose quotes were missing."""
    conn = storage.init_db(config.DB_PATH)
    storage.persist_code(conn, "r", "m", "c", {"c": {"a.txt": 1}}, ["a.txt"],
                         [CodeHit("a.txt", "c", "kept", "r")], None)
    conn.execute("CREATE TEMP TRIGGER boom BEFORE INSERT ON coding_results "
                 "BEGIN SELECT RAISE(ABORT, 'simulated crash'); END")
    with pytest.raises(sqlite3.DatabaseError, match="simulated crash"):
        storage.persist_code(conn, "r", "m", "c", {"c": {"a.txt": 0}}, ["a.txt"],
                             [CodeHit("a.txt", "c", "new", "r")], None)
    assert conn.execute("SELECT count FROM count_matrix").fetchone()[0] == 1
    assert conn.execute("SELECT quote FROM coding_results").fetchone()[0] == "kept"


def test_raw_replies_are_never_published_to_the_live_app():
    import sync_to_live
    assert "raw_responses" not in sync_to_live.RUN_TABLES
    assert "run_invocations" not in sync_to_live.RUN_TABLES


# --- provenance ----------------------------------------------------------------

class _Code:
    def __init__(self, name, definition):
        self.name, self.definition = name, definition


class _Iv:
    def __init__(self, title, text):
        self.title, self.text = title, text


def _prov(defn="feeling worried", docs=(("a.txt", "x"),)):
    return provenance.build(codes=[_Code("worry", defn)],
                            sample=[_Iv(t, x) for t, x in docs], adapters=[],
                            definitions="current", definitions_src=None,
                            definitions_max_version=None)


def test_provenance_is_a_pure_function_of_the_inputs():
    assert _prov()["must_match"] == _prov()["must_match"]


@pytest.mark.parametrize("change,key", [
    (dict(defn="feeling anxious"), "definitions_sha256"),
    (dict(docs=(("a.txt", "y"),)), "sample_sha256"),
    (dict(docs=(("b.txt", "x"), ("a.txt", "x"))), "sample_sha256"),
])
def test_any_change_to_what_is_sent_is_a_mismatch(change, key):
    assert any(m.startswith(key) for m in provenance.mismatches(_prov(), _prov(**change)))


def test_a_parser_change_is_a_mismatch(monkeypatch):
    before = _prov()
    import models.prompting as prompting
    monkeypatch.setattr(prompting, "PARSER_VERSION", 99)
    assert "parser_version: 2 -> 99" in provenance.mismatches(before, _prov())


# --- end to end ------------------------------------------------------------------

class _Model(ModelAdapter):
    """Finds the code in the first document of each batch. Scripted replies
    (keyed by code and first title) are served first -- e.g. an off-task one."""

    def __init__(self, name="raw-model", script=None, always_malformed=()):
        self.name = name
        self.script = {k: list(v) for k, v in (script or {}).items()}
        self.always_malformed = set(always_malformed)
        self.lock = threading.Lock()

    def is_available(self):
        return True

    def complete(self, request):
        if request.code_name in self.always_malformed:
            return RawReply(OFF_TASK, "stop")
        with self.lock:
            queue = self.script.get((request.code_name, request.document_titles[0]))
            if queue:
                return RawReply(queue.pop(0), "stop")
        return RawReply(json.dumps({"occurrences": [{
            "document_title": request.document_titles[0],
            "quote": f"quote for {request.code_name}", "reason": "r"}]}),
            "stop", {"temperature": 0})


@pytest.fixture
def pipeline_env(mem_db, monkeypatch, tmp_path):
    import run_pipeline
    monkeypatch.setattr(config, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(provenance, "git_state", lambda path=None: ("c0ffee", False))
    return run_pipeline


def _first_code_and_title(run_pipeline):
    codes, _, _ = run_pipeline.load_codes("codebook")
    from pipeline.interviews import load_interviews, select_sample
    sample = select_sample(load_interviews(config.INTERVIEWS_DIR))
    return codes[0].name, sample[0].title


def _latest_run(conn):
    return conn.execute("SELECT run_id FROM runs ORDER BY created_at DESC, "
                        "run_id DESC LIMIT 1").fetchone()[0]


def test_a_run_with_an_off_task_reply_verifies_end_to_end(pipeline_env, monkeypatch):
    import verify_run
    first_code, first_title = _first_code_and_title(pipeline_env)
    model = _Model(script={(first_code, first_title): [OFF_TASK]})
    monkeypatch.setattr(pipeline_env, "available_adapters", lambda: [model])
    pipeline_env.run(label="e2e", max_codes=3)

    conn = storage.init_db(config.DB_PATH)
    run_id = _latest_run(conn)
    outcomes = dict(conn.execute(
        "SELECT outcome, COUNT(*) FROM raw_responses WHERE run_id=? GROUP BY outcome",
        (run_id,)).fetchall())
    assert outcomes == {"malformed": 1, "accepted": 15}   # 3 codes x 5 batches + 1 re-ask
    # the re-asked batch still found its occurrence; it was not scored absent
    assert conn.execute("SELECT count FROM count_matrix WHERE run_id=? AND code_name=? "
                        "AND document=?", (run_id, first_code, first_title)).fetchone()[0] == 1

    report = verify_run.verify(conn, run_id)
    assert report.ok, report.render()


def test_verify_detects_a_tampered_count(pipeline_env, monkeypatch):
    import verify_run
    monkeypatch.setattr(pipeline_env, "available_adapters", lambda: [_Model()])
    pipeline_env.run(label="tamper", max_codes=2)
    conn = storage.init_db(config.DB_PATH)
    run_id = _latest_run(conn)
    conn.execute("UPDATE count_matrix SET count=1 WHERE id=(SELECT MIN(id) FROM "
                 "count_matrix WHERE run_id=? AND count=0)", (run_id,))
    conn.commit()
    report = verify_run.verify(conn, run_id)
    assert not report.ok
    assert any("re-parsed count matrix differs" in f for f in report.failures)


def test_verify_detects_a_tampered_reply(pipeline_env, monkeypatch):
    import verify_run
    monkeypatch.setattr(pipeline_env, "available_adapters", lambda: [_Model()])
    pipeline_env.run(label="tamper2", max_codes=2)
    conn = storage.init_db(config.DB_PATH)
    run_id = _latest_run(conn)
    conn.execute("UPDATE raw_responses SET content=? WHERE id=(SELECT MIN(id) FROM "
                 "raw_responses WHERE run_id=? AND final=1)",
                 (json.dumps({"occurrences": []}), run_id))
    conn.commit()
    assert not verify_run.verify(conn, run_id).ok


def test_verify_detects_a_changed_definition(pipeline_env, monkeypatch):
    import verify_run
    monkeypatch.setattr(pipeline_env, "available_adapters", lambda: [_Model()])
    pipeline_env.run(label="defs", max_codes=2)
    conn = storage.init_db(config.DB_PATH)
    run_id = _latest_run(conn)
    real = pipeline_env.load_codes

    def edited(*a, **kw):
        codes, src, v = real(*a, **kw)
        from dataclasses import replace
        return [replace(codes[0], definition=codes[0].definition + " (edited)")] + codes[1:], src, v

    monkeypatch.setattr(pipeline_env, "load_codes", edited)
    report = verify_run.verify(conn, run_id)
    assert any("definitions_sha256" in f for f in report.failures)


def test_a_code_that_never_gets_a_valid_reply_is_failed_not_absent(pipeline_env, monkeypatch):
    import verify_run
    first_code, _ = _first_code_and_title(pipeline_env)
    model = _Model(always_malformed={first_code})
    monkeypatch.setattr(pipeline_env, "available_adapters", lambda: [model])
    pipeline_env.run(label="fails", max_codes=2)
    conn = storage.init_db(config.DB_PATH)
    run_id = _latest_run(conn)
    assert first_code not in storage.codes_already_coded(conn, run_id, model.name)
    assert conn.execute("SELECT COUNT(*) FROM kappa WHERE run_id=? AND model=? AND "
                        "code_name=?", (run_id, model.name, first_code)).fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM raw_responses WHERE run_id=? AND "
                        "code_name=? AND final=1", (run_id, first_code)).fetchone()[0] == 0
    report = verify_run.verify(conn, run_id)
    assert report.ok, report.render()
    assert "never completed" in report.render()


def test_resume_refuses_different_definitions(pipeline_env, monkeypatch, capsys):
    model = _Model()
    monkeypatch.setattr(pipeline_env, "available_adapters", lambda: [model])
    pipeline_env.run(label="resume_defs", max_codes=2)
    conn = storage.init_db(config.DB_PATH)
    run_id = _latest_run(conn)
    conn.close()
    real = pipeline_env.load_codes

    def edited(*a, **kw):
        codes, src, v = real(*a, **kw)
        from dataclasses import replace
        return [replace(codes[0], definition="something else")] + codes[1:], src, v

    monkeypatch.setattr(pipeline_env, "load_codes", edited)
    called = []
    model.complete = lambda req: called.append(req)  # must never be reached
    with pytest.raises(SystemExit):
        pipeline_env.run(label="resume_defs", max_codes=2, resume_run_id=run_id)
    assert not called
    assert "definitions_sha256" in capsys.readouterr().err


def test_resume_refuses_a_run_without_provenance(pipeline_env, monkeypatch, capsys):
    conn = storage.init_db(config.DB_PATH)
    storage.record_run(conn, "legacy", label="", is_full=False, n_codes=2,
                       n_documents=104, definitions="codebook", models=["x"])
    conn.close()
    monkeypatch.setattr(pipeline_env, "available_adapters", lambda: [_Model()])
    with pytest.raises(SystemExit):
        pipeline_env.run(max_codes=2, resume_run_id="legacy")
    assert "predates provenance" in capsys.readouterr().err


def test_a_full_run_from_uncommitted_code_is_refused_before_any_call(pipeline_env, monkeypatch, capsys):
    monkeypatch.setattr(provenance, "git_state", lambda path=None: ("c0ffee", True))
    model = _Model()
    called = []
    model.complete = lambda req: called.append(req)
    monkeypatch.setattr(pipeline_env, "available_adapters", lambda: [model])
    with pytest.raises(SystemExit):
        pipeline_env.run(label="dirty")
    assert not called
    assert "not committed" in capsys.readouterr().err


def test_a_full_run_with_an_adapter_that_hides_its_replies_is_refused(pipeline_env, monkeypatch, capsys):
    class Opaque:
        name = "opaque"

        def code_one(self, request):
            raise AssertionError("must not be called")

    monkeypatch.setattr(pipeline_env, "available_adapters", lambda: [Opaque()])
    with pytest.raises(SystemExit):
        pipeline_env.run(label="opaque")
    assert "cannot return raw replies" in capsys.readouterr().err


def test_every_invocation_is_recorded_with_its_commit(pipeline_env, monkeypatch):
    monkeypatch.setattr(pipeline_env, "available_adapters", lambda: [_Model()])
    pipeline_env.run(label="inv", max_codes=1)
    conn = storage.init_db(config.DB_PATH)
    run_id = _latest_run(conn)
    pipeline_env.run(label="inv", max_codes=1, resume_run_id=run_id)
    invs = storage.invocations(conn, run_id)
    assert len(invs) == 2
    assert all(i["git_commit"] == "c0ffee" and i["finished_at"] for i in invs)
    assert invs[0]["provenance"]["must_match"] == invs[1]["provenance"]["must_match"]


def test_verify_detects_a_reply_recorded_against_a_different_prompt(pipeline_env, monkeypatch):
    """Inputs can match today while one stored reply claims a prompt that was
    never built from them -- e.g. rows copied in from another run."""
    import verify_run
    monkeypatch.setattr(pipeline_env, "available_adapters", lambda: [_Model()])
    pipeline_env.run(label="prompt", max_codes=2)
    conn = storage.init_db(config.DB_PATH)
    run_id = _latest_run(conn)
    conn.execute("UPDATE raw_responses SET prompt_sha256=? WHERE id=(SELECT MIN(id) "
                 "FROM raw_responses WHERE run_id=? AND final=1)", ("0" * 64, run_id))
    conn.commit()
    report = verify_run.verify(conn, run_id)
    assert any("prompt differs from the one sent" in f for f in report.failures)


# --- publishing ---------------------------------------------------------------

def _run_row(conn, run_id):
    conn.row_factory = sqlite3.Row
    return conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()


def test_a_complete_verified_run_may_be_published(pipeline_env, monkeypatch):
    import sync_to_live
    monkeypatch.setattr(pipeline_env, "available_adapters", lambda: [_Model()])
    pipeline_env.run(label="pub", max_codes=2)
    conn = storage.init_db(config.DB_PATH)
    assert sync_to_live.publish_problems(conn, _run_row(conn, _latest_run(conn))) == []


def test_an_interrupted_resume_is_not_published(pipeline_env, monkeypatch):
    """The state a quota ceiling leaves: a full run whose kappa is stale."""
    import sync_to_live

    class Dies(_Model):
        def complete(self, request):
            if request.code_name == self.die_on:
                raise KeyboardInterrupt("quota ceiling, operator stops the run")
            return super().complete(request)

    monkeypatch.setattr(pipeline_env, "available_adapters", lambda: [_Model("first")])
    pipeline_env.run(label="pub2", max_codes=2)
    conn = storage.init_db(config.DB_PATH)
    run_id = _latest_run(conn)
    second = Dies("second")
    second.die_on = pipeline_env.load_codes("codebook")[0][1].name
    monkeypatch.setattr(pipeline_env, "available_adapters", lambda: [second])
    with pytest.raises(KeyboardInterrupt):
        pipeline_env.run(label="pub2", max_codes=2, resume_run_id=run_id)
    problems = sync_to_live.publish_problems(conn, _run_row(conn, run_id))
    assert any("did not finish" in p for p in problems)
    assert any("second has not coded 1 of 2" in p for p in problems)
    assert any(p.startswith("verify_run:") for p in problems)


def test_a_run_with_a_failed_code_is_not_published(pipeline_env, monkeypatch):
    import sync_to_live
    first_code, _ = _first_code_and_title(pipeline_env)
    monkeypatch.setattr(pipeline_env, "available_adapters",
                        lambda: [_Model(always_malformed={first_code})])
    pipeline_env.run(label="pub3", max_codes=2)
    conn = storage.init_db(config.DB_PATH)
    problems = sync_to_live.publish_problems(conn, _run_row(conn, _latest_run(conn)))
    assert problems == ["raw-model has not coded 1 of 2 codes"]


def test_invocations_come_back_in_the_order_they_were_started(mem_db):
    """Same-second starts used to be ordered by their random ids."""
    conn = storage.init_db(config.DB_PATH)
    ids = ["ffff", "0000", "8888"]          # deliberately not sorted
    for i in ids:
        storage.record_invocation(conn, invocation_id=i, run_id="r",
                                  started_at="2026-10-06T12:00:00", git_commit="c",
                                  git_dirty=False, models=["m"], argv=[], provenance={})
    assert [i["invocation_id"] for i in storage.invocations(conn, "r")] == ids


def test_sync_refuses_an_unpublishable_run_before_touching_sharepoint(pipeline_env, monkeypatch):
    import sys
    import sync_to_live
    first_code, _ = _first_code_and_title(pipeline_env)
    monkeypatch.setattr(pipeline_env, "available_adapters",
                        lambda: [_Model(always_malformed={first_code})])
    pipeline_env.run(label="pub4", max_codes=2)
    conn = storage.init_db(config.DB_PATH)
    run_id = _latest_run(conn)
    # Mark it full so the older partial-run check cannot be what refuses it.
    conn.execute("UPDATE runs SET is_full=1 WHERE run_id=?", (run_id,))
    conn.commit()
    conn.row_factory = sqlite3.Row
    monkeypatch.setattr(sync_to_live, "open_local", lambda _p: conn)
    monkeypatch.setattr(sync_to_live.sp, "configured", lambda: True)

    def no_network(*_a, **_k):
        raise AssertionError("SharePoint must not be contacted")

    monkeypatch.setattr(sync_to_live.sp, "download_with_etag", no_network)
    monkeypatch.setattr(sys, "argv", ["sync_to_live.py", "--run-id", run_id, "--dry-run"])
    with pytest.raises(SystemExit, match=r"(?s)Refusing to publish.*has not coded 1 of 2"):
        sync_to_live.main()


def test_re_scoring_with_score_run_stores_exactly_what_the_pipeline_stored(pipeline_env, monkeypatch):
    """score_run used its own vote, so re-scoring could change stored results."""
    import sys
    import score_run
    import verify_run
    monkeypatch.setattr(pipeline_env, "available_adapters",
                        lambda: [_Model("m1"), _Model("m2", always_malformed=set()),
                                 _Model("m3")])
    pipeline_env.run(label="rescore", max_codes=3)
    conn = storage.init_db(config.DB_PATH)
    run_id = _latest_run(conn)
    before = sorted(conn.execute("SELECT model, code_name, kappa, n_documents, "
                                 "n_human_present, n_llm_present FROM kappa "
                                 "WHERE run_id=?", (run_id,)).fetchall())
    vote_before = sorted(conn.execute("SELECT code_name, document, count FROM "
                                      "majority_vote_matrix WHERE run_id=?", (run_id,)).fetchall())
    monkeypatch.setattr(sys, "argv", ["score_run.py", "--run-id", run_id])
    assert score_run.main() == 0
    after = sorted(conn.execute("SELECT model, code_name, kappa, n_documents, "
                                "n_human_present, n_llm_present FROM kappa "
                                "WHERE run_id=?", (run_id,)).fetchall())
    vote_after = sorted(conn.execute("SELECT code_name, document, count FROM "
                                     "majority_vote_matrix WHERE run_id=?", (run_id,)).fetchall())
    assert after == before and vote_after == vote_before
    assert verify_run.verify(conn, run_id).ok
