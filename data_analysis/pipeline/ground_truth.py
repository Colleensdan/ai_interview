"""Read the human ground-truth code x document count matrix (CountData.xlsx).

This is the ATLAS.ti CodeDocumentTable: row 1 holds document headers like
``27502M\\nGr=53``; each subsequent row is ``● <code>\\nGr=NN`` followed by the
integer count of that code in each document. We return counts keyed by the
canonical document key (leading digits) so they line up with the interview
filenames.
"""

from __future__ import annotations

import re
from pathlib import Path

import openpyxl


def _clean_code(label) -> str:
    """`'● A: alienating\\nGr=26'` -> `'A: alienating'`."""
    if label is None:
        return ""
    text = str(label).split("\n", 1)[0]
    return text.lstrip("●").strip()


def _doc_header_key(label) -> str:
    """`'27502M\\nGr=53'` -> `'27502'` (leading digits of the first line)."""
    if label is None:
        return ""
    first = str(label).split("\n", 1)[0].strip()
    m = re.match(r"(\d+)", first)
    return m.group(1) if m else first


def load_ground_truth_counts(
    path: str | Path, sheet: str
) -> tuple[dict[str, dict[str, int]], list[str]]:
    """Return (counts, doc_keys).

    ``counts[code_name][doc_key]`` = human count.
    ``doc_keys`` is the ordered list of document keys (matrix columns).
    """
    wb = openpyxl.load_workbook(path, data_only=True, read_only=True)
    ws = wb[sheet]
    rows = list(ws.iter_rows(values_only=True))
    wb.close()
    if not rows:
        return {}, []

    header = rows[0]
    # Column 0 is the code label; columns 1.. are documents.
    doc_keys = [_doc_header_key(h) for h in header[1:]]

    counts: dict[str, dict[str, int]] = {}
    for row in rows[1:]:
        if not row or not row[0]:
            continue
        code = _clean_code(row[0])
        if not code:
            continue
        per_doc: dict[str, int] = {}
        for key, value in zip(doc_keys, row[1:]):
            try:
                per_doc[key] = int(value) if value is not None else 0
            except (TypeError, ValueError):
                per_doc[key] = 0
        counts[code] = per_doc
    return counts, doc_keys
