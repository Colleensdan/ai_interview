"""Tests for the agreement maths and the matrix build.

These had no coverage at all, despite Cohen's kappa being the study's headline
number. The degenerate branch in particular is worth pinning down: when both
raters are constant, kappa is mathematically undefined (0/0), and the value the
code returns there decides whether a meaningless result reads as perfect
agreement.
"""

from __future__ import annotations

import math

from models.base import CodeHit
from pipeline.agreement import (
    KappaResult,
    cohen_kappa,
    match_codes,
    mean_kappa,
    per_code_kappa,
)
from pipeline.matrices import build_count_matrix, majority_vote


# --- cohen_kappa -------------------------------------------------------------

def test_perfect_agreement_on_a_non_constant_vector():
    human = [1, 1, 1, 0, 0]
    llm = [1, 1, 1, 0, 0]
    assert cohen_kappa(human, llm) == 1.0


def test_total_disagreement_is_negative():
    assert cohen_kappa([1, 1, 0, 0], [0, 0, 1, 1]) == -1.0


def test_chance_level_agreement_is_about_zero():
    # Both rate half present, but independently: kappa should collapse to ~0.
    assert abs(cohen_kappa([1, 1, 0, 0], [1, 0, 1, 0])) < 1e-9


def test_both_raters_constant_and_identical_returns_one():
    """Undefined in principle (pe == 1), reported as 1.0 by convention.

    This is the case that produced surprising kappa=1 values: it means "neither
    rater ever disagreed because neither ever varied", not "the model is
    perfect". The validator's all-absent-transcript note exists because of it.
    """
    assert cohen_kappa([0, 0, 0], [0, 0, 0]) == 1.0
    assert cohen_kappa([1, 1, 1], [1, 1, 1]) == 1.0


def test_both_raters_constant_but_opposite_is_zero_not_undefined():
    """Constant-but-opposite is well defined, and it is 0 — not NaN.

    Expected agreement is P(both say present) + P(both say absent), which is 0
    here, so kappa = (0 - 0) / (1 - 0) = 0. Worth stating explicitly because
    `pe == 1.0` can *only* happen when the two vectors are constant AND equal;
    the `else math.nan` arm of that branch in `cohen_kappa` is unreachable.
    """
    assert cohen_kappa([0, 0, 0], [1, 1, 1]) == 0.0


def test_empty_input_is_nan():
    assert math.isnan(cohen_kappa([], []))


def test_code_the_model_finds_but_the_human_never_does_scores_zero_not_one():
    """The distinction that matters: an AI-only code is *not* kappa 1."""
    k = cohen_kappa([0, 0, 0, 0, 0, 0, 0], [1, 1, 0, 0, 0, 1, 1])
    assert not math.isnan(k)
    assert k <= 0.0


# --- match_codes -------------------------------------------------------------

def test_exact_and_normalised_matches():
    got = match_codes(["anger/irritation"], ["anger/irritation"])
    assert got["anger/irritation"] == "anger/irritation"


def test_matches_across_punctuation_and_case_drift():
    got = match_codes(["A: Mistrust"], ["a mistrust"])
    assert got["A: Mistrust"] == "a mistrust"


def test_unmatched_code_maps_to_none():
    got = match_codes(["something entirely absent"], ["anger/irritation"])
    assert got["something entirely absent"] is None


def test_unmatched_code_is_scored_against_an_absent_human_row():
    """An unmatched name must not silently look like agreement."""
    results = per_code_kappa(
        ["ghost code"],
        {"real code": {"d1": 1, "d2": 1}},
        {"ghost code": {"doc1.txt": 1, "doc2.txt": 0}},
        [("doc1.txt", "d1"), ("doc2.txt", "d2")],
        {"ghost code": None},
    )
    assert results[0].n_human_present == 0


# --- matrices ----------------------------------------------------------------

def test_build_count_matrix_counts_occurrences_per_document():
    hits = [
        CodeHit("a.txt", "worry", "q1", "r1"),
        CodeHit("a.txt", "worry", "q2", "r2"),
        CodeHit("b.txt", "worry", "q3", "r3"),
    ]
    m = build_count_matrix(hits, ["worry", "trust"], ["a.txt", "b.txt"])
    assert m["worry"]["a.txt"] == 2
    assert m["worry"]["b.txt"] == 1
    assert m["trust"]["a.txt"] == 0


def test_build_count_matrix_ignores_hits_outside_the_declared_axes():
    hits = [CodeHit("unknown.txt", "worry", "q", "r"),
            CodeHit("a.txt", "unknown code", "q", "r")]
    m = build_count_matrix(hits, ["worry"], ["a.txt"])
    assert m["worry"]["a.txt"] == 0


def _votes(*counts, code="worry", doc="d.txt"):
    """One single-cell matrix per model; None = the model did not code it."""
    return [{code: {doc: n}} if n is not None else {} for n in counts]


def _vote(*counts):
    return majority_vote(_votes(*counts), ["worry"], ["d.txt"]).get("worry", {}).get("d.txt")


def test_majority_vote_is_a_majority_on_presence_not_the_modal_count():
    """5 of 9 models found the code; the old modal-count rule said absent
    because the 'present' votes were split across 1, 2 and 3."""
    assert _vote(0, 0, 0, 0, 1, 1, 2, 2, 3) == 1


def test_a_minority_finding_the_code_is_absent():
    assert _vote(0, 0, 0, 0, 0, 1, 1, 2, 3) == 0


def test_an_exact_tie_is_absent():
    assert _vote(0, 0, 0, 0, 1, 1, 1, 1) == 0


def test_the_vote_cell_is_presence_not_a_count():
    assert _vote(5, 5, 7) == 1


def test_a_model_that_did_not_code_the_code_is_not_a_voter():
    """Counting a FAILED code as 'absent' would pull the vote toward absent."""
    assert _vote(1, 1, None) == 1
    assert _vote(1, 0, None) == 0          # 1 of 2 voters: a tie, so absent


def test_a_code_no_model_coded_has_no_vote_at_all():
    assert "worry" not in majority_vote([{}, {}], ["worry"], ["d.txt"])


def test_with_one_model_the_vote_is_its_presence():
    assert _vote(3) == 1 and _vote(0) == 0


def test_a_code_without_a_vote_is_never_scored_for_the_vote():
    from pipeline.agreement import KappaResult  # noqa: F401
    from pipeline import scoring
    voters = [("m1", {"worry": {"d.txt": 1}})]
    _, rows = scoring.score(voters, ["worry", "trust"], ["d.txt"], [("d.txt", "d")],
                            {"worry": {"d": 1}, "trust": {"d": 1}},
                            {"worry": "worry", "trust": "trust"})
    assert {(m, k.code_name) for m, k in rows} == {("m1", "worry"),
                                                   ("majority_vote", "worry")}


# --- renamed / dropped ground-truth codes ------------------------------------
#
# The 2026-09 ground-truth export renamed one code and dropped another. Both
# cases silently corrupt kappa if unhandled, and the second one corrupts it
# UPWARDS, which is the dangerous direction.

def test_alias_maps_a_renamed_ground_truth_code():
    """A rename is below any safe fuzzy cutoff, so it needs an explicit alias."""
    got = match_codes(
        ["information seeking/increased awareness"],
        ["information seeking/increased attention"],
        aliases={"information seeking/increased awareness":
                 "information seeking/increased attention"},
    )
    assert got["information seeking/increased awareness"] == \
        "information seeking/increased attention"


def test_alias_is_ignored_when_its_target_is_absent_from_the_export():
    """A stale alias must not resurrect a code the humans really dropped."""
    assert match_codes(["a"], ["b"], aliases={"a": "gone"})["a"] is None


def test_code_with_no_ground_truth_row_is_unscored_not_perfect():
    """The failure mode this guards against.

    A code the humans dropped has an all-absent human vector. If the model also
    never applies it, the two vectors are identical constants and cohen_kappa
    returns 1.0 — a perfect score for a code that cannot be scored at all, which
    would then inflate the mean kappa on the home page. It must come back as
    unscored instead.
    """
    docs = [(f"d{i}", f"d{i}") for i in range(6)]
    res = per_code_kappa(["dropped"], {}, {"dropped": {}}, docs,
                         {"dropped": None})[0]
    assert math.isnan(res.kappa)
    assert res.no_ground_truth is True
    # The trap it avoids:
    assert cohen_kappa([0] * 6, [0] * 6) == 1.0


def test_code_present_in_ground_truth_is_still_scored_normally():
    """The unscored path must not swallow codes that DO have a human row."""
    docs = [("d0", "d0"), ("d1", "d1")]
    res = per_code_kappa(["real"], {"real": {"d0": 1}}, {"real": {"d0": 1}},
                         docs, {"real": "real"})[0]
    assert res.no_ground_truth is False
    assert res.kappa == 1.0


# --- mean kappa across codes -------------------------------------------------

def test_mean_kappa_is_the_unweighted_mean_and_median():
    got = mean_kappa([0.2, 0.4, 0.9])
    assert abs(got["mean"] - 0.5) < 1e-12
    assert abs(got["median"] - 0.4) < 1e-12
    assert got["n_scored"] == 3 and got["n_unscored"] == 0
    assert got["min"] == 0.2 and got["max"] == 0.9


def test_mean_kappa_median_of_an_even_count_averages_the_middle_pair():
    assert abs(mean_kappa([0.0, 1.0, 0.4, 0.6])["median"] - 0.5) < 1e-12


def test_mean_kappa_excludes_undefined_rather_than_calling_them_zero():
    """Coercing NaN to 0 would invent an observation and depress the headline."""
    got = mean_kappa([0.5, float("nan"), None, 0.7])
    assert abs(got["mean"] - 0.6) < 1e-12
    assert got["n_scored"] == 2 and got["n_unscored"] == 2


def test_mean_kappa_accepts_kappa_results():
    got = mean_kappa([KappaResult("x", 0.8, 10, 5, 5),
                      KappaResult("y", float("nan"), 10, 0, 0, True)])
    assert abs(got["mean"] - 0.8) < 1e-12
    assert got["n_scored"] == 1 and got["n_unscored"] == 1


def test_mean_kappa_with_nothing_scorable_reports_none_not_zero():
    got = mean_kappa([float("nan"), None])
    assert got["mean"] is None and got["median"] is None
    assert got["n_scored"] == 0 and got["n_unscored"] == 2
