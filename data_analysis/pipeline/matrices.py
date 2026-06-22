"""Build code x document count matrices and the cross-model majority vote."""

from __future__ import annotations

from collections import Counter
from typing import Iterable

from models.base import CodeHit

# A matrix is {code_name: {document: count}}.
Matrix = dict[str, dict[str, int]]


def build_count_matrix(
    hits: Iterable[CodeHit],
    code_names: list[str],
    documents: list[str],
) -> Matrix:
    """Count, per code per document, how many occurrences a model reported.

    Always returns a dense matrix: every code x document cell is present (0 if
    the model reported nothing there).
    """
    matrix: Matrix = {c: {d: 0 for d in documents} for c in code_names}
    for hit in hits:
        if hit.code_name in matrix and hit.document_title in matrix[hit.code_name]:
            matrix[hit.code_name][hit.document_title] += 1
    return matrix


def majority_vote(
    matrices: list[Matrix],
    code_names: list[str],
    documents: list[str],
) -> Matrix:
    """Majority-vote count per code per document, each model a voter.

    For each cell, take the most common count across models. Ties break toward
    the smaller count (conservative). With one model (N=1) this is exactly that
    model's count, so the same code path works as more models come online.
    """
    result: Matrix = {c: {d: 0 for d in documents} for c in code_names}
    for code in code_names:
        for doc in documents:
            votes = [m[code][doc] for m in matrices if code in m and doc in m[code]]
            if not votes:
                continue
            tally = Counter(votes)
            top = max(tally.values())
            winners = [v for v, n in tally.items() if n == top]
            result[code][doc] = min(winners)
    return result
