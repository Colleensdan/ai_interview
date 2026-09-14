"""Generate the descriptive figures and statistics for a completed run.

    python make_figures.py                          # newest full run, both styles
    python make_figures.py --style publication
    python make_figures.py --run-id <id> --out outputs/figures

Two output conventions are produced from the same data (see
``analysis.figures.TEXT``):

* ``presentation`` — question titles and interpretive subtitles, screen-weighted
  type, 160 dpi. Intended to be narrated.
* ``publication`` — descriptive noun-phrase titles and numbered captions giving
  only what is plotted, how, and n; neutral reference lines; 300 dpi. Intended
  to stand beside running text that carries the interpretation.

The statistics are computed once and both styles plot the same numbers, so the
two sets cannot drift apart.

Reads only the results database; calls no model and writes nothing outside the
output directory.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from analysis import figures as F
from analysis import stats as S

DEFS_DB = ("/home/colleen/ai_interview_backups/sharepoint_20260906_170416/"
           "copy_a/state/coding.sqlite")


def render(out: Path, *, kf, vd, prev, rho_prev, bias, pairs, names,
           effort, rho_eff) -> list[Path]:
    """Write every figure into *out* using the currently selected style."""
    out.mkdir(parents=True, exist_ok=True)
    made = [
        F.fig_kappa_by_model(kf, S, out / "01_kappa_by_model.png"),
        F.fig_kappa_heatmap(kf, S, out / "02_kappa_heatmap.png"),
    ]
    if not vd.get("insufficient"):
        made.append(F.fig_variance_decomposition(vd, out / "03_variance_sources.png"))
    made += [
        F.fig_prevalence_paradox(prev, out / "04_kappa_paradox.png", rho_prev),
        F.fig_bias(bias, S, out / "05_over_under_application.png", kf.n_documents),
        F.fig_agreement_matrix(pairs, names, S, out / "06_who_agrees_with_whom.png",
                               n_codes=len(kf.complete_codes()),
                               n_docs=kf.n_documents),
        F.fig_code_difficulty(kf, out / "07_code_difficulty.png"),
    ]
    if effort:
        made.append(F.fig_definition_effort(
            effort, out / "08_definition_effort.png", rho_eff,
            n_models=len(kf.usable_models)))
    return made


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--out", default="outputs/figures")
    ap.add_argument("--style", choices=(F.PRESENTATION, F.PUBLICATION, "both"),
                    default="both",
                    help="Output convention. 'presentation' uses question "
                         "titles and interpretive subtitles for slides; "
                         "'publication' uses descriptive titles and numbered "
                         "methods-only captions at 300 dpi. 'both' (default) "
                         "writes each into its own subdirectory.")
    ap.add_argument("--definitions-db", default=DEFS_DB)
    args = ap.parse_args()

    conn = S.connect()
    run_id = args.run_id or S.latest_full_run(conn)
    kf = S.kappa_frame(conn, run_id)
    complete = kf.complete_codes()
    print(f"run {run_id}: {len(kf.real_models)} model(s) present, "
          f"{len(kf.usable_models)} usable, {len(kf.codes)} code(s), "
          f"{len(complete)} compared, {kf.n_documents} transcripts")
    for m in kf.partial_models:
        print(f"  EXCLUDED from cross-model figures: {S.display_name(m)} "
              f"covered only {kf.coverage(m):.0%} of codes "
              f"(needs >= {kf.MIN_COVERAGE:.0%})")

    # --- statistics (computed once; both styles plot the same numbers) -------
    from pipeline.agreement import mean_kappa

    summary: dict = {
        "run_id": run_id, "n_documents": kf.n_documents,
        "n_codes_compared": len(complete),
        "models_compared": [S.display_name(m) for m in kf.usable_models],
        "models_excluded_partial": {S.display_name(m): kf.coverage(m)
                                    for m in kf.partial_models},
        "models": {},
    }
    for m in kf.models:
        agg = mean_kappa(kf.values_for(m))
        summary["models"][S.display_name(m)] = {
            "provider": S.provider_of(m),
            "mean_kappa": agg["mean"], "median_kappa": agg["median"],
            "min": agg["min"], "max": agg["max"],
            "n_scored": agg["n_scored"], "n_unscored": agg["n_unscored"],
            "n_above_target": sum(1 for v in kf.values_for(m) if v > F.TARGET),
        }

    # Procedural codes are scored but held out of the comparison above. They are
    # reported here so reinstating them is visible without letting them move the
    # model-comparison means (analysis.stats.kappa_frame explains why).
    pf = S.procedural_frame(conn, run_id)
    if pf is not None:
        summary["procedural_codes"] = {
            "codes": pf.codes,
            "note": ("Scored, but excluded from the model comparison: these "
                     "measure adherence to the interview procedure rather than "
                     "semantic judgement."),
            "models": {
                S.display_name(m): {
                    "mean_kappa": mean_kappa(pf.values_for(m))["mean"],
                    "per_code": {c: pf.kappa.get((m, c)) for c in pf.codes},
                }
                for m in pf.models
            },
        }
        print(f"  procedural codes reported separately: {', '.join(pf.codes)}")
    else:
        print("  no procedural codes in this run (they were reinstated 2026-09-14)")

    vd = S.variance_decomposition(kf)
    summary["variance_decomposition"] = {
        k: v for k, v in vd.items() if k not in ("code_means", "model_means")}

    prev = S.prevalence_table(kf)
    rho_prev = S.spearman([r["prevalence"] for r in prev],
                          [r["mean_kappa"] for r in prev])
    summary["spearman_prevalence_vs_kappa"] = rho_prev
    summary["n_rare_codes_under_10pct"] = sum(
        1 for r in prev if r["prevalence"] < 0.10)

    bias = S.bias_table(kf)
    summary["bias"] = [{k: v for k, v in b.items() if k != "diffs"} for b in bias]

    vectors = S.presence_vectors(conn, run_id, kf)
    pairs = S.pairwise_agreement(vectors)
    names = list(kf.usable_models) + ["human"]
    mm = [pairs[(a, b)] for i, a in enumerate(kf.usable_models)
          for b in kf.usable_models[i + 1:]]
    mh = [pairs[(m, "human")] for m in kf.usable_models]
    if mm:
        summary["mean_model_model_kappa"] = sum(mm) / len(mm)
    if mh:
        summary["mean_model_human_kappa"] = sum(mh) / len(mh)

    effort, rho_eff = [], float("nan")
    try:
        effort = S.definition_effort(args.definitions_db, kf)
        rho_eff = S.spearman([r["versions"] for r in effort],
                             [r["mean_kappa"] for r in effort])
        summary["spearman_versions_vs_kappa"] = rho_eff
    except Exception as exc:  # noqa: BLE001
        print(f"  (definition-effort figure skipped: {type(exc).__name__}: {exc})")

    # --- render each requested style ----------------------------------------
    root = Path(args.out)
    styles = ([F.PRESENTATION, F.PUBLICATION] if args.style == "both"
              else [args.style])
    root.mkdir(parents=True, exist_ok=True)
    (root / "summary.json").write_text(json.dumps(summary, indent=2, default=float))

    for name in styles:
        F.set_style(name)
        out = root / name if len(styles) > 1 else root
        made = render(out, kf=kf, vd=vd, prev=prev, rho_prev=rho_prev, bias=bias,
                      pairs=pairs, names=names, effort=effort, rho_eff=rho_eff)
        print(f"\n{name}: {len(made)} figure(s) -> {out}/")
        for path in made:
            print(f"  {Path(path).name}")
    conn.close()
    print(f"\n  {root}/summary.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
