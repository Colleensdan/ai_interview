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
from pipeline.agreement import match_codes, per_code_kappa
from pipeline.codebook import load_codebook
from pipeline.coding import code_across_batches, make_batches
from pipeline.ground_truth import load_ground_truth_counts
from pipeline.interviews import load_interviews, select_sample
from pipeline.matrices import build_count_matrix, majority_vote
from pipeline import storage


def _slug(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name)


def _note(msg: str) -> None:
    print(msg, flush=True)


def run(max_codes: int | None = None, max_docs: int | None = None, label: str = "") -> dict:
    out = config.ensure_output_dir()
    run_id = time.strftime("%Y%m%d_%H%M%S") + (f"_{label}" if label else "")
    suffix = f"_{label}" if label else ""

    # 1. Inputs --------------------------------------------------------------
    codes = load_codebook(config.CODEBOOK_PATH)
    excluded = set(config.EXCLUDED_CODES)
    dropped = [c.name for c in codes if c.name in excluded]
    codes = [c for c in codes if c.name not in excluded]
    if dropped:
        print(f"  excluding {len(dropped)} code(s) from coding and kappa: "
              f"{', '.join(dropped)}")
    if not codes:
        print("Every code is excluded — nothing to code.", file=sys.stderr)
        sys.exit(1)
    if max_codes:
        codes = codes[:max_codes]
    code_names = [c.name for c in codes]

    interviews = load_interviews(config.INTERVIEWS_DIR)
    sample = select_sample(interviews)
    if max_docs:
        sample = sample[:max_docs]
    titles = [iv.title for iv in sample]

    adapters = available_adapters()
    print(f"[run {run_id}] codes={len(codes)} sampled_docs={len(sample)} "
          f"({len(interviews)} total) models={[a.name for a in adapters] or 'NONE'}")
    if not adapters:
        print("No model adapters are available (no credentials). Aborting.",
              file=sys.stderr)
        sys.exit(1)

    conn = storage.init_db(config.DB_PATH)

    # Documents are sent in batches rather than as one blob: the output token
    # budget is shared with the model's reasoning tokens, so a frequent code
    # across a large corpus overflows it — and a truncated reply is
    # indistinguishable from "code not present". Codes stay sequential
    # (spec 4.2); the batches within a code are what run concurrently.
    batches = make_batches(sample)
    if len(batches) > 1:
        print(f"  {len(batches)} batch(es) of up to {config.DOCS_PER_CALL} document(s) "
              f"per code, {config.MAX_CONCURRENT_CALLS} in flight")

    # 2-5. Per-model coding + matrices --------------------------------------
    per_model_matrices: list[tuple[str, dict]] = []
    failed: dict[str, list[str]] = {}
    for adapter in adapters:
        print(f"  coding with {adapter.name} ...")
        hits = []
        for i, code in enumerate(codes, 1):
            t0 = time.time()
            try:
                code_hits = code_across_batches(
                    adapter, code.name, code.definition, batches, note=_note)
            except Exception as exc:  # noqa: BLE001
                # One code's failure must not discard the whole run's work: an
                # unexpected response shape once cost 44 completed codes,
                # because nothing is persisted until the loop finishes. The
                # code is dropped from scoring rather than recorded as absent,
                # which would look like a real (and very poor) result.
                failed.setdefault(adapter.name, []).append(code.name)
                print(f"    ! [{i}/{len(codes)}] {code.name}: FAILED after "
                      f"{time.time() - t0:.0f}s ({type(exc).__name__}: {exc}); "
                      f"excluded from this run.", flush=True)
                continue
            hits.extend(code_hits)
            print(f"    [{i}/{len(codes)}] {code.name}: "
                  f"{len(code_hits)} occurrence(s) ({time.time() - t0:.0f}s)",
                  flush=True)

        model_codes = [c for c in code_names
                       if c not in set(failed.get(adapter.name, []))]
        results_csv = out / f"results_{_slug(adapter.name)}{suffix}.csv"
        storage.write_results_csv(results_csv, hits)
        storage.insert_coding_results(conn, run_id, adapter.name, hits)

        matrix = build_count_matrix(hits, model_codes, titles)
        matrix_csv = out / f"countmatrix_{_slug(adapter.name)}{suffix}.csv"
        storage.write_matrix_csv(matrix_csv, matrix, model_codes, titles)
        storage.insert_count_matrix(conn, run_id, adapter.name, matrix, model_codes, titles)
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
    # Transcripts with no ground-truth column used to vanish here without a
    # word, leaving kappa quietly computed over a smaller set than reported.
    orphans = [iv.title for iv in sample if iv.key not in gt_keys]
    if orphans:
        lost = len(orphans) / len(sample)
        print(f"  WARNING: {len(orphans)}/{len(sample)} sampled transcript(s) have no "
              f"column in the ground-truth matrix and are excluded from kappa: "
              f"{', '.join(orphans[:8])}"
              f"{f' (+{len(orphans) - 8} more)' if len(orphans) > 8 else ''}")
        if lost > config.MAX_DOC_LOSS_FRACTION:
            print(f"Aborting: {lost:.0%} of documents failed to join, above the "
                  f"{config.MAX_DOC_LOSS_FRACTION:.0%} limit. This is an input "
                  f"mismatch, not a data property — run --validate-only.",
                  file=sys.stderr)
            sys.exit(1)
    if not doc_pairs:
        print("Aborting: no sampled transcript joins to the ground-truth matrix.",
              file=sys.stderr)
        sys.exit(1)

    code_to_gt = match_codes(code_names, gt_code_names)
    unmatched = [c for c, g in code_to_gt.items() if g is None]
    if unmatched:
        print(f"  WARNING: {len(unmatched)} code(s) had no ground-truth match and are "
              f"scored against an all-absent human row, which makes their kappa "
              f"meaningless: {unmatched}")
    kappa_rows = []
    scored = per_model_matrices + [("majority_vote", majority)]
    for model_name, matrix in scored:
        # A code that errored for this model has no coding to compare against;
        # scoring it would report a confident kappa for work never done.
        model_codes = [c for c in code_names if c not in set(failed.get(model_name, []))]
        for kr in per_code_kappa(model_codes, gt_counts, matrix, doc_pairs, code_to_gt):
            kappa_rows.append((model_name, kr))
    storage.write_kappa_csv(out / f"kappa{suffix}.csv", kappa_rows)
    storage.insert_kappa(conn, run_id, kappa_rows)

    conn.close()

    # Console summary --------------------------------------------------------
    print(f"\n[run {run_id}] done. Outputs in {out}")
    for model_name, names in failed.items():
        print(f"  WARNING: {model_name} failed on {len(names)} code(s), which are "
              f"absent from its matrix and kappa: {', '.join(names)}")
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
    p.add_argument("--validate-only", action="store_true",
                   help="Cross-check the inputs and exit without calling any model.")
    p.add_argument("--skip-validate", action="store_true",
                   help="Skip the input cross-check (not recommended).")
    args = p.parse_args()

    if not args.skip_validate:
        from pipeline.validate_inputs import validate
        report = validate()
        print(report.render())
        print()
        if not report.ok:
            print("Refusing to run on inconsistent inputs. Fix the above, or pass "
                  "--skip-validate to override.", file=sys.stderr)
            sys.exit(2)
    if args.validate_only:
        return

    run(max_codes=args.max_codes, max_docs=args.max_docs, label=args.label)


if __name__ == "__main__":
    main()
