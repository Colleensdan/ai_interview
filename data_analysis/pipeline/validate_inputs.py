"""Cross-check the four inputs against each other before spending anything.

The pipeline joins transcripts, the codebook, the count matrix and the quotes
workbook on names that each file spells differently. Every one of those joins
used to fail silently: an unmatched code became "absent in every document" and
scored a meaningless kappa, an unmatched document was quietly dropped, and a
codebook whose header said "Comment" instead of "Definition" produced a clean
run that coded nothing at all.

This module makes those joins assertions instead. It reports what it finds
rather than checking against expected totals, so it stays useful when the data
changes — the numbers are the study's, not the code's.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import config
from app.quotes import build_norm, locate
from pipeline.codebook import load_codebook
from pipeline.ground_truth import load_ground_truth_table
from pipeline.interviews import load_interviews
from pipeline.quote_sheets import build_quote_index


@dataclass
class Report:
    """Findings from a validation pass. ``problems`` blocks a run; ``notes`` don't."""

    lines: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems

    def render(self) -> str:
        out = list(self.lines)
        if self.notes:
            out.append("")
            out.append("Notes:")
            out += [f"  - {n}" for n in self.notes]
        if self.problems:
            out.append("")
            out.append("PROBLEMS:")
            out += [f"  ! {p}" for p in self.problems]
        else:
            out.append("")
            out.append("All input cross-checks passed.")
        return "\n".join(out)


def _fmt(items, limit: int = 8) -> str:
    items = list(items)
    head = ", ".join(str(i) for i in items[:limit])
    return head + (f" (+{len(items) - limit} more)" if len(items) > limit else "")


def validate(check_quote_locations: bool = True) -> Report:
    r = Report()

    codes = load_codebook(config.CODEBOOK_PATH)
    code_names = [c.name for c in codes]
    table = load_ground_truth_table(
        config.GROUND_TRUTH_COUNTS_PATH, config.GROUND_TRUTH_COUNTS_SHEET)
    interviews = load_interviews(config.INTERVIEWS_DIR)

    r.lines.append(f"codebook      {config.CODEBOOK_PATH}")
    r.lines.append(f"counts        {config.GROUND_TRUTH_COUNTS_PATH}")
    r.lines.append(f"quotes        {config.GROUND_TRUTH_QUOTES_PATH}")
    r.lines.append(f"transcripts   {config.INTERVIEWS_DIR}")
    r.lines.append("")
    r.lines.append(f"{len(code_names)} codes in codebook, "
                   f"{len(table.counts)} in count matrix")
    r.lines.append(f"{len(interviews)} transcripts, "
                   f"{len(table.doc_keys)} documents in count matrix")

    # --- codes line up both ways ------------------------------------------
    missing_from_counts = [c for c in code_names if c not in table.counts]
    missing_from_codebook = [c for c in table.counts if c not in set(code_names)]
    if missing_from_counts:
        r.problems.append(
            f"{len(missing_from_counts)} codebook code(s) absent from the count "
            f"matrix — they would score kappa against an all-absent human row: "
            f"{_fmt(missing_from_counts)}")
    if missing_from_codebook:
        r.problems.append(
            f"{len(missing_from_codebook)} counted code(s) absent from the "
            f"codebook — the model is never asked about them: "
            f"{_fmt(missing_from_codebook)}")

    # --- documents line up both ways --------------------------------------
    keys = {iv.key for iv in interviews}
    gt_keys = set(table.doc_keys)
    no_transcript = sorted(gt_keys - keys)
    no_column = sorted(keys - gt_keys)
    if no_transcript:
        r.problems.append(
            f"{len(no_transcript)} counted document(s) have no transcript file: "
            f"{_fmt(no_transcript)}")
    if no_column:
        r.problems.append(
            f"{len(no_column)} transcript(s) have no column in the count matrix "
            f"and would be dropped from kappa: {_fmt(no_column)}")

    # --- quote sheets resolve to exactly one code each ---------------------
    wb = None
    try:
        import openpyxl
        wb = openpyxl.load_workbook(
            config.GROUND_TRUTH_QUOTES_PATH, data_only=True, read_only=True)
        index = build_quote_index(
            wb, code_names,
            groundedness=table.code_groundedness,
            doc_for_number=table.document_for_number,
            non_code_sheets=config.NON_CODE_SHEETS,
        )
    finally:
        if wb is not None:
            wb.close()

    n_quotes = sum(len(v) for v in index.by_code.values())
    r.lines.append(f"{len(index.sheet_to_code)} quote sheets resolved, "
                   f"{n_quotes} quotations")

    if index.unresolved_sheets:
        r.problems.append(
            f"quote sheet(s) match no code: {_fmt(index.unresolved_sheets)}")
    if index.ambiguous:
        r.problems.append(
            f"quote sheet(s) could not be told apart — their quotes would be "
            f"attributed to the wrong code: {_fmt(index.ambiguous)}")
    if index.codes_without_sheets:
        # Not fatal: the code is genuinely human-coded, there is simply nothing
        # to highlight. The app flags these rather than showing them as AI-only.
        r.notes.append(
            f"{len(index.codes_without_sheets)} code(s) are counted but have no "
            f"quote sheet, so the human panel has nothing to highlight for them: "
            f"{_fmt(index.codes_without_sheets)}")

    # --- quote counts agree with ATLAS's own groundedness ------------------
    mismatched = [
        f"{code} (sheet {len(qs)}, Gr={table.code_groundedness.get(code)})"
        for code, qs in index.by_code.items()
        if table.code_groundedness.get(code) not in (None, len(qs))
    ]
    if mismatched:
        r.problems.append(
            f"quote count disagrees with the count matrix's Gr= for "
            f"{len(mismatched)} code(s): {_fmt(mismatched)}")

    # --- every quote is findable in the document it is attributed to -------
    if check_quote_locations:
        by_key = {iv.key: iv for iv in interviews}
        norms = {k: build_norm(iv.text) for k, iv in by_key.items()}
        unattributed, unlocatable = [], []
        for code, quotations in index.by_code.items():
            for q in quotations:
                if q.doc is None:
                    unattributed.append(f"{code} [{q.quote_id}]")
                    continue
                iv = by_key.get(q.doc)
                if iv is None or not locate(iv.text, q.quote, norm=norms.get(q.doc)):
                    unlocatable.append(f"{code} [{q.quote_id}] in {q.doc}")
        r.lines.append(
            f"{n_quotes - len(unattributed) - len(unlocatable)}/{n_quotes} "
            f"quotations located in their own document")
        if unattributed:
            r.notes.append(
                f"{len(unattributed)} quotation(s) carry no usable document ID and "
                f"fall back to a text search: {_fmt(unattributed)}")
        if unlocatable:
            r.problems.append(
                f"{len(unlocatable)} quotation(s) not found in the document they "
                f"are attributed to: {_fmt(unlocatable)}")

    # --- what will actually be coded --------------------------------------
    excluded = [c for c in code_names if c in set(config.EXCLUDED_CODES)]
    unknown_exclusions = [c for c in config.EXCLUDED_CODES if c not in set(code_names)]
    scored = [c for c in code_names if c not in set(config.EXCLUDED_CODES)]
    r.lines.append("")
    r.lines.append(f"{len(scored)} code(s) will be coded and scored; "
                   f"{len(excluded)} excluded ({_fmt(excluded)})")
    if unknown_exclusions:
        r.notes.append(
            f"AICODE_EXCLUDED_CODES names {len(unknown_exclusions)} code(s) that are "
            f"not in the codebook: {_fmt(unknown_exclusions)}")
    if not scored:
        r.problems.append("every code is excluded — nothing would be coded.")

    empty = [iv.title for iv in interviews
             if sum(table.counts.get(c, {}).get(iv.key, 0) for c in code_names) == 0]
    if empty:
        r.notes.append(
            f"{len(empty)} transcript(s) carry no human codes at all (abandoned "
            f"interviews); they are all-absent on both sides and inflate "
            f"agreement slightly: {_fmt(empty)}")

    return r
