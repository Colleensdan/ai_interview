"""Code x interview workbook: every coding the app shows, as one Excel file.

One sheet per source -- the human ground truth, the majority vote, and each
model -- with interviews as rows and codes as columns, so any AI coding can be
checked by hand against the human coding of the same interview.

Each (model, code) column is read from the run its displayed kappa came from:
the newest kappa row for that model and code, exactly as the app picks it
(``app.data_access.DataStore.latest_runs``). Reading the newest run *with
counts* instead would pair an older run's codings with a newer run's kappa
whenever the newer run found the code nowhere -- the bug behind the
"pro-environmental social norms increased" report.

Cells hold counts (quotations per interview) for the humans and the models, and
the vote as the run stored it: 1/0 under presence majority, a count for runs
scored under the older count-plurality rule. A blank cell means that source never
coded that code; it is not the same as 0.
"""

from __future__ import annotations

import io
import sqlite3
from pathlib import Path

import openpyxl
from openpyxl.styles import Font

import config
from pipeline.agreement import match_codes

MAJORITY = "majority_vote"
WORKBOOK_NAME = "code_interview_matrix.xlsx"
HUMAN_SHEET = "Human ground truth"
VOTE_SHEET = "Majority vote"


def latest_kappa_runs(conn: sqlite3.Connection) -> dict[tuple[str, str], tuple[str, float | None]]:
    """``(model, code) -> (run_id, kappa)`` of the newest kappa row, excluded
    codes left out (see ``DataStore._latest_kappa_rows`` for why)."""
    excluded = tuple(config.EXCLUDED_CODES)
    sql = ("SELECT model, code_name, run_id, kappa FROM kappa k WHERE id=("
           "  SELECT MAX(id) FROM kappa WHERE model=k.model AND code_name=k.code_name)")
    if excluded:
        sql += f" AND code_name NOT IN ({','.join('?' * len(excluded))})"
    rows = _rows(conn, sql, excluded)
    return {(r[0], r[1]): (r[2], r[3]) for r in rows}


def _rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list:
    try:
        return conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        return []  # table not created yet: nothing of that source to export


def interview_id(document: str) -> str:
    """The interview ID a reviewer looks up: the file name without ``.txt``.

    The ground truth names documents ``R_xxx`` and the models ``R_xxx.txt``.
    """
    return Path(document).stem if document.endswith(".txt") else document


def _sheet_title(name: str, taken: set[str]) -> str:
    # Excel: at most 31 characters, none of []:*?/\ , unique per workbook.
    clean = "".join("_" if ch in "[]:*?/\\" else ch for ch in name)[:31]
    title, n = clean, 2
    while title in taken:
        suffix = f" ({n})"
        title, n = clean[:31 - len(suffix)] + suffix, n + 1
    taken.add(title)
    return title


def build_matrices(conn: sqlite3.Connection, code_names: list[str]):
    """Return ``(documents, sheets, notes)``.

    ``sheets`` is an ordered list of ``(source, {code: {interview_id: count}})``;
    ``notes`` lists ``(source, code, run_id, kappa, ground-truth name)``.
    """
    runs = latest_kappa_runs(conn)
    excluded = set(config.EXCLUDED_CODES)
    codes = [c for c in code_names if c not in excluded]

    gt: dict[str, dict[str, int]] = {}
    for code, doc, count in _rows(
            conn, "SELECT code_name, document, count FROM ground_truth_matrix"):
        gt.setdefault(code, {})[interview_id(doc)] = count
    gt_names = match_codes(codes, list(gt), aliases=config.GT_CODE_ALIASES)

    documents = {d for col in gt.values() for d in col}
    sheets: list[tuple[str, dict]] = [
        (HUMAN_SHEET, {c: gt.get(gt_names.get(c) or "", {}) for c in codes if gt_names.get(c)}),
    ]
    notes = [(HUMAN_SHEET, c, "", None, gt_names.get(c) or "(no ground-truth row)")
             for c in codes]

    models = sorted({m for m, _ in runs if m != MAJORITY})
    for source in ([MAJORITY] if any(m == MAJORITY for m, _ in runs) else []) + models:
        table = "majority_vote_matrix" if source == MAJORITY else "count_matrix"
        matrix: dict[str, dict[str, int]] = {}
        for code in codes:
            if (source, code) not in runs:
                continue
            run_id, kappa = runs[(source, code)]
            if source == MAJORITY:
                rows = _rows(conn,
                    f"SELECT document, count FROM {table} WHERE run_id=? AND code_name=?",
                    (run_id, code))
            else:
                rows = _rows(conn,
                    f"SELECT document, count FROM {table} "
                    "WHERE run_id=? AND model=? AND code_name=?", (run_id, source, code))
            col = {interview_id(d): n for d, n in rows}
            documents |= set(col)
            matrix[code] = col
            notes.append((source, code, run_id, kappa, ""))
        sheets.append((VOTE_SHEET if source == MAJORITY else source, matrix))
    return sorted(documents), sheets, notes


def code_names_from_db(conn: sqlite3.Connection) -> list[str]:
    """Codes in codebook order (the order the definitions were seeded in),
    followed by any code that has a kappa row but no definition row."""
    names: list[str] = []
    for table, sql in (
            ("code_definitions",
             "SELECT code_name FROM code_definitions GROUP BY code_name ORDER BY MIN(rowid)"),
            ("kappa", "SELECT code_name FROM kappa GROUP BY code_name ORDER BY MIN(rowid)")):
        try:
            rows = conn.execute(sql).fetchall()
        except sqlite3.OperationalError:
            continue  # table not created yet
        names += [r[0] for r in rows if r[0] not in names]
    return names


def workbook_bytes(conn: sqlite3.Connection, code_names: list[str] | None = None) -> bytes:
    if code_names is None:
        code_names = code_names_from_db(conn)
    documents, sheets, notes = build_matrices(conn, code_names)
    codes = [c for c in code_names if c not in set(config.EXCLUDED_CODES)]
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    taken: set[str] = set()
    bold = Font(bold=True)

    for source, matrix in sheets:
        ws = wb.create_sheet(_sheet_title(source, taken))
        ws.append(["Interview ID"] + codes)
        for cell in ws[1]:
            cell.font = bold
        for doc in documents:
            ws.append([doc] + [matrix[c].get(doc) if c in matrix else None
                               for c in codes])
        ws.freeze_panes = "B2"
        ws.column_dimensions["A"].width = 22

    ws = wb.create_sheet(_sheet_title("Notes", taken))
    ws.append(["Sheet", "Code", "Run", "Cohen's kappa", "Ground-truth code"])
    for cell in ws[1]:
        cell.font = bold
    for source, code, run_id, kappa, gt_name in notes:
        ws.append([source, code, run_id,
                   None if kappa is None or kappa != kappa else round(kappa, 4), gt_name])
    ws.append([])
    ws.append(["Cells: quotations per interview (humans, models). Majority "
               "vote: 1/0 under the presence-majority rule; runs scored before "
               "2026-10-06 stored the count-plurality vote, a count. Blank = that "
               "source never coded that code or interview. Each column comes from "
               "the run its kappa on the site came from (see Run)."])
    excluded = sorted(set(config.EXCLUDED_CODES))
    if excluded:
        ws.append([f"Excluded from coding and kappa: {', '.join(excluded)}"])

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
