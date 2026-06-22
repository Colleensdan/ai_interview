"""Code-specific Cohen's kappa between human ground truth and LLM coding.

Per spec 4.8: binary present/absent per code per transcript, computed per code,
target > 0.80. Implemented directly (numpy not required) so the only edge cases
we must define are the degenerate ones.
"""

from __future__ import annotations

import difflib
import math
import re
from dataclasses import dataclass


def _norm_code(name: str) -> str:
    """Normalise a code name for matching: lowercase, alphanumerics only."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def match_codes(
    code_names: list[str], gt_code_names: list[str], cutoff: float = 0.85
) -> dict[str, str | None]:
    """Map each codebook code to the ground-truth code naming the same thing.

    The ground-truth export has minor naming drift from the codebook (case,
    British/US spelling, a typo like "eduaction", truncated names). We match on
    a normalised form first, then fall back to closest-match above ``cutoff``.
    Returns ``code -> gt_code`` (or ``None`` when nothing matches, meaning the
    code is treated as absent everywhere in the human data).
    """
    gt_norm = {_norm_code(g): g for g in gt_code_names}
    mapping: dict[str, str | None] = {}
    for code in code_names:
        n = _norm_code(code)
        if n in gt_norm:
            mapping[code] = gt_norm[n]
            continue
        close = difflib.get_close_matches(n, list(gt_norm), n=1, cutoff=cutoff)
        mapping[code] = gt_norm[close[0]] if close else None
    return mapping


@dataclass(frozen=True)
class KappaResult:
    code_name: str
    kappa: float  # NaN when undefined (see cohen_kappa)
    n_documents: int
    n_human_present: int
    n_llm_present: int


def cohen_kappa(human: list[int], llm: list[int]) -> float:
    """Cohen's kappa for two binary (0/1) labelings of the same items.

    Degenerate handling: if both raters use only one category, kappa is
    mathematically undefined (zero expected-disagreement variance). We return
    1.0 when the two labelings are *identical* (perfect, if trivial, agreement)
    and NaN otherwise — callers can decide how to treat NaN.
    """
    n = len(human)
    if n == 0:
        return math.nan

    agree = sum(1 for h, l in zip(human, llm) if h == l)
    po = agree / n

    h1 = sum(human) / n          # P(human = present)
    l1 = sum(llm) / n            # P(llm = present)
    pe = h1 * l1 + (1 - h1) * (1 - l1)

    if pe == 1.0:
        # Both raters constant -> undefined; perfect only if identical.
        return 1.0 if human == llm else math.nan
    return (po - pe) / (1 - pe)


def _present(count) -> int:
    try:
        return 1 if int(count) > 0 else 0
    except (TypeError, ValueError):
        return 0


def per_code_kappa(
    code_names: list[str],
    human_counts: dict[str, dict[str, int]],
    llm_matrix: dict[str, dict[str, int]],
    doc_pairs: list[tuple[str, str]],
    code_to_gt: dict[str, str | None] | None = None,
) -> list[KappaResult]:
    """Compute per-code kappa over a shared set of documents.

    ``doc_pairs`` is a list of (llm_document_title, human_document_key) for the
    documents present in both codings (the sampled, jointly-coded set). For each
    code we build present/absent vectors over those documents — missing entries
    (e.g. a code absent from the human matrix) count as absent.

    ``code_to_gt`` maps each codebook code to its ground-truth code name (see
    :func:`match_codes`); without it, an exact name match is assumed.
    """
    results: list[KappaResult] = []
    for code in code_names:
        gt_code = code_to_gt.get(code, code) if code_to_gt else code
        human_row = human_counts.get(gt_code, {}) if gt_code else {}
        llm_row = llm_matrix.get(code, {})
        human_vec = [_present(human_row.get(hkey, 0)) for _, hkey in doc_pairs]
        llm_vec = [_present(llm_row.get(title, 0)) for title, _ in doc_pairs]
        results.append(
            KappaResult(
                code_name=code,
                kappa=cohen_kappa(human_vec, llm_vec),
                n_documents=len(doc_pairs),
                n_human_present=sum(human_vec),
                n_llm_present=sum(llm_vec),
            )
        )
    return results
