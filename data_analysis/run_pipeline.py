"""Task 1 orchestrator: end-to-end LLM coding + agreement analysis.

Run the full 50% pipeline:

    python run_pipeline.py

Limit scope (used by the mini validation run, but also handy for debugging):

    python run_pipeline.py --max-codes 2 --max-docs 2 --label mini

Everything is driven by config.py (paths, sample fraction, seed, Azure creds).
"""

from __future__ import annotations

import argparse
import sys
import time

import config
from models import available_adapters
from models.base import CodingRequest
from pipeline.agreement import match_codes, per_code_kappa
from pipeline.codebook import load_codebook
from pipeline.ground_truth import load_ground_truth_counts
from pipeline.interviews import (
    load_interviews,
    merge_documents,
    select_sample,
)
from pipeline.matrices import build_count_matrix, majority_vote
from pipeline import storage


def _slug(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name)


def run(max_codes: int | None = None, max_docs: int | None = None, label: str = "") -> dict:
    out = config.ensure_output_dir()
    run_id = time.strftime("%Y%m%d_%H%M%S") + (f"_{label}" if label else "")
    suffix = f"_{label}" if label else ""

    # 1. Inputs --------------------------------------------------------------
    codes = load_codebook(config.CODEBOOK_PATH)
    if max_codes:
        codes = codes[:max_codes]
    code_names = [c.name for c in codes]

    interviews = load_interviews(config.INTERVIEWS_DIR)
    sample = select_sample(interviews)
    if max_docs:
        sample = sample[:max_docs]
    titles = [iv.title for iv in sample]
    merged = merge_documents(sample)

    adapters = available_adapters()
    print(f"[run {run_id}] codes={len(codes)} sampled_docs={len(sample)} "
          f"({len(interviews)} total) models={[a.name for a in adapters] or 'NONE'}")
    if not adapters:
        print("No model adapters are available (no credentials). Aborting.",
              file=sys.stderr)
        sys.exit(1)

    conn = storage.init_db(config.DB_PATH)

    # 2-5. Per-model coding + matrices --------------------------------------
    per_model_matrices: list[tuple[str, dict]] = []
    for adapter in adapters:
        print(f"  coding with {adapter.name} ...")
        hits = []
        for i, code in enumerate(codes, 1):
            req = CodingRequest(
                code_name=code.name,
                code_description=code.definition,
                merged_document=merged,
                document_titles=tuple(titles),
            )
            t0 = time.time()
            code_hits = adapter.code_one(req)
            hits.extend(code_hits)
            print(f"    [{i}/{len(codes)}] {code.name}: "
                  f"{len(code_hits)} occurrence(s) ({time.time() - t0:.0f}s)",
                  flush=True)

        results_csv = out / f"results_{_slug(adapter.name)}{suffix}.csv"
        storage.write_results_csv(results_csv, hits)
        storage.insert_coding_results(conn, run_id, adapter.name, hits)

        matrix = build_count_matrix(hits, code_names, titles)
        matrix_csv = out / f"countmatrix_{_slug(adapter.name)}{suffix}.csv"
        storage.write_matrix_csv(matrix_csv, matrix, code_names, titles)
        storage.insert_count_matrix(conn, run_id, adapter.name, matrix, code_names, titles)
        per_model_matrices.append((adapter.name, matrix))

    # 6. Majority vote -------------------------------------------------------
    majority = majority_vote([m for _, m in per_model_matrices], code_names, titles)
    storage.write_matrix_csv(out / f"countmatrix_majority{suffix}.csv",
                             majority, code_names, titles)
    storage.insert_majority_matrix(conn, run_id, majority, code_names, titles)

    # 7. Human ground-truth matrix (its own table) --------------------------
    gt_counts, gt_keys = load_ground_truth_counts(
        config.GROUND_TRUTH_COUNTS_PATH, config.GROUND_TRUTH_COUNTS_SHEET
    )
    gt_code_names = list(gt_counts.keys())
    storage.write_matrix_csv(out / f"ground_truth_matrix{suffix}.csv",
                             gt_counts, gt_code_names, gt_keys)
    storage.insert_ground_truth_matrix(conn, gt_counts, gt_code_names, gt_keys)

    # 8. Cohen's kappa (per code, per model + majority vote) ----------------
    doc_pairs = [(iv.title, iv.key) for iv in sample if iv.key in gt_keys]
    code_to_gt = match_codes(code_names, gt_code_names)
    unmatched = [c for c, g in code_to_gt.items() if g is None]
    if unmatched:
        print(f"  note: {len(unmatched)} code(s) had no ground-truth match "
              f"(treated as absent in human data): {unmatched}")
    kappa_rows = []
    scored = per_model_matrices + [("majority_vote", majority)]
    for model_name, matrix in scored:
        for kr in per_code_kappa(code_names, gt_counts, matrix, doc_pairs, code_to_gt):
            kappa_rows.append((model_name, kr))
    storage.write_kappa_csv(out / f"kappa{suffix}.csv", kappa_rows)
    storage.insert_kappa(conn, run_id, kappa_rows)

    conn.close()

    # Console summary --------------------------------------------------------
    print(f"\n[run {run_id}] done. Outputs in {out}")
    _print_kappa_summary(kappa_rows, doc_pairs)
    return {"run_id": run_id, "output_dir": str(out),
            "doc_pairs": doc_pairs, "kappa_rows": kappa_rows}


def _print_kappa_summary(kappa_rows, doc_pairs) -> None:
    print(f"  kappa computed over {len(doc_pairs)} shared document(s).")
    by_model: dict[str, list] = {}
    for model, kr in kappa_rows:
        by_model.setdefault(model, []).append(kr)
    for model, krs in by_model.items():
        scored = [k.kappa for k in krs if not (isinstance(k.kappa, float) and k.kappa != k.kappa)]
        meets = sum(1 for k in scored if k > config.KAPPA_TARGET)
        print(f"    {model}: {len(scored)} codes scored, "
              f"{meets} above kappa>{config.KAPPA_TARGET}")


def main() -> None:
    p = argparse.ArgumentParser(description="Task 1 LLM coding pipeline.")
    p.add_argument("--max-codes", type=int, default=None,
                   help="Only process the first N codes (debug/validation).")
    p.add_argument("--max-docs", type=int, default=None,
                   help="Only process the first N sampled docs (debug/validation).")
    p.add_argument("--label", default="", help="Suffix for output filenames/run id.")
    args = p.parse_args()
    run(max_codes=args.max_codes, max_docs=args.max_docs, label=args.label)


if __name__ == "__main__":
    main()
