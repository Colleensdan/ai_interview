"""CSV and SQLite persistence.

CSVs are the human-facing deliverables; SQLite is the single unified store
across all models. Matrices are written wide to CSV (rows = codes, columns =
documents) and tidy/long to the DB (one row per code x document) with the
required ``model`` column and an autoincrement primary key.
"""

from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

from .agreement import KappaResult
from .matrices import Matrix
from models.base import CodeHit

_SCHEMA = """
CREATE TABLE IF NOT EXISTS coding_results (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        TEXT NOT NULL,
    model         TEXT NOT NULL,
    document_title TEXT NOT NULL,
    code_name     TEXT NOT NULL,
    quote         TEXT,
    reason        TEXT,
    created_at    TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS count_matrix (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id    TEXT NOT NULL,
    model     TEXT NOT NULL,
    code_name TEXT NOT NULL,
    document  TEXT NOT NULL,
    count     INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS majority_vote_matrix (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id    TEXT NOT NULL,
    code_name TEXT NOT NULL,
    document  TEXT NOT NULL,
    count     INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS ground_truth_matrix (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    source    TEXT NOT NULL DEFAULT 'human_ground_truth',
    code_name TEXT NOT NULL,
    document  TEXT NOT NULL,
    count     INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS kappa (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id        TEXT NOT NULL,
    model         TEXT NOT NULL,
    code_name     TEXT NOT NULL,
    kappa         REAL,
    n_documents   INTEGER,
    n_human_present INTEGER,
    n_llm_present INTEGER
);
"""


def init_db(db_path: str | Path) -> sqlite3.Connection:
    import db as _db  # shared WAL + busy_timeout connection helper

    conn = _db.connect(db_path)
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


# --- coding results ---------------------------------------------------------

_RESULT_HEADER = ["Document title", "Code name", "Quote", "Reason"]


def write_results_csv(path: str | Path, hits: list[CodeHit]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(_RESULT_HEADER)
        for h in hits:
            w.writerow([h.document_title, h.code_name, h.quote, h.reason])


def insert_coding_results(
    conn: sqlite3.Connection, run_id: str, model: str, hits: list[CodeHit]
) -> None:
    conn.executemany(
        "INSERT INTO coding_results "
        "(run_id, model, document_title, code_name, quote, reason) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        [(run_id, model, h.document_title, h.code_name, h.quote, h.reason) for h in hits],
    )
    conn.commit()


# --- matrices ---------------------------------------------------------------

def write_matrix_csv(
    path: str | Path,
    matrix: Matrix,
    code_names: list[str],
    documents: list[str],
) -> None:
    """Wide matrix: first column = code names, remaining columns = documents."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Code"] + documents)
        for code in code_names:
            row = matrix.get(code, {})
            w.writerow([code] + [row.get(d, 0) for d in documents])


def _matrix_rows(matrix: Matrix, code_names, documents):
    for code in code_names:
        row = matrix.get(code, {})
        for doc in documents:
            yield code, doc, row.get(doc, 0)


def insert_count_matrix(
    conn, run_id, model, matrix, code_names, documents
) -> None:
    conn.executemany(
        "INSERT INTO count_matrix (run_id, model, code_name, document, count) "
        "VALUES (?, ?, ?, ?, ?)",
        [(run_id, model, c, d, n) for c, d, n in _matrix_rows(matrix, code_names, documents)],
    )
    conn.commit()


def insert_majority_matrix(conn, run_id, matrix, code_names, documents) -> None:
    conn.executemany(
        "INSERT INTO majority_vote_matrix (run_id, code_name, document, count) "
        "VALUES (?, ?, ?, ?)",
        [(run_id, c, d, n) for c, d, n in _matrix_rows(matrix, code_names, documents)],
    )
    conn.commit()


def insert_ground_truth_matrix(conn, matrix, code_names, documents) -> None:
    conn.executemany(
        "INSERT INTO ground_truth_matrix (code_name, document, count) "
        "VALUES (?, ?, ?)",
        [(c, d, n) for c, d, n in _matrix_rows(matrix, code_names, documents)],
    )
    conn.commit()


# --- kappa ------------------------------------------------------------------

def write_kappa_csv(path: str | Path, rows: list[tuple[str, KappaResult]]) -> None:
    """rows = list of (model, KappaResult)."""
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(
            ["Model", "Code", "Cohen's kappa", "n_documents",
             "n_human_present", "n_llm_present", "meets_target_>0.80"]
        )
        for model, r in rows:
            meets = (not _isnan(r.kappa)) and r.kappa > 0.80
            kappa_out = "" if _isnan(r.kappa) else round(r.kappa, 4)
            w.writerow([model, r.code_name, kappa_out, r.n_documents,
                        r.n_human_present, r.n_llm_present, meets])


def insert_kappa(conn, run_id, rows: list[tuple[str, KappaResult]]) -> None:
    conn.executemany(
        "INSERT INTO kappa "
        "(run_id, model, code_name, kappa, n_documents, n_human_present, n_llm_present) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        [
            (run_id, model, r.code_name,
             None if _isnan(r.kappa) else r.kappa,
             r.n_documents, r.n_human_present, r.n_llm_present)
            for model, r in rows
        ],
    )
    conn.commit()


def _isnan(x) -> bool:
    return isinstance(x, float) and x != x
