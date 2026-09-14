"""Figures for the multi-model coding experiment.

Colour follows the project's validated palette (see ``PALETTE`` below): hues are
assigned by the JOB they do, never cycled. Concretely:

* **Categorical** — provider identity only (OpenAI / Anthropic / Mistral), three
  slots, which is the number that clears the all-pairs colour-blindness gate.
  Nine models are NOT nine hues; the model is carried by position and its axis
  label, so identity never depends on colour alone.
* **Sequential** — one blue hue, light to dark, for magnitude (heatmaps).
* **Diverging** — blue/red about a neutral grey zero, for signed quantities
  (over- vs under-application). Never a rainbow, never a hue at the midpoint.
* **Status** — reserved for meets/misses the kappa target; always paired with a
  label or a marked axis line, never carried by colour alone.
"""

from __future__ import annotations

import math

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap
from matplotlib.lines import Line2D
from matplotlib.patches import Patch

# --- palette ----------------------------------------------------------------

PALETTE = {
    "surface": "#fcfcfb",
    "page": "#f9f9f7",
    "ink": "#0b0b0b",
    "ink2": "#52514e",
    "muted": "#898781",
    "grid": "#e1e0d9",
    "axis": "#c3c2b7",
    # categorical (provider identity) — validated all-pairs, light mode
    "blue": "#2a78d6",
    "orange": "#eb6834",
    "aqua": "#1baf7a",
    "violet": "#4a3aa7",
    # status
    "good": "#0ca30c",
    "critical": "#d03b3b",
    "warning": "#fab219",
}

PROVIDER_COLOR = {
    "OpenAI": PALETTE["blue"],
    "Anthropic": PALETTE["orange"],
    "Mistral": PALETTE["aqua"],
    # The vote is derived from the others, not a vendor: neutral grey keeps it
    # from reading as a fourth provider.
    "Ensemble": "#6f6e69",
}

# Sequential blue ramp, steps 100 -> 700 of the reference palette.
SEQ_BLUE = ["#e8f1fd", "#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5",
            "#256abf", "#184f95", "#0d366b"]
CMAP_SEQ = LinearSegmentedColormap.from_list("seq_blue", SEQ_BLUE)
# Diverging blue <-> red with a neutral grey midpoint (never a hue at zero).
CMAP_DIV = LinearSegmentedColormap.from_list(
    "div_br", ["#0d366b", "#256abf", "#9ec5f4", "#f0efec",
               "#f3b0af", "#d03b3b", "#8f2020"])

TARGET = 0.80


def style() -> None:
    """Global look: recessive chrome, generous whitespace, one type family.

    Publication output is set smaller and at print resolution; presentation
    output is larger and screen-weighted, because a slide is read at three
    metres and a journal figure at thirty centimetres.
    """
    pub = _style == PUBLICATION
    plt.rcParams.update({
        "figure.facecolor": PALETTE["surface"],
        "axes.facecolor": PALETTE["surface"],
        "savefig.facecolor": PALETTE["surface"],
        "font.family": "DejaVu Sans",
        "font.size": 9.5 if pub else 10.5,
        "text.color": PALETTE["ink"],
        "axes.labelcolor": PALETTE["ink2"],
        "axes.edgecolor": PALETTE["axis"],
        "axes.linewidth": 0.8,
        "axes.grid": True,
        "axes.axisbelow": True,
        "grid.color": PALETTE["grid"],
        "grid.linewidth": 0.7,
        "xtick.color": PALETTE["muted"],
        "ytick.color": PALETTE["muted"],
        "xtick.labelsize": 8.5 if pub else 9.5,
        "ytick.labelsize": 8.5 if pub else 9.5,
        "legend.frameon": False,
        "figure.dpi": 140,
        # 300 dpi is the usual minimum a journal will accept for line art.
        "savefig.dpi": 300 if pub else 160,
        "savefig.bbox": "tight",
        "savefig.pad_inches": 0.35,
    })


PRESENTATION = "presentation"
PUBLICATION = "publication"

# Figure wording, both conventions. British English throughout.
TEXT: dict[str, dict[str, dict[str, str]]] = {
    "kappa_by_model": {
        PRESENTATION: {
            "title": "Agreement with the reviewers, by model",
            "subtitle": ("Per-code Cohen's κ over {n_docs} transcripts and "
                         "{n_codes} codes. Boxes span the interquartile range; "
                         "whiskers the 5th–95th percentile."),
            "pad": 30,
            "caption": ("Undefined κ (a code neither rater varied on) is "
                        "excluded, never counted as zero."),
        },
        PUBLICATION: {
            "title": "Distribution of per-code agreement, by model",
            "caption": ("Figure 1.  Per-code Cohen's κ against the reviewers' "
                        "coding for each model ({n_codes} codes, {n_docs} "
                        "transcripts). Boxes span the interquartile range, "
                        "whiskers the 5th–95th percentiles; each marker denotes "
                        "one code. Undefined κ, arising where neither rater "
                        "varied on a code, is excluded rather than set to zero. "
                        "The dashed line indicates the pre-specified agreement "
                        "criterion (κ = 0.80)."),
        },
    },
    "kappa_heatmap": {
        PRESENTATION: {
            "title": "Where the agreement actually varies",
            "subtitle": ("Rows: codes, hardest at the bottom. Columns: models, "
                         "strongest at the left.\nHorizontal bands = the code is "
                         "the constraint; vertical bands = the model is."),
            "pad": 34,
            "caption": ("Only codes scored by every model are shown, so each "
                        "column covers an identical set."),
        },
        PUBLICATION: {
            "title": "Agreement by code and model",
            "caption": ("Figure 2.  Cohen's κ for every code × model "
                        "combination ({n_codes} codes, {n_models} models). Rows "
                        "are ordered by mean κ across models and columns by "
                        "median κ across codes. Only codes scored by every "
                        "model are included, so all columns cover an identical "
                        "set."),
        },
    },
    "variance": {
        PRESENTATION: {
            "title": "Is the bottleneck the model, or the codebook?",
            "subtitle": ("Variance in κ across {n_models} models × {n_codes} "
                         "codes, split into its sources."),
            "caption": ("Two-way layout with one observation per cell, so the "
                        "model × code interaction is inseparable from error and "
                        "is reported inside the residual."),
        },
        PUBLICATION: {
            "title": "Sources of variance in inter-rater agreement",
            "caption": ("Figure 3.  Decomposition of the total sum of squares in "
                        "Cohen's κ across a {n_models} × {n_codes} model-by-code "
                        "layout. With a single observation per cell the model × "
                        "code interaction is not separable from error and is "
                        "reported within the residual term."),
        },
    },
    "paradox": {
        PRESENTATION: {
            "title": "Is a low κ a real disagreement, or just a rare code?",
            "subtitle": "Each point is one code.{rho_sentence}",
            "caption": ("{n_rare} of {n_total} codes fall in the shaded rare "
                        "band (<10% of transcripts), where κ is structurally "
                        "depressed.  A wide gap between the two series for one "
                        "code means its low κ is largely a base-rate artefact, "
                        "not a coding failure."),
        },
        PUBLICATION: {
            "title": "Agreement by code prevalence",
            "caption": ("Figure 4.  Mean Cohen's κ and prevalence-adjusted "
                        "bias-adjusted kappa (PABAK) for each code, plotted "
                        "against the proportion of transcripts to which the "
                        "reviewers applied that code (n = {n_total}).{rho_caption} "
                        "The shaded region denotes codes applied to fewer than "
                        "10% of transcripts (n = {n_rare}), for which κ is "
                        "attenuated by the marginal distribution. The dashed "
                        "line indicates the agreement criterion (κ = 0.80)."),
        },
    },
    "bias": {
        PRESENTATION: {
            "title": "Which direction is each model wrong in?",
            "subtitle": ("Diamond = mean across codes; small dots = individual "
                         "codes. Over-application is fixed by tightening a "
                         "definition, under-application by broadening it."),
            "pad": 30,
        },
        PUBLICATION: {
            "title": "Difference in code application between models and reviewers",
            "caption": ("Figure 5.  For each model, the difference between the "
                        "number of transcripts to which the model applied a code "
                        "and the number to which the reviewers applied it, across "
                        "{n_codes} codes and {n_docs} transcripts. Diamonds "
                        "denote the mean across codes; smaller markers denote "
                        "individual codes. Positive values indicate more frequent "
                        "application by the model."),
        },
    },
    "agreement_matrix": {
        PRESENTATION: {
            "title": "Who agrees with whom",
            "subtitle": ("κ computed over every code × transcript decision. The "
                         "outlined row and column are the reviewers."),
            "caption": ("Models agreeing with each other more than with the "
                        "reviewers would indicate a shared reading of the "
                        "codebook rather than random error."),
        },
        PUBLICATION: {
            "title": "Pairwise inter-rater agreement, models and reviewers",
            "caption": ("Figure 6.  Cohen's κ for each pair of raters, computed "
                        "over all code × transcript coding decisions "
                        "({n_codes} codes × {n_docs} transcripts). Reviewer "
                        "coding is delineated by the outlined row and column. "
                        "Diagonal elements are unity by definition."),
        },
    },
    "code_difficulty": {
        PRESENTATION: {
            "title": "Which codes are hard for every model?",
            "subtitle": ("A short bar low down is a code no model can rescue — "
                         "the definition is the problem. A long bar means model "
                         "choice matters for that code."),
            "pad": 30,
        },
        PUBLICATION: {
            "title": "Agreement by code, with between-model range",
            "caption": ("Figure 7.  Mean Cohen's κ across {n_models} models for "
                        "each code (n = {n_codes}), ordered by mean. Bars span "
                        "the minimum to maximum κ observed across models. The "
                        "dashed line indicates the agreement criterion "
                        "(κ = 0.80)."),
        },
    },
    "definition_effort": {
        PRESENTATION: {
            "title": "Did the definition rewriting pay off?",
            "subtitle": ("{rho_prefix}Point size is the length of the current "
                         "definition. Read the direction of causation with care."),
            "caption": ("A negative relationship is expected and is not evidence "
                        "that editing hurts: reviewers returned to the codes that "
                        "were already scoring badly, so effort is a marker of "
                        "difficulty."),
        },
        PUBLICATION: {
            "title": "Inter-rater agreement by number of definition revisions",
            "caption": ("Figure 8.  Mean Cohen's κ across {n_models} models, "
                        "plotted against the number of definition versions "
                        "recorded for each code (n = {n_total}). Each marker "
                        "denotes one code; marker area is proportional to the "
                        "character length of the current definition."
                        "{rho_caption} The dashed line indicates the "
                        "pre-specified agreement criterion (κ = 0.80)."),
        },
    },
}


# --- styles ------------------------------------------------------------------
#
# The same data serves two audiences with genuinely different conventions, so
# the wording lives in one table rather than in two forks of the plotting code:
#
# * PRESENTATION — a question title and an interpretive subtitle. On a slide the
#   figure is narrated, the title earns attention, and stating the takeaway on
#   the chart is a feature.
# * PUBLICATION — a descriptive noun-phrase title and a numbered caption giving
#   only what is plotted, how, and n. A question title leads the reader, and an
#   interpretive caption asserts as fact what belongs in the running text where
#   it can be qualified, argued and cited.
#
# Keeping both in one table means a change to the underlying result cannot
# update one voice and silently leave the other stale.

_style = PRESENTATION


def set_style(name: str) -> None:
    """Select the output convention and apply its typography."""
    global _style
    if name not in (PRESENTATION, PUBLICATION):
        raise ValueError(f"unknown style {name!r}")
    _style = name
    style()


def current_style() -> str:
    return _style


def _headline(ax, fig, key: str, *, extra: str = "", **fmt) -> None:
    """Render title / subtitle / caption for *key* in the active style.

    *extra* is appended to the caption rather than drawn as a second one. Both
    caption writers place text at a fixed figure position, so a second call
    overprints the first: the figure ends up with two captions superimposed and
    neither legible. A caller with a conditional note passes it here.
    """
    entry = TEXT[key][_style]
    title = entry["title"].format(**fmt)
    cap = entry.get("caption", "")
    cap = cap.format(**fmt) if cap else ""
    if extra:
        cap = f"{cap} {extra.strip()}".strip()
    if _style == PUBLICATION:
        _fig_label(ax, title)
        if cap:
            _numbered_caption(fig, cap)
        return
    sub = entry.get("subtitle", "").format(**fmt)
    _titles(ax, title, sub, pad=entry.get("pad", 26))
    if cap:
        _caption(fig, cap)


def _titles(ax, title: str, subtitle: str = "", *, pad: float = 22.0) -> None:
    """Title + explanatory subtitle. The subtitle carries the reading.

    The subtitle is anchored va="bottom", so a multi-line one grows UPWARD into
    the title. The title's pad therefore has to make room for every line, not
    just the first.
    """
    lines = subtitle.count("\n") + 1 if subtitle else 0
    ax.set_title(title, loc="left", fontsize=14.5, fontweight="bold",
                 color=PALETTE["ink"],
                 pad=pad + (10 + 13 * (lines - 1) if subtitle else 0))
    if subtitle:
        ax.annotate(subtitle, xy=(0, 1), xycoords="axes fraction",
                    xytext=(0, pad - 4), textcoords="offset points",
                    ha="left", va="bottom", fontsize=10, linespacing=1.45,
                    color=PALETTE["ink2"], annotation_clip=False)


def _despine(ax, keep=("left", "bottom")) -> None:
    for side, spine in ax.spines.items():
        spine.set_visible(side in keep)


def _caption(fig, text: str) -> None:
    fig.text(0.005, -0.015, text, ha="left", va="top", fontsize=8.8,
             color=PALETTE["muted"], wrap=True)


def target_colour() -> str:
    """Colour for the agreement-criterion rule.

    A status green announces a verdict, which is right on a slide and wrong in a
    journal figure, where a reference line is scaffolding rather than a finding.
    """
    return PALETTE["axis"] if _style == PUBLICATION else PALETTE["good"]


def target_label(prefix_presentation: str = "target κ > ") -> str:
    return (f"κ = {TARGET:.2f}" if _style == PUBLICATION
            else f"{prefix_presentation}{TARGET:g}")


def _fig_label(ax, text: str) -> None:
    """A short descriptive title, in the noun-phrase form journals expect.

    Deliberately not a question and not a claim: a title such as "Did the
    rewriting pay off?" both leads the reader and asserts a causal reading the
    design cannot support. What the axes show is the title's only job.
    """
    ax.set_title(text, loc="left", fontsize=12, fontweight="bold",
                 color=PALETTE["ink"], pad=14)


def _numbered_caption(fig, text: str) -> None:
    """A ``Figure N.`` caption: what is plotted, how, and n — nothing else.

    Interpretation, caveats and causal reasoning belong in the running text
    where they can be argued and cited, not in a caption where they read as
    established fact.
    """
    fig.text(0.005, -0.02, text, ha="left", va="top", fontsize=9,
             color=PALETTE["ink2"], wrap=True, linespacing=1.45)


# --- F1: kappa by model, box + strip -----------------------------------------

def fig_kappa_by_model(kf, stats, path):
    """Distribution of per-code kappa for each model.

    A box plot alone hides how few codes there are and where they pile up, so
    every code is also drawn as a point. With ~49 per model that is honest and
    still readable; a summary box on its own would let a bimodal model pass for
    a mediocre one.
    """
    order = sorted(kf.models,
                   key=lambda m: -_median(kf.values_for(m) or [0]))
    partial = set(kf.partial_models)
    fig, ax = plt.subplots(figsize=(11.5, 0.62 * len(order) + 3.4))

    rng = _Rng(7)
    for i, model in enumerate(order):
        vals = kf.values_for(model)
        if not vals:
            continue
        colour = PROVIDER_COLOR.get(stats.provider_of(model), PALETTE["muted"])
        y = len(order) - i
        bp = ax.boxplot([vals], positions=[y], vert=False, widths=0.52,
                        patch_artist=True, showfliers=False, whis=(5, 95))
        for patch in bp["boxes"]:
            patch.set(facecolor=colour, alpha=0.20, edgecolor=colour, linewidth=1.6)
        for part in ("whiskers", "caps"):
            for line in bp[part]:
                line.set(color=colour, linewidth=1.4)
        for line in bp["medians"]:
            line.set(color=colour, linewidth=2.6)
        ax.scatter(vals, [y + rng.jitter(0.15) for _ in vals],
                   s=17, color=colour, alpha=0.45, linewidths=0.5,
                   edgecolors=PALETTE["surface"], zorder=3)
        ax.annotate(f"{_median(vals):.2f}", xy=(1.012, y), xycoords=("axes fraction", "data"),
                    va="center", ha="left", fontsize=9.5, color=PALETTE["ink2"],
                    fontweight="bold")

    ax.axvline(TARGET, color=target_colour(), linewidth=1.5, linestyle=(0, (5, 3)),
               zorder=1)
    ax.annotate(target_label(), xy=(TARGET, 0.35), xytext=(4, 0),
                textcoords="offset points", color=target_colour(), fontsize=9.5,
                va="bottom", ha="left",
                fontweight="normal" if current_style() == PUBLICATION else "bold")

    ax.set_yticks(range(1, len(order) + 1))
    ax.set_yticklabels(
        [stats.display_name(m) + (f"  ({kf.coverage(m):.0%} of codes)"
                                  if m in partial else "")
         for m in reversed(order)],
        fontsize=10.5, color=PALETTE["ink"])
    for tick, m in zip(ax.get_yticklabels(), reversed(order)):
        if m in partial:
            tick.set_color(PALETTE["muted"])
            tick.set_fontstyle("italic")
    ax.set_xlabel("Cohen's κ against the reviewers' coding"
                  + ("" if current_style() == PUBLICATION
                     else " (one point per code)"))
    ax.set_xlim(-0.15, 1.05)
    ax.set_ylim(0.4, len(order) + 0.75)
    ax.grid(axis="y", visible=False)
    _despine(ax, keep=("bottom",))
    ax.annotate("median", xy=(1.012, len(order) + 0.55),
                xycoords=("axes fraction", "data"), ha="left", fontsize=8.8,
                color=PALETTE["muted"])

    handles = [Patch(facecolor=c, alpha=0.25, edgecolor=c, label=p)
               for p, c in PROVIDER_COLOR.items()
               if any(stats.provider_of(m) == p for m in order)]
    ax.legend(handles=handles, loc="upper left", bbox_to_anchor=(0, -0.09),
              ncol=4, fontsize=9.5, handlelength=1.2)

    # Appended to the caption by _headline rather than drawn separately: two
    # caption calls land at the same figure coordinates and overprint.
    extra = ""
    if partial:
        extra = (" Greyed models did not complete the code set and are shown "
                 "for completeness only; they are scored on a smaller set of "
                 "codes and are excluded from all other figures.")
    _headline(ax, fig, "kappa_by_model", extra=extra, n_docs=kf.n_documents,
              n_codes=len(kf.complete_codes()))
    fig.savefig(path)
    plt.close(fig)
    return path


class _Rng:
    """Tiny deterministic jitter — the same figure every time it is rebuilt."""

    def __init__(self, seed: int) -> None:
        self._s = seed

    def jitter(self, amount: float) -> float:
        self._s = (1103515245 * self._s + 12345) % (1 << 31)
        return (self._s / (1 << 31) - 0.5) * 2 * amount


def _median(xs):
    if not xs:
        return float("nan")
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


# --- F2: code x model heatmap ------------------------------------------------

def fig_kappa_heatmap(kf, stats, path):
    """Every (code, model) kappa at once.

    The point of the figure is the STRUCTURE, not any one cell: horizontal
    banding means a code is hard for everyone (the codebook is the constraint),
    vertical banding means a model is weak everywhere (the model is). Rows are
    ordered by mean kappa so the banding is visible rather than scattered.
    """
    codes = sorted(kf.complete_codes(),
                   key=lambda c: -_mean(kf.values_for_code(c)))
    models = sorted(kf.usable_models, key=lambda m: -_median(kf.values_for(m) or [0]))
    grid = [[kf.kappa[(m, c)] for m in models] for c in codes]

    fig, ax = plt.subplots(figsize=(9.2, 0.29 * len(codes) + 3.2))
    im = ax.imshow(grid, aspect="auto", cmap=CMAP_SEQ, vmin=0, vmax=1)

    ax.set_xticks(range(len(models)))
    ax.set_xticklabels([stats.display_name(m) for m in models], rotation=38,
                       ha="right", fontsize=9.5, color=PALETTE["ink"])
    ax.set_yticks(range(len(codes)))
    ax.set_yticklabels([c[:46] for c in codes], fontsize=8.2, color=PALETTE["ink2"])
    ax.grid(False)
    ax.tick_params(length=0)
    for spine in ax.spines.values():
        spine.set_visible(False)

    cbar = fig.colorbar(im, ax=ax, fraction=0.026, pad=0.02)
    cbar.set_label("Cohen's κ", fontsize=9.5, color=PALETTE["ink2"])
    cbar.outline.set_visible(False)
    cbar.ax.tick_params(length=0, labelsize=9)

    _headline(ax, fig, "kappa_heatmap", n_codes=len(codes), n_models=len(models))
    fig.savefig(path)
    plt.close(fig)
    return path


def _mean(xs):
    return sum(xs) / len(xs) if xs else float("nan")


# --- F3: variance decomposition ----------------------------------------------

def fig_variance_decomposition(vd, path):
    """How much of the spread in kappa is the code, and how much the model.

    A composition of one whole, so it is one stacked bar rather than three: the
    question is the SHARE, and three separate bars would invite reading the
    absolute sums of squares, which mean nothing on their own.
    """
    fig, ax = plt.subplots(figsize=(11.5, 3.5))
    # The gloss under each key entry is a plain-language reading of the term.
    # That helps a narrated slide and is redundant in print, where the caption
    # already defines the decomposition — so publication carries the statistical
    # name alone.
    pub = current_style() == PUBLICATION
    parts = [
        ("Between codes", vd["pct_code"], SEQ_BLUE[6],
         "" if pub else "some codes are harder than others"),
        ("Between models", vd["pct_model"], SEQ_BLUE[4],
         "" if pub else "some models are better than others"),
        ("Residual", vd["pct_residual"], SEQ_BLUE[2],
         "interaction and error" if pub else "model × code interaction and noise"),
    ]
    left = 0.0
    for label, pct, colour, gloss in parts:
        ax.barh([0], [pct], left=left, height=0.42, color=colour,
                edgecolor=PALETTE["surface"], linewidth=2.4)
        # In-bar percentage only where the segment is wide enough to hold it;
        # a 3% slice cannot, and every segment's number appears in the key below.
        if pct > 8:
            ax.annotate(f"{pct:.0f}%", xy=(left + pct / 2, 0), ha="center",
                        va="center", fontsize=13, fontweight="bold",
                        color="#ffffff" if colour in SEQ_BLUE[4:] else PALETTE["ink"])
        left += pct

    # A key in its own row rather than labels under each segment: the segments
    # here differ by more than an order of magnitude, so positional labels
    # collide as soon as one source dominates — which is exactly the result
    # worth showing.
    for i, (label, pct, colour, gloss) in enumerate(parts):
        x = i / len(parts)
        ax.annotate("■", xy=(x, -0.62), xycoords=("axes fraction", "data"),
                    fontsize=13, color=colour, va="center", ha="left")
        ax.annotate(f"{label} — {pct:.0f}%", xy=(x, -0.62),
                    xycoords=("axes fraction", "data"), xytext=(17, 0),
                    textcoords="offset points", fontsize=10.5,
                    fontweight="bold", color=PALETTE["ink"], va="center")
        if gloss:
            ax.annotate(gloss, xy=(x, -0.90), xycoords=("axes fraction", "data"),
                        xytext=(17, 0), textcoords="offset points", fontsize=9.5,
                        color=PALETTE["ink2"], va="center")

    ax.set_xlim(0, 100)
    ax.set_ylim(-1.25, 0.5)
    ax.set_yticks([])
    ax.set_xticks([])
    ax.grid(False)
    _despine(ax, keep=())
    _headline(ax, fig, "variance", n_models=vd["n_models"], n_codes=vd["n_codes"])
    fig.savefig(path)
    plt.close(fig)
    return path


# --- F4: the kappa paradox ---------------------------------------------------

def fig_prevalence_paradox(rows, path, rho=None):
    """Kappa against how often the humans actually used the code.

    Cohen's κ divides by the chance agreement implied by the marginals, so a
    code applied to a handful of transcripts is mathematically pushed toward
    zero even when the raters agree on almost every document. PABAK — the same
    raw agreement with balanced marginals — is plotted alongside precisely
    because the GAP between the two curves is the size of that artefact.
    Without this figure a rare-but-well-coded code is indistinguishable from a
    genuinely broken one.
    """
    fig, ax = plt.subplots(figsize=(11.5, 6.4))
    xs = [r["prevalence"] for r in rows]
    ax.scatter(xs, [r["pabak"] for r in rows], s=44, color=PALETTE["muted"],
               alpha=0.5, linewidths=0.6, edgecolors=PALETTE["surface"],
               label="PABAK (same agreement, balanced marginals)", zorder=2)
    ax.scatter(xs, [r["mean_kappa"] for r in rows], s=58, color=PALETTE["blue"],
               alpha=0.85, linewidths=0.7, edgecolors=PALETTE["surface"],
               label="Cohen's κ (mean across models)", zorder=3)

    ax.axhline(TARGET, color=target_colour(), linewidth=1.4, linestyle=(0, (5, 3)))
    # x in axes fraction so the label stays put whatever the data range is.
    ax.annotate(target_label(), xy=(0.995, TARGET),
                xycoords=("axes fraction", "data"), xytext=(0, 5),
                textcoords="offset points", ha="right", va="bottom",
                color=target_colour(), fontsize=9.5,
                fontweight="normal" if current_style() == PUBLICATION else "bold")

    # Axis limits follow the data. A fixed 0–1 box on both axes would leave the
    # plot mostly empty and squash the structure that matters into a corner.
    lo_y = min([r["mean_kappa"] for r in rows] + [r["pabak"] for r in rows])
    hi_x = max(xs)
    ax.set_xlim(-0.012, hi_x * 1.10)
    ax.set_ylim(min(lo_y - 0.10, TARGET - 0.15), 1.06)

    rare = [r for r in rows if r["prevalence"] < 0.10]
    if rare:
        ax.axvspan(0, 0.10, color=PALETTE["warning"], alpha=0.10, zorder=0)
        # The band is labelled at its top edge, outside the point cloud. The
        # count itself goes in the caption: anywhere inside these axes collides
        # with either the dense upper cloud or the low-kappa codes and their
        # callouts, and a shaded band needs a name, not a paragraph.
        ax.annotate("rare codes", xy=(0.05, 1.005), xycoords=("data", "axes fraction"),
                    ha="center", va="bottom", fontsize=9.3,
                    color=PALETTE["ink2"], annotation_clip=False)

    # Label only the two clearest cases of the artefact, pushed to opposite
    # sides of their points: three callouts in this region overprint each other.
    worst = sorted(rows, key=lambda r: r["pabak"] - r["mean_kappa"])[-2:]
    for n, r in enumerate(sorted(worst, key=lambda r: r["mean_kappa"])):
        # The lower point is labelled ABOVE it and the higher one BELOW, so the
        # two callouts move apart rather than toward each other.
        ax.annotate(r["code"][:34], xy=(r["prevalence"], r["mean_kappa"]),
                    xytext=(14, 20 if n == 0 else -22),
                    textcoords="offset points",
                    fontsize=8.8, color=PALETTE["ink2"],
                    arrowprops=dict(arrowstyle="-", color=PALETTE["axis"],
                                    linewidth=0.7, shrinkA=0, shrinkB=3))

    ax.set_xlabel("Proportion of transcripts to which the reviewers "
                  "applied the code")
    ax.set_ylabel("Agreement")
    _despine(ax)
    has_rho = rho is not None and not math.isnan(rho)
    _headline(ax, fig, "paradox", n_rare=len(rare), n_total=len(rows),
              rho_sentence=(f"  Spearman ρ(prevalence, κ) = {rho:+.2f}."
                            if has_rho else ""),
              rho_caption=(f" Spearman's ρ = {rho:+.2f}.".replace("-", "\u2212")
                           if has_rho else ""))
    ax.legend(loc="lower right", fontsize=9.5)
    fig.savefig(path)
    plt.close(fig)
    return path


# --- F5: over- vs under-application ------------------------------------------

def fig_bias(bias, stats, path, n_docs=104):
    """Direction of error: does the model apply codes more or less than humans?

    κ is symmetric and hides this completely, yet the remedies are opposites —
    over-application means the definition is too loose, under-application too
    narrow. Signed quantity about a meaningful zero, so a diverging scale with a
    neutral midpoint, and the zero line is the strongest mark on the plot.
    """
    order = sorted(bias, key=lambda r: r["mean_diff"])
    fig, ax = plt.subplots(figsize=(11.5, 0.62 * len(order) + 3.2))

    lim = max(abs(d) for r in order for d in r["diffs"]) or 1
    rng = _Rng(11)
    for i, row in enumerate(order):
        y = i + 1
        ax.scatter(row["diffs"], [y + rng.jitter(0.14) for _ in row["diffs"]],
                   s=16, color=PALETTE["muted"], alpha=0.35,
                   linewidths=0, zorder=2)
        colour = CMAP_DIV(0.5 + 0.5 * max(-1, min(1, row["mean_diff"] / (lim * 0.6))))
        ax.scatter([row["mean_diff"]], [y], s=185, color=colour, zorder=4,
                   edgecolors=PALETTE["surface"], linewidths=2.0, marker="D")
        ax.annotate(f"{row['mean_diff']:+.1f}", xy=(1.012, y),
                    xycoords=("axes fraction", "data"), va="center", ha="left",
                    fontsize=9.5, color=PALETTE["ink2"], fontweight="bold")

    ax.axvline(0, color=PALETTE["ink"], linewidth=1.6, zorder=3)
    ax.set_yticks(range(1, len(order) + 1))
    ax.set_yticklabels([stats.display_name(r["model"]) for r in order],
                       fontsize=10.5, color=PALETTE["ink"])
    ax.set_xlabel(f"Transcripts coded by the model minus those coded by the "
                  f"reviewers (per code, of {n_docs})")
    ax.grid(axis="y", visible=False)
    ax.set_ylim(0.4, len(order) + 0.7)
    _despine(ax, keep=("bottom",))

    ax.annotate("← applied less often than by the reviewers", xy=(0.02, 0.985),
                xycoords="axes fraction", fontsize=9.5, color=PALETTE["ink2"],
                ha="left", va="top")
    ax.annotate("applied more often than by the reviewers →", xy=(0.98, 0.985),
                xycoords="axes fraction", fontsize=9.5, color=PALETTE["ink2"],
                ha="right", va="top")
    ax.annotate("mean", xy=(1.012, len(order) + 0.5),
                xycoords=("axes fraction", "data"), ha="left", fontsize=8.8,
                color=PALETTE["muted"])
    _headline(ax, fig, "bias", n_codes=len(order[0]["diffs"]) if order else 0,
              n_docs=n_docs)
    fig.savefig(path)
    plt.close(fig)
    return path


# --- F6: who agrees with whom ------------------------------------------------

def fig_agreement_matrix(pairs, names, stats, path, n_codes=None, n_docs=None):
    """Pairwise Cohen's kappa between every rater, reviewers included.

    Supports the comparison of model-model against model-reviewer agreement.
    That comparison is stated in the running text rather than on the figure:
    the matrix reports the coefficients, and any inference about shared
    interpretation of the codebook is an argument to be made in prose.
    """
    labels = [stats.display_name(n) if n != "human" else "Reviewers"
              for n in names]
    grid = [[pairs.get((a, b), float("nan")) for b in names] for a in names]

    fig, ax = plt.subplots(figsize=(9.6, 8.2))
    im = ax.imshow(grid, cmap=CMAP_SEQ, vmin=0, vmax=1)
    for i in range(len(names)):
        for j in range(len(names)):
            v = grid[i][j]
            if v is None or (isinstance(v, float) and math.isnan(v)):
                continue
            ax.annotate(f"{v:.2f}", xy=(j, i), ha="center", va="center",
                        fontsize=8.6,
                        color="#ffffff" if v > 0.55 else PALETTE["ink"])

    ax.set_xticks(range(len(names)))
    ax.set_xticklabels(labels, rotation=38, ha="right", fontsize=9.3)
    ax.set_yticks(range(len(names)))
    ax.set_yticklabels(labels, fontsize=9.3)
    for tick in (ax.get_xticklabels() + ax.get_yticklabels()):
        if tick.get_text() == "Reviewers":
            tick.set_color(PALETTE["ink"])
            tick.set_fontweight("bold")
        else:
            tick.set_color(PALETTE["ink2"])
    ax.grid(False)
    ax.tick_params(length=0)
    for spine in ax.spines.values():
        spine.set_visible(False)

    # A neutral rule, not a status colour: the reviewers' row is the reference
    # against which the models are read, not a warning.
    idx = names.index("human")
    for pos in (idx - 0.5, idx + 0.5):
        ax.axhline(pos, color=PALETTE["ink"], linewidth=1.2)
        ax.axvline(pos, color=PALETTE["ink"], linewidth=1.2)

    cbar = fig.colorbar(im, ax=ax, fraction=0.036, pad=0.02)
    cbar.set_label("Cohen's κ", fontsize=9.5, color=PALETTE["ink2"])
    cbar.outline.set_visible(False)
    cbar.ax.tick_params(length=0, labelsize=9)

    _headline(ax, fig, "agreement_matrix", n_codes=n_codes or 0, n_docs=n_docs or 0)
    fig.savefig(path)
    plt.close(fig)
    return path


# --- F7: which codes are hard for everyone -----------------------------------

def fig_code_difficulty(kf, path, top=None):
    """Per code: the mean across models and the full model-to-model range.

    Ordering by mean turns the question "which codes should the reviewers work
    on next?" into a reading off the bottom of the chart, and the bar length
    shows whether the choice of model would have rescued the code at all.
    """
    rows = []
    for code in kf.complete_codes():
        vals = kf.values_for_code(code)
        if vals:
            rows.append((code, _mean(vals), min(vals), max(vals)))
    rows.sort(key=lambda r: r[1])
    if top:
        rows = rows[:top]

    fig, ax = plt.subplots(figsize=(11.0, 0.30 * len(rows) + 3.2))
    for i, (code, mean, lo, hi) in enumerate(rows):
        # Traffic-lighting each code by whether it clears the criterion states a
        # verdict on the mark itself. That is useful on a slide and out of place
        # in print, where the reference line already carries the comparison and
        # colour ought not pre-judge the reader.
        if current_style() == PUBLICATION:
            colour = PALETTE["blue"]
        else:
            colour = PALETTE["good"] if mean > TARGET else (
                PALETTE["blue"] if mean > 0.5 else PALETTE["critical"])
        ax.plot([lo, hi], [i, i], color=colour, alpha=0.32, linewidth=3.4,
                solid_capstyle="round", zorder=2)
        ax.scatter([mean], [i], s=42, color=colour, zorder=3,
                   edgecolors=PALETTE["surface"], linewidths=1.1)

    ax.axvline(TARGET, color=target_colour(), linewidth=1.4, linestyle=(0, (5, 3)))
    ax.annotate(target_label(), xy=(TARGET, len(rows) - 0.2),
                xytext=(5, 0), textcoords="offset points", fontsize=9.5,
                color=target_colour(),
                fontweight="normal" if current_style() == PUBLICATION else "bold",
                va="top")
    ax.set_yticks(range(len(rows)))
    ax.set_yticklabels([r[0][:52] for r in rows], fontsize=8.4,
                       color=PALETTE["ink2"])
    ax.set_ylim(-0.8, len(rows) - 0.2)
    lo = min(r[2] for r in rows)
    ax.set_xlim(min(lo - 0.06, TARGET - 0.12), 1.04)
    ax.set_xlabel("Cohen's κ — dot is the mean across models, bar is the "
                  "best-to-worst range")
    ax.grid(axis="y", visible=False)
    _despine(ax, keep=("bottom",))
    _headline(ax, fig, "code_difficulty", n_codes=len(rows),
              n_models=len(kf.usable_models))
    fig.savefig(path)
    plt.close(fig)
    return path


# --- F8: did editing the definitions pay off? --------------------------------

def fig_definition_effort(rows, path, rho=None, n_models=None):
    """Mean kappa against the number of definition revisions per code.

    The figure reports the association only. The direction of causation is not
    identified by this design — revision counts were not assigned, and codes
    with poor initial agreement plausibly attracted more revision — so that
    argument is made in the running text, not asserted in the caption.
    """
    fig, ax = plt.subplots(figsize=(10.4, 6.0))
    xs = [r["versions"] for r in rows]
    ys = [r["mean_kappa"] for r in rows]
    sizes = [28 + 0.06 * r["chars"] for r in rows]
    ax.scatter(xs, ys, s=sizes, color=PALETTE["blue"], alpha=0.55,
               linewidths=0.7, edgecolors=PALETTE["surface"], zorder=3)

    ax.axhline(TARGET, color=PALETTE["axis"], linewidth=1.2, linestyle=(0, (5, 3)))
    # Anchored LEFT here: the most-edited codes sit at the right of this plot
    # and their callout labels would otherwise land on top of this one.
    ax.annotate(f"κ = {TARGET:.2f}", xy=(0.005, TARGET),
                xycoords=("axes fraction", "data"), xytext=(0, 5),
                textcoords="offset points", ha="left", va="bottom",
                color=PALETTE["ink2"], fontsize=9)
    ax.set_xlabel("Number of definition revisions")
    ax.set_ylabel("Mean Cohen's κ")
    # Revisions are a count: fractional ticks are not meaningful values.
    lo, hi = min(xs), max(xs)
    step = 1 if hi - lo <= 12 else 2 if hi - lo <= 25 else 5
    ax.set_xticks(list(range(int(lo), int(hi) + 1, step)))
    ax.set_xlim(lo - 0.8, hi + 0.8)
    _despine(ax)

    has_rho = rho is not None and not math.isnan(rho)
    _headline(ax, fig, "definition_effort", n_models=n_models or 0,
              n_total=len(rows),
              rho_prefix=(f"Spearman ρ = {rho:+.2f}.  " if has_rho else ""),
              rho_caption=(f" Spearman's ρ = {rho:+.2f}.".replace("-", "\u2212")
                           if has_rho else ""))
    fig.savefig(path)
    plt.close(fig)
    return path
