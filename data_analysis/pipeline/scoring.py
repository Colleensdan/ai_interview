"""The run's scoring rule, in one place.

``run_pipeline`` and ``verify_run`` both call :func:`score`, so the kappa a run
stores and the kappa an audit recomputes are produced by the same code. A second
copy of this logic is how two scripts came to disagree about the majority vote.
"""

from __future__ import annotations

from pipeline.agreement import per_code_kappa
from pipeline.matrices import VOTE_RULE, majority_vote  # noqa: F401  (re-exported)

MAJORITY = "majority_vote"


def score(voters, code_names, titles, doc_pairs, gt_counts, code_to_gt,
          failed=None):
    """``(majority_matrix, kappa_rows)`` for a run.

    *voters* is ``[(model, matrix)]`` for every model the run holds. The vote
    follows ``VOTE_RULE`` (presence majority) over every model that coded the
    code; each model is scored only on codes in its own matrix and not in
    ``failed[model]`` -- scoring a code it never coded would report a confident
    kappa for work never done.
    """
    failed = failed or {}
    voters = sorted(voters)
    majority = majority_vote([m for _, m in voters], code_names, titles)
    rows = []
    for model_name, matrix in voters + [(MAJORITY, majority)]:
        skip = set(failed.get(model_name, []))
        model_codes = [c for c in code_names if c in matrix and c not in skip]
        for kr in per_code_kappa(model_codes, gt_counts, matrix, doc_pairs, code_to_gt):
            rows.append((model_name, kr))
    return majority, rows
