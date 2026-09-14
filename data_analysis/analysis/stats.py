"""Descriptive statistics over a completed multi-model run.

Everything here reads the results database; nothing calls a model. The questions
it exists to answer, in the order they matter to the study:

1. How well does each model agree with the human coders, and how consistently
   across codes?  -> ``kappa_frame``
2. Is the variation we see driven by the MODEL or by the CODE? If every model
   fails on the same codes, a better model cannot help and the codebook is the
   bottleneck.  -> ``variance_decomposition``
3. Is a low kappa a real disagreement or a base-rate artefact? Cohen's kappa is
   depressed for rare categories even when raw agreement is high — the "kappa
   paradox" — so a rare code can look broken when it is merely rare.
   -> ``prevalence_table``, ``pabak``
4. When a model is wrong, is it over- or under-applying the code? Those have
   opposite fixes (tighten vs broaden the definition), and kappa hides the
   direction entirely.  -> ``bias_table``
5. Do the models agree with EACH OTHER more than with the humans? If so they
   share a systematic reading, which is a different diagnosis from noise.
   -> ``pairwise_agreement``
6. Did the reviewers' editing effort pay off?  -> ``definition_effort``
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass

import config
from models.catalog import get_spec
from pipeline.agreement import cohen_kappa

MAJORITY = "majority_vote"


# --- loading -----------------------------------------------------------------

def connect(db_path=None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path or config.DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def latest_full_run(conn) -> str:
    row = conn.execute(
        "SELECT run_id FROM runs WHERE is_full=1 ORDER BY run_id DESC LIMIT 1").fetchone()
    if row is None:
        raise SystemExit("No full run recorded — run run_pipeline.py first.")
    return row["run_id"]


def display_name(adapter_name: str) -> str:
    """'openai_v1-gpt-5.2' -> 'gpt-5.2'; majority vote keeps a readable label."""
    if adapter_name == MAJORITY:
        return "majority vote"
    for prefix in ("azure_openai-", "openai_v1-", "anthropic-"):
        if adapter_name.startswith(prefix):
            return adapter_name[len(prefix):]
    return adapter_name


def provider_of(adapter_name: str) -> str:
    if adapter_name == MAJORITY:
        return "Ensemble"
    return get_spec(display_name(adapter_name)).provider


# --- 1. per-code kappa -------------------------------------------------------

@dataclass(frozen=True)
class KappaFrame:
    """Tidy per (model, code) kappa for one run, plus the presence counts."""

    run_id: str
    models: list[str]                       # adapter names, real models first
    codes: list[str]
    kappa: dict[tuple[str, str], float | None]   # (model, code) -> kappa
    n_human: dict[tuple[str, str], int]
    n_llm: dict[tuple[str, str], int]
    n_documents: int

    def values_for(self, model: str) -> list[float]:
        """Defined kappas for one model (NaN/None dropped, never zero-filled)."""
        return [v for c in self.codes
                if (v := self.kappa.get((model, c))) is not None and not _nan(v)]

    def values_for_code(self, code: str) -> list[float]:
        return [v for m in self.usable_models
                if (v := self.kappa.get((m, code))) is not None and not _nan(v)]

    #: A model must have scored at least this share of the run's codes to enter
    #: the cross-model comparison. Without a floor, one model that stopped
    #: part-way (an API quota ceiling, say) would drag ``complete_codes`` down to
    #: its own handful of codes and every other model would silently be compared
    #: on that tiny subset — a far worse distortion than omitting it.
    MIN_COVERAGE = 0.90

    @property
    def real_models(self) -> list[str]:
        """Models excluding the majority-vote pseudo-model.

        The vote is derived FROM the others, so including it in a
        between-models variance term would double-count them.
        """
        return [m for m in self.models if m != MAJORITY]

    def coverage(self, model: str) -> float:
        scored = sum(1 for c in self.codes
                     if self.kappa.get((model, c)) is not None
                     and not _nan(self.kappa[(model, c)]))
        return scored / len(self.codes) if self.codes else 0.0

    @property
    def usable_models(self) -> list[str]:
        """Real models with enough coverage to compare (see MIN_COVERAGE)."""
        return [m for m in self.real_models
                if self.coverage(m) >= self.MIN_COVERAGE]

    @property
    def partial_models(self) -> list[str]:
        """Real models excluded from the comparison, with their coverage."""
        return [m for m in self.real_models
                if self.coverage(m) < self.MIN_COVERAGE]

    def complete_codes(self) -> list[str]:
        """Codes every USABLE model scored — the only fair basis for comparison.

        A code missing for one model (dropped after a transient fault) would
        otherwise make that model's mean an average over a different, and
        probably easier, subset than its rivals were scored on.
        """
        models = self.usable_models
        if not models:
            return []
        return [c for c in self.codes
                if all(self.kappa.get((m, c)) is not None
                       and not _nan(self.kappa[(m, c)])
                       for m in models)]


def _nan(x) -> bool:
    return isinstance(x, float) and x != x


def kappa_frame(conn, run_id: str) -> KappaFrame:
    """The cross-model comparison frame: substantive codes only.

    Procedural codes (the chatbot's closing turn, the participant's rating of
    its summary) are scored and stored like any other, but they are held out
    here. They measure instruction-following rather than semantic judgement, and
    including them would move each model's mean kappa by more than the gaps that
    separate the models -- reordering them for a reason unrelated to coding
    performance, and breaking comparability with the runs already reported.
    ``procedural_frame`` returns the same structure for those codes alone.
    """
    rows = [r for r in conn.execute(
        "SELECT model, code_name, kappa, n_documents, n_human_present, n_llm_present "
        "FROM kappa WHERE run_id=?", (run_id,)).fetchall()
        if r["code_name"] not in config.PROCEDURAL_CODES]
    if not rows:
        raise SystemExit(f"No kappa rows for run {run_id!r}.")

    kappa, n_human, n_llm = {}, {}, {}
    models, codes, n_docs = [], [], 0
    for r in rows:
        key = (r["model"], r["code_name"])
        kappa[key] = r["kappa"]
        n_human[key] = r["n_human_present"] or 0
        n_llm[key] = r["n_llm_present"] or 0
        n_docs = max(n_docs, r["n_documents"] or 0)
        if r["model"] not in models:
            models.append(r["model"])
        if r["code_name"] not in codes:
            codes.append(r["code_name"])

    reals = sorted(m for m in models if m != MAJORITY)
    ordered = reals + ([MAJORITY] if MAJORITY in models else [])
    return KappaFrame(run_id, ordered, sorted(codes), kappa, n_human, n_llm, n_docs)


def procedural_frame(conn, run_id: str) -> KappaFrame | None:
    """The procedural codes alone, in the same shape as :func:`kappa_frame`.

    Returns None when the run coded none of them, which is every run before
    they were reinstated on 2026-09-14.
    """
    rows = [r for r in conn.execute(
        "SELECT model, code_name, kappa, n_documents, n_human_present, n_llm_present "
        "FROM kappa WHERE run_id=?", (run_id,)).fetchall()
        if r["code_name"] in config.PROCEDURAL_CODES]
    if not rows:
        return None
    kappa, n_human, n_llm = {}, {}, {}
    models, codes, n_docs = [], [], 0
    for r in rows:
        key = (r["model"], r["code_name"])
        kappa[key] = r["kappa"]
        n_human[key] = r["n_human_present"] or 0
        n_llm[key] = r["n_llm_present"] or 0
        n_docs = max(n_docs, r["n_documents"] or 0)
        if r["model"] not in models:
            models.append(r["model"])
        if r["code_name"] not in codes:
            codes.append(r["code_name"])
    reals = sorted(m for m in models if m != MAJORITY)
    ordered = reals + ([MAJORITY] if MAJORITY in models else [])
    return KappaFrame(run_id, ordered, sorted(codes), kappa, n_human, n_llm, n_docs)


# --- 2. is it the model or the code? -----------------------------------------

def variance_decomposition(kf: KappaFrame) -> dict:
    """Split the variance in kappa into between-code, between-model, residual.

    A balanced two-way layout with one observation per cell (model x code), so
    the interaction is inseparable from error and lands in ``residual`` — which
    is the honest label for it. Computed on ``complete_codes()`` only, since an
    unbalanced grid would confound "this model is worse" with "this model was
    scored on fewer codes".

    The ratio is the decision-relevant number: a large between-CODE share means
    the codes differ in difficulty and a better model buys little; a large
    between-MODEL share means model choice is what matters.
    """
    models, codes = kf.usable_models, kf.complete_codes()
    if len(models) < 2 or len(codes) < 2:
        return {"n_models": len(models), "n_codes": len(codes), "insufficient": True}

    cell = {(m, c): kf.kappa[(m, c)] for m in models for c in codes}
    grand = sum(cell.values()) / len(cell)
    by_code = {c: sum(cell[(m, c)] for m in models) / len(models) for c in codes}
    by_model = {m: sum(cell[(m, c)] for c in codes) / len(codes) for m in models}

    ss_code = len(models) * sum((by_code[c] - grand) ** 2 for c in codes)
    ss_model = len(codes) * sum((by_model[m] - grand) ** 2 for m in models)
    ss_total = sum((v - grand) ** 2 for v in cell.values())
    ss_resid = ss_total - ss_code - ss_model

    return {
        "n_models": len(models), "n_codes": len(codes), "insufficient": False,
        "grand_mean": grand,
        "ss_code": ss_code, "ss_model": ss_model,
        "ss_residual": ss_resid, "ss_total": ss_total,
        "pct_code": 100 * ss_code / ss_total if ss_total else 0.0,
        "pct_model": 100 * ss_model / ss_total if ss_total else 0.0,
        "pct_residual": 100 * ss_resid / ss_total if ss_total else 0.0,
        "code_means": by_code, "model_means": by_model,
        # Spread of the per-code model means: how much room a model choice has.
        "code_sd": _sd(list(by_code.values())),
        "model_sd": _sd(list(by_model.values())),
    }


def _sd(xs: list[float]) -> float:
    if len(xs) < 2:
        return 0.0
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


# --- 3. the kappa paradox ----------------------------------------------------

def pabak(po: float) -> float:
    """Prevalence-adjusted bias-adjusted kappa = 2*Po - 1.

    Reported ALONGSIDE kappa, never instead of it. Cohen's kappa corrects for
    chance using the observed marginals, which is why a code that is rare in the
    corpus can show high raw agreement and near-zero kappa. PABAK asks the
    complementary question — how much do the raters agree, if the categories had
    been balanced — and the gap between the two is a direct measure of how much
    of a code's low score is base rate rather than disagreement.
    """
    return 2 * po - 1


def prevalence_table(kf: KappaFrame) -> list[dict]:
    """Per code: human prevalence, mean kappa across models, agreement, PABAK."""
    out = []
    n = kf.n_documents or 1
    for code in kf.complete_codes():
        ks = kf.values_for_code(code)
        if not ks:
            continue
        humans = [kf.n_human[(m, code)] for m in kf.usable_models]
        llms = [kf.n_llm[(m, code)] for m in kf.usable_models]
        prev = (sum(humans) / len(humans)) / n
        mean_k = sum(ks) / len(ks)
        # Recover observed agreement from kappa and the two marginals: with
        # binary vectors, Pe is fixed by the marginals, so Po = k(1-Pe)+Pe.
        pos = []
        for m in kf.usable_models:
            h = kf.n_human[(m, code)] / n
            l = kf.n_llm[(m, code)] / n
            pe = h * l + (1 - h) * (1 - l)
            k = kf.kappa[(m, code)]
            pos.append(k * (1 - pe) + pe)
        po = sum(pos) / len(pos)
        out.append({
            "code": code,
            "prevalence": prev,
            "n_human": sum(humans) / len(humans),
            "n_llm": sum(llms) / len(llms),
            "mean_kappa": mean_k,
            "sd_kappa": _sd(ks),
            "po": po,
            "pabak": pabak(po),
        })
    return sorted(out, key=lambda r: r["mean_kappa"])


# --- 4. direction of error ---------------------------------------------------

def bias_table(kf: KappaFrame) -> list[dict]:
    """Per model: how much it over- or under-applies codes relative to humans.

    ``mean_ratio`` is the average of (model applications / human applications)
    per code, so 1.0 is calibrated, >1 over-applies, <1 under-applies. Reported
    with the median too, because a single runaway code skews the mean.
    """
    out = []
    codes = kf.complete_codes()
    for model in kf.usable_models:
        diffs, ratios = [], []
        for code in codes:
            h, l = kf.n_human[(model, code)], kf.n_llm[(model, code)]
            diffs.append(l - h)
            if h > 0:
                ratios.append(l / h)
        srt = sorted(diffs)
        out.append({
            "model": model,
            "mean_diff": sum(diffs) / len(diffs),
            "median_diff": srt[len(srt) // 2],
            "mean_ratio": (sum(ratios) / len(ratios)) if ratios else float("nan"),
            "over": sum(1 for d in diffs if d > 0),
            "under": sum(1 for d in diffs if d < 0),
            "exact": sum(1 for d in diffs if d == 0),
            "diffs": diffs,
        })
    return out


# --- 5. do the models agree with each other? ---------------------------------

def presence_vectors(conn, run_id: str, kf: KappaFrame) -> dict[str, list[int]]:
    """Flatten each model's code x document coding to one binary vector.

    Human truth is included under the key ``"human"``, on exactly the same
    (code, document) grid, so model-vs-model and model-vs-human agreement are
    measured on the same footing and are directly comparable.
    """
    docs = [r["document"] for r in conn.execute(
        "SELECT DISTINCT document FROM count_matrix WHERE run_id=? ORDER BY document",
        (run_id,))]
    codes = kf.complete_codes()
    grid = [(c, d) for c in codes for d in docs]

    vectors: dict[str, list[int]] = {}
    for model in kf.usable_models:
        counts = {(r["code_name"], r["document"]): r["count"] for r in conn.execute(
            "SELECT code_name, document, count FROM count_matrix "
            "WHERE run_id=? AND model=?", (run_id, model))}
        vectors[model] = [1 if counts.get(k, 0) > 0 else 0 for k in grid]

    # The human side is keyed by document KEY, not the transcript filename.
    from pipeline.interviews import doc_key
    human = {(r["code_name"], r["document"]): r["count"] for r in conn.execute(
        "SELECT code_name, document, count FROM ground_truth_matrix")}
    gt_alias = _gt_alias_map(codes, {c for c, _ in human})
    vectors["human"] = [
        1 if human.get((gt_alias.get(c, c), doc_key(d)), 0) > 0 else 0
        for c, d in grid
    ]
    return vectors


def _gt_alias_map(codes, gt_codes) -> dict[str, str]:
    from pipeline.agreement import match_codes
    return {c: g for c, g in
            match_codes(list(codes), sorted(gt_codes),
                        aliases=config.GT_CODE_ALIASES).items() if g}


def pairwise_agreement(vectors: dict[str, list[int]]) -> dict[tuple[str, str], float]:
    """Cohen's kappa between every pair of raters (models and the humans)."""
    names = list(vectors)
    out = {}
    for i, a in enumerate(names):
        for b in names[i:]:
            k = 1.0 if a == b else cohen_kappa(vectors[a], vectors[b])
            out[(a, b)] = out[(b, a)] = k
    return out


# --- 6. did editing the definitions pay off? ---------------------------------

def definition_effort(defs_db, kf: KappaFrame) -> list[dict]:
    """Per code: how many versions the reviewers wrote, vs the kappa achieved.

    Interpreted with care — the causal arrow most likely runs backwards. Codes
    that scored badly are the ones reviewers returned to, so a NEGATIVE relation
    between edit count and kappa is what "effort went where it was needed"
    looks like, not evidence that editing hurts.
    """
    import db as _db
    from pathlib import Path

    raw = Path(defs_db).read_bytes()
    c = sqlite3.connect(":memory:")
    c.deserialize(_db._as_rollback_image(raw))
    c.row_factory = sqlite3.Row
    versions = {r["code_name"]: r["n"] for r in c.execute(
        "SELECT code_name, COUNT(*) n FROM code_definitions GROUP BY code_name")}
    lengths = {r["code_name"]: len(r["definition"] or "") for r in c.execute(
        "SELECT code_name, definition FROM code_definitions WHERE is_current=1")}
    c.close()

    out = []
    for code in kf.complete_codes():
        ks = kf.values_for_code(code)
        if not ks or code not in versions:
            continue
        out.append({"code": code, "versions": versions[code],
                    "chars": lengths.get(code, 0),
                    "mean_kappa": sum(ks) / len(ks)})
    return out


def spearman(xs: list[float], ys: list[float]) -> float:
    """Rank correlation — used because these relations are monotone, not linear."""
    def ranks(vs):
        order = sorted(range(len(vs)), key=lambda i: vs[i])
        r = [0.0] * len(vs)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and vs[order[j + 1]] == vs[order[i]]:
                j += 1
            avg = (i + j) / 2 + 1
            for k in range(i, j + 1):
                r[order[k]] = avg
            i = j + 1
        return r

    rx, ry = ranks(xs), ranks(ys)
    n = len(xs)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return num / den if den else float("nan")
