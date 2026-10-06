"""Per-code speaker scope, and holding procedural codes out of the comparison.

Reinstating the chatbot-behaviour codes (2026-09-14) had two requirements that
pull against each other: `finished` must be applied to interviewer turns, which
the blanket interviewee-only rule forbids, while the 49 substantive codes must
keep being prompted exactly as they were, so the runs already reported stay
comparable. These assert both halves.
"""

from __future__ import annotations

import sqlite3

import config
from analysis import stats as S
from models.base import SCOPE_INTERVIEWEE, SCOPE_INTERVIEWER, CodingRequest
from models.prompting import build_user_prompt
from pipeline import coding


def _req(**kw):
    base = dict(code_name="anger/irritation", code_description="Strong negative feeling.",
                merged_document="===== BEGIN DOCUMENT =====\ntext\n", document_titles=("a.txt",))
    base.update(kw)
    return CodingRequest(**base)


def test_default_scope_prompt_is_unchanged():
    """The substantive codes must be prompted byte-for-byte as before.

    If this drifts, every kappa already reported was produced under a different
    prompt and is no longer comparable with anything run afterwards.
    """
    r = _req()
    assert r.scope == SCOPE_INTERVIEWEE, "interviewee is the default scope"
    expected = (
        f"CODE NAME: {r.code_name}\n"
        f"CODE DEFINITION: {r.code_description}\n\n"
        f"Valid document titles: {list(r.document_titles)}\n\n"
        "INTERVIEW DOCUMENTS:\n"
        f"{r.merged_document}"
    )
    assert build_user_prompt(r) == expected


def test_interviewer_scope_overrides_the_transcript_tag():
    prompt = build_user_prompt(_req(code_name="finished", scope=SCOPE_INTERVIEWER))
    assert "ONLY interviewer turns" in prompt
    assert "must NOT code interviewee turns" in prompt
    # The override must quote the tag VERBATIM, em-dash included. A near-miss
    # leaves the model with two rules and no way to see they are the same one.
    from pipeline.interviews import annotate_roles
    tag = "[INTERVIEWER \u2014 do NOT code this]"
    assert tag in annotate_roles("assistant:\nhi\n\nuser:\nhello\n"), \
        "the transcript tag changed; the scope override must be updated to match"
    assert tag in prompt


def test_scope_is_resolved_from_the_code_name(monkeypatch):
    """code_batch derives scope itself, so no caller has to pass it."""
    seen = {}

    def fake(adapter, req, note, **_kw):
        seen["scope"] = req.scope
        return []

    monkeypatch.setattr(coding, "call_with_retry", fake)
    monkeypatch.setattr(config, "INTERVIEWER_ONLY_CODES", frozenset({"finished"}))

    class Doc:
        title, text = "a.txt", "user:\nhello\n"

    coding.code_batch(object(), "finished", "d", [Doc()], note=lambda _m: None)
    assert seen["scope"] == SCOPE_INTERVIEWER
    coding.code_batch(object(), "anger/irritation", "d", [Doc()], note=lambda _m: None)
    assert seen["scope"] == SCOPE_INTERVIEWEE


def _conn_with(rows):
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE kappa(run_id TEXT, model TEXT, code_name TEXT, kappa REAL,"
              " n_documents INT, n_human_present INT, n_llm_present INT)")
    c.executemany("INSERT INTO kappa VALUES (?,?,?,?,?,?,?)", rows)
    return c


def test_procedural_codes_are_held_out_of_the_comparison(monkeypatch):
    """Adding easy procedural codes must not move the model-comparison means."""
    monkeypatch.setattr(config, "PROCEDURAL_CODES", frozenset({"finished", "summary: 4"}))
    c = _conn_with([
        ("r1", "m1", "anger/irritation", 0.70, 104, 5, 5),
        ("r1", "m1", "frustration",      0.60, 104, 5, 5),
        ("r1", "m1", "finished",         0.98, 104, 63, 63),
        ("r1", "m1", "summary: 4",       0.97, 104, 37, 37),
    ])
    kf = S.kappa_frame(c, "r1")
    assert kf.codes == ["anger/irritation", "frustration"]
    assert "finished" not in kf.codes

    pf = S.procedural_frame(c, "r1")
    assert pf is not None and pf.codes == ["finished", "summary: 4"]
    c.close()


def test_procedural_frame_is_none_for_older_runs(monkeypatch):
    monkeypatch.setattr(config, "PROCEDURAL_CODES", frozenset({"finished"}))
    c = _conn_with([("r1", "m1", "frustration", 0.60, 104, 5, 5)])
    assert S.procedural_frame(c, "r1") is None
    c.close()
