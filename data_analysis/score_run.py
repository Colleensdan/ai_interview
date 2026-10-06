"""Compute kappa for a run from coding already persisted in the database.

Scoring needs no model: a run's per-model count matrices are written to the DB as
each model finishes, and kappa is a pure function of those matrices plus the
human ground truth. ``run_pipeline.py`` nevertheless scores only at the very end,
so an interruption — a quota ceiling, a lost connection, an operator stopping the
job — throws away hours of completed coding that is still sitting in the
database. This is the recovery path: it rebuilds the kappa rows, the majority
vote, and the CSVs from what was already paid for.

It is also the reason coding and scoring are worth keeping separable: re-scoring
after a ground-truth correction costs nothing and calls nobody.

    python score_run.py                       # newest run
    python score_run.py --run-id <id>
"""

from __future__ import annotations

import argparse
import sqlite3
import sys

import config
from pipeline import scoring, storage
from pipeline.agreement import match_codes, mean_kappa
from pipeline.ground_truth import load_ground_truth_counts
from pipeline.interviews import doc_key


def _conn() -> sqlite3.Connection:
    c = storage.init_db(config.DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def load_matrices(conn, run_id: str) -> dict[str, dict]:
    """{model: {code: {document: count}}} for every model in the run."""
    out: dict[str, dict] = {}
    for r in conn.execute(
            "SELECT model, code_name, document, count FROM count_matrix "
            "WHERE run_id=?", (run_id,)):
        out.setdefault(r["model"], {}).setdefault(r["code_name"], {})[
            r["document"]] = r["count"]
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run-id", default=None)
    args = ap.parse_args()

    conn = _conn()
    run_id = args.run_id or conn.execute(
        "SELECT run_id FROM runs ORDER BY run_id DESC LIMIT 1").fetchone()["run_id"]
    matrices = load_matrices(conn, run_id)
    if not matrices:
        raise SystemExit(f"No coding persisted for run {run_id!r}.")

    # The run's own code list when it recorded one, so a code every model
    # failed is still part of the run (and simply has no vote).
    invs = storage.invocations(conn, run_id)
    all_codes = (invs[0]["provenance"]["must_match"]["codes"] if invs
                 else sorted({c for m in matrices.values() for c in m}))
    documents = sorted({d for m in matrices.values()
                        for row in m.values() for d in row})
    print(f"run {run_id}: {len(matrices)} model(s), {len(all_codes)} code(s), "
          f"{len(documents)} document(s) persisted")
    for m, mat in sorted(matrices.items(), key=lambda kv: -len(kv[1])):
        print(f"  {m:34s} {len(mat):>3}/{len(all_codes)} codes")

    # --- human side ---------------------------------------------------------
    gt_counts, gt_keys = load_ground_truth_counts(
        config.GROUND_TRUTH_COUNTS_PATH, config.GROUND_TRUTH_COUNTS_SHEET)
    doc_pairs = [(d, doc_key(d)) for d in documents if doc_key(d) in gt_keys]
    if not doc_pairs:
        raise SystemExit("No document joins the ground-truth matrix.")
    if len(doc_pairs) < len(documents):
        print(f"  NOTE: {len(documents) - len(doc_pairs)} document(s) have no "
              f"ground-truth column and are excluded from kappa.")
    code_to_gt = match_codes(all_codes, list(gt_counts),
                             aliases=config.GT_CODE_ALIASES)

    # --- the run's scoring rule (shared with run_pipeline and verify_run) ----
    # This script used to apply its own vote (models above a coverage cutoff,
    # codes all of them shared), so re-scoring a run could store a different
    # majority vote from the one the pipeline computed for the same data.
    majority, rows = scoring.score(sorted(matrices.items()), all_codes, documents,
                                   doc_pairs, gt_counts, code_to_gt)
    voted = [c for c in all_codes if c in majority]
    storage.insert_majority_matrix(conn, run_id, majority, voted, documents)
    print(f"  majority vote ({scoring.VOTE_RULE}) over {len(matrices)} model(s), "
          f"{len(voted)} code(s)")

    # Replace this run's kappa so re-scoring is idempotent.
    conn.execute("DELETE FROM kappa WHERE run_id=?", (run_id,))
    conn.commit()
    storage.insert_kappa(conn, run_id, rows)

    out = config.ensure_output_dir()
    storage.write_kappa_csv(out / f"kappa_{run_id}.csv", rows)
    storage.insert_ground_truth_matrix(
        conn, gt_counts, list(gt_counts), gt_keys)

    print(f"\nscored over {len(doc_pairs)} document(s):")
    by_model: dict[str, list] = {}
    for model, kr in rows:
        by_model.setdefault(model, []).append(kr)
    for model, krs in sorted(by_model.items()):
        agg = mean_kappa(krs)
        mean = "n/a" if agg["mean"] is None else f"{agg['mean']:.4f}"
        med = "n/a" if agg["median"] is None else f"{agg['median']:.4f}"
        above = sum(1 for k in krs
                    if k.kappa == k.kappa and k.kappa > config.KAPPA_TARGET)
        print(f"  {model:34s} mean κ {mean}  median {med}  "
              f"n={agg['n_scored']}  above target {above}")
    conn.close()
    print(f"\nwrote {out}/kappa_{run_id}.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
