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
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from pathlib import Path

import config
from models import available_adapters
from pipeline.agreement import match_codes, mean_kappa, per_code_kappa
from pipeline.codebook import load_codebook
from pipeline.coding import code_across_batches, make_batches
from pipeline.ground_truth import load_ground_truth_counts
from pipeline.interviews import load_interviews, select_sample
from pipeline.matrices import build_count_matrix, majority_vote
from pipeline import storage


def _slug(name: str) -> str:
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in name)


def load_current_definitions(db_path) -> dict[str, str]:
    """Read each code's CURRENT definition from a definitions store, read-only.

    ``Codebook.xlsx`` holds the definitions the study started with; the Task 2
    app archives every edit to ``code_definitions`` and marks one row per code
    ``is_current``. Those two answer different questions, and a run that used
    the codebook while the reviewers had spent weeks improving the definitions
    would report a kappa for work nobody is using.

    Opened by deserializing a private in-memory copy rather than connecting to
    the file: :class:`app.definitions.DefinitionStore` runs ``CREATE TABLE IF
    NOT EXISTS`` on construction, so merely opening the store to READ it writes
    to the file — unacceptable when the file is an archived copy of the
    reviewers' work. Nothing here can modify ``db_path``.
    """
    import sqlite3

    import db as _db

    raw = Path(db_path).read_bytes()
    conn = sqlite3.connect(":memory:")
    try:
        conn.deserialize(_db._as_rollback_image(raw))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT code_name, definition, version FROM code_definitions "
            "WHERE is_current=1").fetchall()
    finally:
        conn.close()
    return {r["code_name"]: (r["definition"], r["version"]) for r in rows}


def _note(msg: str) -> None:
    print(msg, flush=True)


def run(max_codes: int | None = None, max_docs: int | None = None, label: str = "",
        definitions: str = "codebook", definitions_db=None,
        model_concurrency: int = 1, resume_run_id: str | None = None) -> dict:
    out = config.ensure_output_dir()
    # Resuming appends this invocation's models to an EXISTING run. External
    # limits (a quota ceiling, a dropped connection) can stop a multi-model run
    # part-way, and without this the only way to add the missing models is to
    # re-code the ones that already succeeded — paying twice for identical work
    # and, worse, splitting one experiment across two run ids that can no longer
    # be compared as a single matrix.
    run_id = resume_run_id or (
        time.strftime("%Y%m%d_%H%M%S") + (f"_{label}" if label else ""))
    suffix = f"_{label}" if label else ""

    # 1. Inputs --------------------------------------------------------------
    codes = load_codebook(config.CODEBOOK_PATH)

    if definitions == "current":
        src = definitions_db or config.DB_PATH
        current = load_current_definitions(src)
        applied = [c for c in codes if c.name in current
                   and current[c.name][0] != c.definition]
        codes = [
            replace(c, definition=current[c.name][0]) if c.name in current else c
            for c in codes
        ]
        print(f"  definitions: CURRENT versions from {src}")
        print(f"    {len(applied)} code(s) coded with a reviewer-edited definition "
              f"instead of the codebook's "
              f"(max version v{max(current[c.name][1] for c in applied)})"
              if applied else "    (no edited definitions found; identical to codebook)")
        missing = [c.name for c in codes if c.name not in current]
        if missing:
            print(f"    WARNING: {len(missing)} code(s) absent from the definitions "
                  f"store; falling back to the codebook text for them: {missing}")
    else:
        print("  definitions: original Codebook.xlsx text")

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

    # A run is "full" only if nothing narrowed its scope. Recorded now rather
    # than inferred later from the label, because a label is a free-text string
    # and publishing the wrong run to the live app is not a recoverable mistake
    # in reviewers' minds even when it is in the database.
    if resume_run_id:
        import sqlite3 as _sqlite3

        # init_db hands back a plain connection; the columns are read by name.
        conn.row_factory = _sqlite3.Row
        prior = conn.execute("SELECT * FROM runs WHERE run_id=?",
                             (run_id,)).fetchone()
        if prior is None:
            print(f"No run {run_id!r} to resume.", file=sys.stderr)
            sys.exit(1)
        # A resume must not silently relax the scope the run was recorded with:
        # appending a truncated model to a full run would make `is_full` a lie.
        if prior["is_full"] and (max_codes is not None or max_docs is not None):
            print(f"Refusing to append a scope-limited model set to full run "
                  f"{run_id!r}.", file=sys.stderr)
            sys.exit(1)
        prior_models = [m for m in (prior["models"] or "").split(",") if m]
        merged = prior_models + [a.name for a in adapters
                                 if a.name not in prior_models]
        storage.record_run(
            conn, run_id, label=prior["label"], is_full=bool(prior["is_full"]),
            n_codes=prior["n_codes"], n_documents=prior["n_documents"],
            definitions=prior["definitions"], models=merged)
        print(f"  resuming run {run_id} (had: {', '.join(prior_models) or 'none'})")
    else:
        storage.record_run(
            conn, run_id, label=label,
            is_full=(max_codes is None and max_docs is None),
            n_codes=len(codes), n_documents=len(sample),
            definitions=definitions, models=[a.name for a in adapters],
        )

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
    write_lock = threading.Lock()

    def code_with(adapter):
        """Code every outstanding code through one model, persisting as it goes.

        Each code is written the moment it completes, and codes already stored
        for this run are skipped, so an interruption costs at most the one code
        in flight. Before this, nothing was persisted until a model finished all
        49, and a model stopped at code 30 lost everything -- a real cost when a
        weekly token allowance runs out mid-model.

        Writes happen inside the worker under a lock: a sqlite3 connection
        belongs to the thread that created it, so a worker opens its own.
        """
        prefix = f"[{adapter.name}]"
        # Resume at code level. Every completed code is persisted as it lands, so
        # an interrupted model keeps its work instead of paying for all 49 again
        # -- which matters when the weekly token allowance runs out mid-model.
        with write_lock:
            rconn = storage.init_db(config.DB_PATH)
            try:
                done = storage.codes_already_coded(rconn, run_id, adapter.name)
            finally:
                rconn.close()
        todo = [c for c in codes if c.name not in done]
        if done:
            print(f"  {prefix} resuming: {len(done)} code(s) already stored, "
                  f"{len(todo)} to go", flush=True)
        if not todo:
            print(f"  {prefix} already complete for this run; nothing to code.",
                  flush=True)
        hits = []
        for i, code in enumerate(todo, 1):
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
                with write_lock:
                    failed.setdefault(adapter.name, []).append(code.name)
                print(f"    ! {prefix} [{i}/{len(todo)}] {code.name}: FAILED after "
                      f"{time.time() - t0:.0f}s ({type(exc).__name__}: {exc}); "
                      f"excluded from this run.", flush=True)
                continue
            hits.extend(code_hits)
            # Persist immediately: a model killed at code 30 keeps 29 codes.
            with write_lock:
                wconn = storage.init_db(config.DB_PATH)
                try:
                    storage.insert_count_matrix(
                        wconn, run_id, adapter.name,
                        build_count_matrix(code_hits, [code.name], titles),
                        [code.name], titles)
                    if code_hits:
                        storage.insert_coding_results(
                            wconn, run_id, adapter.name, code_hits)
                finally:
                    wconn.close()
            print(f"    {prefix} [{i}/{len(todo)}] {code.name}: "
                  f"{len(code_hits)} occurrence(s) ({time.time() - t0:.0f}s)",
                  flush=True)

        # Read back what the run now holds for this model: with a resume that
        # includes codes an earlier invocation coded, which `hits` does not.
        with write_lock:
            rconn = storage.init_db(config.DB_PATH)
            try:
                matrix = storage.load_count_matrices(
                    rconn, run_id).get(adapter.name, {})
                all_hits = storage.load_coding_results(rconn, run_id, adapter.name)
            finally:
                rconn.close()
        model_codes = [c for c in code_names if c in matrix]
        with write_lock:
            # A sqlite3 connection belongs to the thread that created it, so a
            # worker cannot reuse the run's connection — it opens its own for
            # the write and closes it again. The lock still serialises writers,
            # which keeps this correct in file mode and in the shared-cache
            # in-memory mode the deployed app uses.
            wconn = storage.init_db(config.DB_PATH)
            try:
                storage.write_results_csv(
                    out / f"results_{_slug(adapter.name)}{suffix}.csv", all_hits)
                storage.write_matrix_csv(
                    out / f"countmatrix_{_slug(adapter.name)}{suffix}.csv",
                    matrix, model_codes, titles)
            finally:
                wconn.close()
            per_model_matrices.append((adapter.name, matrix))
        print(f"  done: {adapter.name} ({len(all_hits)} occurrence(s) stored for this run)", flush=True)

    # Models are independent of one another, so with several of them the run is
    # bounded by wall-clock time spent waiting on APIs, not by anything local.
    # Sequential stays the default: it keeps the log readable and the request
    # rate low, which is what a single-model run wants.
    if model_concurrency > 1 and len(adapters) > 1:
        workers = min(model_concurrency, len(adapters))
        print(f"  running {len(adapters)} model(s), {workers} at a time")
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(code_with, a): a for a in adapters}
            for fut in as_completed(futures):
                # Surface a worker that died outright; a failed CODE is already
                # handled above, so reaching here means the model itself broke.
                exc = fut.exception()
                if exc is not None:
                    print(f"  ! model {futures[fut].name} aborted: "
                          f"{type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
    else:
        for adapter in adapters:
            print(f"  coding with {adapter.name} ...")
            code_with(adapter)

    # Deterministic order regardless of which model finished first, so the
    # majority vote and the CSVs do not depend on network timing.
    order = {a.name: i for i, a in enumerate(adapters)}
    per_model_matrices.sort(key=lambda pair: order.get(pair[0], 0))

    # 6. Majority vote -------------------------------------------------------
    # A resumed run must vote over EVERY model the run holds, not just the ones
    # this invocation coded. Voting over the current invocation alone replaced a
    # seven-model vote with a two-model one, which scored below several of its
    # own members and was not obviously wrong on sight.
    voting = dict(per_model_matrices)
    if resume_run_id:
        prior_matrices = storage.load_count_matrices(conn, run_id)
        for name, matrix in prior_matrices.items():
            voting.setdefault(name, matrix)
        added = [n for n in prior_matrices if n not in dict(per_model_matrices)]
        if added:
            print(f"  majority vote includes {len(added)} model(s) coded earlier "
                  f"in this run: {', '.join(sorted(added))}")
    voters = sorted(voting.items())
    majority = majority_vote([m for _, m in voters], code_names, titles)
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

    code_to_gt = match_codes(code_names, gt_code_names,
                             aliases=config.GT_CODE_ALIASES)
    aliased = {c: g for c, g in code_to_gt.items()
               if c in config.GT_CODE_ALIASES and g != c}
    if aliased:
        print(f"  {len(aliased)} code(s) mapped to a renamed ground-truth row "
              f"(config.GT_CODE_ALIASES): "
              + ", ".join(f"{c!r} -> {g!r}" for c, g in aliased.items()))
    unmatched = [c for c, g in code_to_gt.items() if g is None]
    if unmatched:
        print(f"  WARNING: {len(unmatched)} code(s) have no row in the ground-truth "
              f"export and are reported as UNSCORED (excluded from the average, not "
              f"counted as zero agreement): {unmatched}")
    kappa_rows = []
    # Score every model the run holds, not only this invocation's: insert_kappa
    # replaces the run's rows, so scoring a subset would drop the rest.
    scored = voters + [("majority_vote", majority)]
    for model_name, matrix in scored:
        # A code that errored for this model has no coding to compare against;
        # scoring it would report a confident kappa for work never done. A model
        # carried over from an earlier invocation has no `failed` entry here, so
        # its completed codes are read from its own matrix instead.
        model_codes = [c for c in code_names
                       if c in matrix and c not in set(failed.get(model_name, []))]
        for kr in per_code_kappa(model_codes, gt_counts, matrix, doc_pairs, code_to_gt):
            kappa_rows.append((model_name, kr))
    storage.write_kappa_csv(out / f"kappa{suffix}.csv", kappa_rows)
    storage.insert_kappa(conn, run_id, kappa_rows)

    conn.close()

    # Console summary --------------------------------------------------------
    # Token usage, so a caching claim can be checked rather than asserted.
    from models import usage as _usage
    _u = _usage.summary()
    if _u:
        print(f"\n  token usage (prompt order: {config.PROMPT_ORDER})")
        for _m, _t in sorted(_u.items()):
            print(f"    {_m:34s} {_t['calls']:>4} calls  "
                  f"in {_t['prompt']:>10,}  out {_t['completion']:>8,}  "
                  f"cached {_t['cached']:>10,} ({_t['cache_hit_rate']:.0%})")
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
        agg = mean_kappa(krs)
        mean_txt = "n/a" if agg["mean"] is None else f"{agg['mean']:.4f}"
        median_txt = "n/a" if agg["median"] is None else f"{agg['median']:.4f}"
        print(f"    {model}: {len(scored)} codes scored, "
              f"{meets} above kappa>{config.KAPPA_TARGET}")
        print(f"      mean kappa (unweighted across codes) = {mean_txt}  "
              f"[median {median_txt}, n={agg['n_scored']}, "
              f"unscored {agg['n_unscored']}]")


def main() -> None:
    p = argparse.ArgumentParser(description="Task 1 LLM coding pipeline.")
    p.add_argument("--max-codes", type=int, default=None,
                   help="Only process the first N codes (debug/validation).")
    p.add_argument("--max-docs", type=int, default=None,
                   help="Only process the first N sampled docs (debug/validation).")
    p.add_argument("--label", default="", help="Suffix for output filenames/run id.")
    p.add_argument("--definitions", choices=("codebook", "current"), default="codebook",
                   help="Which definition text to code with: the original "
                        "Codebook.xlsx ('codebook', default) or each code's current "
                        "reviewer-edited version from the definitions store "
                        "('current'). These answer different questions — see "
                        "load_current_definitions.")
    p.add_argument("--resume-run-id", default=None,
                   help="Append these models to an EXISTING run instead of "
                        "starting a new one. Use after a run was interrupted "
                        "(e.g. an API quota ceiling) so the completed models are "
                        "not re-coded. Score afterwards with score_run.py.")
    p.add_argument("--model-concurrency", type=int, default=1,
                   help="How many MODELS to run at once (default 1, "
                        "sequential). Models are independent and the run "
                        "is API-latency bound, so >1 shortens a "
                        "multi-model run proportionally.")
    p.add_argument("--definitions-db", default=None,
                   help="SQLite file holding code_definitions, for "
                        "--definitions=current. Defaults to config.DB_PATH. Opened "
                        "read-only (an in-memory copy), so an archived database is "
                        "safe to point at.")
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

    run(max_codes=args.max_codes, max_docs=args.max_docs, label=args.label,
        definitions=args.definitions, definitions_db=args.definitions_db,
        model_concurrency=args.model_concurrency,
        resume_run_id=args.resume_run_id)


if __name__ == "__main__":
    main()
