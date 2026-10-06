"""Build code x document count matrices and the cross-model majority vote."""

from __future__ import annotations

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


#: Which majority-vote rule produced a vote. Recorded with every run.
#:   count_plurality   -- (to 2026-10-06) the most common COUNT across models,
#:                        ties to the smaller. A cell where 4 models said 0 and
#:                        5 split 1/1/2/2/3 voted absent, though 5 of 9 found it.
#:   presence_majority -- present iff MORE than half of the models that coded
#:                        the code found it at least once (exactly half: absent).
VOTE_RULE = "presence_majority"


def majority_vote(
    matrices: list[Matrix],
    code_names: list[str],
    documents: list[str],
) -> Matrix:
    """Presence majority per code per document: 1 = present, 0 = absent.

    Kappa compares presence, so the vote is taken on presence: each model that
    coded the code is one voter, and the cell is present iff more than half of
    them found the code there at least once. A tie (exactly half, possible only
    with an even number of voters) is absent -- the conservative reading, and
    the same direction the previous rule broke ties.

    A model that did not code a code (it FAILED for that model) is not a voter
    on it: counting it as "absent" would pull the vote toward absent for a
    reason unrelated to the transcript. A code that NO model coded is left out
    of the result entirely, so nothing downstream can score a vote that was
    never taken. With one model the vote is that model's presence.
    """
    result: Matrix = {}
    for code in code_names:
        voters = [m[code] for m in matrices if code in m]
        if not voters:
            continue
        result[code] = {
            doc: int(2 * sum(1 for v in voters if v.get(doc, 0) > 0) > len(voters))
            for doc in documents
        }
    return result
