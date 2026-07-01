"""Read the codebook (Code / Freq / Definition across dimension sheets)."""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

import openpyxl


@dataclass(frozen=True)
class Code:
    name: str
    definition: str
    dimension: str  # source sheet name (Affective / Behavioural / Cognitive)


def _norm(s) -> str:
    return str(s).strip() if s is not None else ""


def load_codebook(path: str | Path) -> list[Code]:
    """Return every (code, definition) pair, in sheet then row order.

    Expects a header row with columns Code, Freq, Definition (Freq is ignored).
    Robust to extra/blank rows and to the columns being in any order.
    """
    return _load_codebook_wb(openpyxl.load_workbook(path, data_only=True, read_only=True))


def load_codebook_bytes(data: bytes) -> list[Code]:
    """Same as :func:`load_codebook` but from in-memory bytes (RAM-only mode)."""
    return _load_codebook_wb(
        openpyxl.load_workbook(io.BytesIO(data), data_only=True, read_only=True))


def _load_codebook_wb(wb) -> list[Code]:
    codes: list[Code] = []
    for sheet in wb.sheetnames:
        ws = wb[sheet]
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            continue
        header = [_norm(c).lower() for c in rows[0]]
        try:
            code_col = header.index("code")
            def_col = header.index("definition")
        except ValueError:
            # Not a code sheet (no recognisable header); skip.
            continue
        for row in rows[1:]:
            if row is None:
                continue
            name = _norm(row[code_col]) if code_col < len(row) else ""
            definition = _norm(row[def_col]) if def_col < len(row) else ""
            if name:
                codes.append(
                    Code(name=name, definition=definition, dimension=sheet)
                )
    wb.close()
    return codes
