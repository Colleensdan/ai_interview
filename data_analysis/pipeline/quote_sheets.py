"""Resolve the ground-truth quotes workbook: one sheet per code, with quotes
attributed to the document they came from.

Two problems make this harder than it looks, and both used to be handled by a
`difflib` guess that silently produced wrong answers.

**Sheet names are lossy.** Excel caps sheet names at 31 characters and forbids
``\\ / : * ? [ ]``, so ATLAS.ti mangles long code names and disambiguates the
resulting collisions with a trailing digit. In the real data three distinct
codes -- ``pro-environmental social norms increased`` / ``... reduced`` /
``... unchanged/continues`` -- become the sheets ``pro-environmental social
norms``, ``...norm1`` and ``...norm2``, **not in code order**. Nothing in the
name says which is which, and fuzzy matching collapses all three onto one code.
They are told apart here by ``Gr=``, ATLAS.ti's own count of quotations per
code, matched against the number of rows in each candidate sheet.

**Quotes carry no document name.** The ID column holds ``<document number>:
<quotation number>``, and the document numbering is the column order of the
count matrix. Without it, a quote can only be found by searching every
transcript -- and 117 of the 785 real quotes are short enough ("Angry", "4") to
appear in more than one. With it, attribution is exact.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pipeline.names import norm_for_match

# Trailing digits Excel/ATLAS appends to disambiguate truncated sheet names.
_TRAILING_DIGITS = re.compile(r"\d+$")


@dataclass(frozen=True)
class Quotation:
    code: str
    quote: str
    doc: str | None      # document key, or None if the ID could not be resolved
    quote_id: str        # the raw "3:12" identifier, for traceability


@dataclass
class QuoteIndex:
    """Resolved quotes plus everything that did not line up."""

    by_code: dict[str, list[Quotation]] = field(default_factory=dict)
    sheet_to_code: dict[str, str] = field(default_factory=dict)
    unresolved_sheets: list[str] = field(default_factory=list)
    codes_without_sheets: list[str] = field(default_factory=list)
    ambiguous: list[str] = field(default_factory=list)

    def quotes_for(self, code: str) -> list[Quotation]:
        return self.by_code.get(code, [])


def _sheet_rows(ws) -> list[tuple[str, str]]:
    """Return [(id, quote)] for a code sheet, skipping the header row."""
    out: list[tuple[str, str]] = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        if len(row) > 1 and row[1] is not None and str(row[1]).strip():
            out.append((str(row[0]).strip() if row[0] is not None else "", str(row[1])))
    return out


def _candidates(sheet: str, codes: list[str]) -> list[str]:
    """Codes a sheet name could plausibly refer to, most-specific first."""
    norm_sheet = norm_for_match(sheet)
    norm_codes = {c: norm_for_match(c) for c in codes}

    exact = [c for c, n in norm_codes.items() if n == norm_sheet]
    if exact:
        return exact
    prefixed = [c for c, n in norm_codes.items() if n.startswith(norm_sheet)]
    if prefixed:
        return prefixed
    # Truncated-and-numbered ("...norm1"): drop the disambiguation digits.
    stem = _TRAILING_DIGITS.sub("", norm_sheet)
    if stem and stem != norm_sheet:
        return [c for c, n in norm_codes.items() if n.startswith(stem)]
    return []


def _disambiguate(
    candidates: list[str], n_rows: int, groundedness: dict[str, int], taken: set[str]
) -> str | None:
    """Pick the candidate whose ATLAS groundedness equals this sheet's row count."""
    free = [c for c in candidates if c not in taken]
    matches = [c for c in free if groundedness.get(c) == n_rows]
    if len(matches) == 1:
        return matches[0]
    if len(free) == 1:
        return free[0]
    return None


def build_quote_index(
    wb,
    codes: list[str],
    *,
    groundedness: dict[str, int] | None = None,
    doc_for_number=None,
    non_code_sheets: set[str] | None = None,
) -> QuoteIndex:
    """Map every quote sheet to exactly one code, and every quote to a document.

    ``groundedness`` is ``{code: Gr}`` from the count matrix; ``doc_for_number``
    maps an ATLAS document number to a document key (see
    :meth:`pipeline.ground_truth.GroundTruthTable.document_for_number`).
    Anything that cannot be resolved is recorded on the returned index rather
    than guessed at.
    """
    groundedness = groundedness or {}
    skip = {s.lower() for s in (non_code_sheets or set())}
    index = QuoteIndex()
    taken: set[str] = set()

    # Read each sheet once, then resolve names against the codebook.
    rows_by_sheet = {
        sheet: _sheet_rows(wb[sheet])
        for sheet in wb.sheetnames if sheet.lower() not in skip
    }

    # Sheets whose name resolves to a single code are assigned first, so a
    # collision group cannot steal a code that another sheet matches outright.
    pending: list[tuple[str, list[str]]] = []
    claimed_by: dict[str, str] = {}
    for sheet in rows_by_sheet:
        cands = _candidates(sheet, codes)
        if len(cands) == 1:
            code = cands[0]
            if code in claimed_by:
                # Two sheets resolving to one code would silently merge their
                # quotes; that is a data problem, not something to paper over.
                index.ambiguous.append(
                    f"{sheet} and {claimed_by[code]} both resolve to {code!r}")
                continue
            claimed_by[code] = sheet
            index.sheet_to_code[sheet] = code
            taken.add(code)
        else:
            pending.append((sheet, cands))

    for sheet, cands in pending:
        if not cands:
            index.unresolved_sheets.append(sheet)
            continue
        chosen = _disambiguate(cands, len(rows_by_sheet[sheet]), groundedness, taken)
        if chosen is None:
            index.ambiguous.append(
                f"{sheet} (rows={len(rows_by_sheet[sheet])}, "
                f"candidates={', '.join(sorted(cands))})"
            )
            continue
        index.sheet_to_code[sheet] = chosen
        taken.add(chosen)

    for sheet, code in index.sheet_to_code.items():
        for qid, quote in rows_by_sheet[sheet]:
            index.by_code.setdefault(code, []).append(
                Quotation(
                    code=code,
                    quote=quote,
                    doc=_doc_for(qid, doc_for_number),
                    quote_id=qid,
                )
            )

    index.codes_without_sheets = [
        c for c in codes
        if c not in index.by_code and groundedness.get(c, 0) > 0
    ]
    return index


def _doc_for(quote_id: str, doc_for_number) -> str | None:
    if not quote_id or doc_for_number is None:
        return None
    head = quote_id.split(":", 1)[0].strip()
    if not head.isdigit():
        return None
    return doc_for_number(int(head))
