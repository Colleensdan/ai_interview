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
from pipeline.agreement import cohen_kappa, match_codes, per_code_kappa
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


def test_majority_vote_takes_the_modal_count():
    a = {"worry": {"d.txt": 1}}
    b = {"worry": {"d.txt": 1}}
    c = {"worry": {"d.txt": 5}}
    assert majority_vote([a, b, c], ["worry"], ["d.txt"])["worry"]["d.txt"] == 1
