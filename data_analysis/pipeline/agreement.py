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
    code_names: list[str],
    gt_code_names: list[str],
    cutoff: float = 0.85,
    aliases: dict[str, str] | None = None,
) -> dict[str, str | None]:
    """Map each codebook code to the ground-truth code naming the same thing.

    The ground-truth export has minor naming drift from the codebook (case,
    British/US spelling, a typo like "eduaction", truncated names). We match on
    a normalised form first, then fall back to closest-match above ``cutoff``.
    Returns ``code -> gt_code`` (or ``None`` when nothing matches, meaning the
    code has no human row at all and cannot be scored).

    ``aliases`` maps a codebook code to the ground-truth name it was RENAMED to
    between exports (``config.GT_CODE_ALIASES``). Fuzzy matching cannot be relied
    on for renames: it is a string-similarity test, so a rename that changes a
    whole word ("increased awareness" -> "increased attention") falls below any
    cutoff safe enough to avoid mapping genuinely different codes onto each
    other. An explicit, auditable alias is the honest way to assert "these are
    the same construct" — it is a research judgement, not a spelling fix, so it
    belongs in config where a reviewer can see and challenge it.
    """
    gt_norm = {_norm_code(g): g for g in gt_code_names}
    alias_norm = {_norm_code(k): v for k, v in (aliases or {}).items()}
    mapping: dict[str, str | None] = {}
    for code in code_names:
        n = _norm_code(code)
        # Explicit rename wins over both exact and fuzzy matching, but only if
        # the aliased target actually exists in this export — a stale alias must
        # not silently resurrect a code the humans really did drop.
        target = alias_norm.get(n)
        if target is not None and _norm_code(target) in gt_norm:
            mapping[code] = gt_norm[_norm_code(target)]
            continue
        if n in gt_norm:
            mapping[code] = gt_norm[n]
            continue
        close = difflib.get_close_matches(n, list(gt_norm), n=1, cutoff=cutoff)
        mapping[code] = gt_norm[close[0]] if close else None
    return mapping


@dataclass(frozen=True)
class KappaResult:
    code_name: str
    kappa: float  # NaN when undefined (see cohen_kappa) or unscorable
    n_documents: int
    n_human_present: int
    n_llm_present: int
    # True when the code has no row in the ground-truth export at all, so there
    # is nothing to agree or disagree with. Distinct from a NaN kappa on a code
    # that IS in the ground truth (that one is a degenerate-vector artefact).
    no_ground_truth: bool = False


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
        llm_row = llm_matrix.get(code, {})
        llm_vec = [_present(llm_row.get(title, 0)) for title, _ in doc_pairs]

        if gt_code is None:
            # No human row exists for this code (the humans dropped it from the
            # export). Scoring it against an all-absent row would not be
            # conservative, it would be WRONG in a way that flatters the model:
            # if the model also never applies the code the two vectors are
            # identical constants and cohen_kappa returns 1.0 — a perfect score
            # for a code nobody could score. Report it as unscored instead, so
            # it is excluded from the average rather than inflating it.
            results.append(
                KappaResult(code, math.nan, len(doc_pairs), 0, sum(llm_vec),
                            no_ground_truth=True)
            )
            continue

        human_row = human_counts.get(gt_code, {})
        human_vec = [_present(human_row.get(hkey, 0)) for _, hkey in doc_pairs]
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


# --- averaging across codes --------------------------------------------------

def _is_nan(x) -> bool:
    return isinstance(float(x), float) and float(x) != float(x)


def mean_kappa(kappas) -> dict:
    """Unweighted ("macro") mean of per-code Cohen's kappa.

    ``kappas`` is an iterable of floats or of :class:`KappaResult`. Returns
    ``{mean, median, n_scored, n_unscored, min, max}``; ``mean`` is None when
    nothing is scorable.

    WHY THE UNWEIGHTED MEAN, and not the alternatives:

    * **Not a pooled/global kappa** (concatenating every code x document
      decision into one 2x2 table and taking one kappa). That answers a
      different question — "how often do the raters agree on a cell?" — and it
      is dominated by the base rate of the commonest codes. Because expected
      agreement is then computed from one blended marginal rather than each
      code's own, a model can post a strong pooled kappa while most individual
      codes fail: a textbook Simpson's-paradox trap. The codebook, not the
      quotation, is the object of study here, so codes must not be allowed to
      outvote each other by frequency.
    * **Not weighted by n_documents.** Every code is scored over the *same*
      document set (``doc_pairs``), so those weights are all equal and the
      weighted mean collapses to the unweighted one anyway. There is no
      precision argument to be had.
    * **Not weighted by prevalence.** That would reintroduce exactly the
      frequency dominance the macro mean exists to avoid, and prevalence here
      is a property of the interview corpus, not of how much we care about a
      code.
    * **Not a Fisher z-transformed mean.** Fisher's z is for Pearson r; kappa is
      not a correlation coefficient and the transform has no distributional
      justification for it.

    Undefined kappas are EXCLUDED, never coerced to 0. A NaN arises when a code
    is constant for both raters (no variance to disagree about) or has no human
    row at all; calling that "zero agreement" would be a fabricated observation,
    and it would drag the headline number down for codes nobody could score.
    ``n_unscored`` is returned alongside so the mean is always read against the
    number of codes it actually covers.

    The median is returned as a robustness check, not as the headline: kappa is
    bounded and often left-skewed, so a mean well below the median is a signal
    that a few badly-performing codes are carrying the average.
    """
    values = []
    unscored = 0
    for k in kappas:
        v = k.kappa if isinstance(k, KappaResult) else k
        if v is None or _is_nan(v):
            unscored += 1
            continue
        values.append(float(v))

    if not values:
        return {"mean": None, "median": None, "n_scored": 0,
                "n_unscored": unscored, "min": None, "max": None}

    values.sort()
    n = len(values)
    median = (values[n // 2] if n % 2
              else (values[n // 2 - 1] + values[n // 2]) / 2)
    return {
        "mean": sum(values) / n,
        "median": median,
        "n_scored": n,
        "n_unscored": unscored,
        "min": values[0],
        "max": values[-1],
    }
