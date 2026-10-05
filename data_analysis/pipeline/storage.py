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

import config
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

-- What each run actually was. Exists so that publishing to the live app can be
-- gated on fact rather than on a naming convention: a partial run (--max-codes
-- / --max-docs) produces kappas over a truncated code or document set, and
-- pushing those to the app would show reviewers numbers that look like the real
-- thing. ``is_full`` is the flag the sync tool refuses on.
CREATE TABLE IF NOT EXISTS runs (
    run_id       TEXT PRIMARY KEY,
    label        TEXT,
    is_full      INTEGER NOT NULL,
    n_codes      INTEGER,
    n_documents  INTEGER,
    definitions  TEXT,          -- 'codebook' | 'current'
    models       TEXT,          -- comma-separated adapter names
    created_at   TEXT DEFAULT (datetime('now'))
);
"""


def init_db(db_path: str | Path) -> sqlite3.Connection:
    import db as _db  # shared WAL + busy_timeout connection helper

    conn = _db.connect(db_path)
    conn.executescript(_SCHEMA)
    conn.commit()
    return conn


# --- run metadata -----------------------------------------------------------

def record_run(conn, run_id: str, *, label: str, is_full: bool, n_codes: int,
               n_documents: int, definitions: str, models: list[str]) -> None:
    """Record what this run covered, so downstream tools need not guess.

    ``is_full`` is False whenever the run was scope-limited, which is what
    ``sync_to_live.py`` refuses to publish.
    """
    conn.execute(
        "INSERT OR REPLACE INTO runs "
        "(run_id, label, is_full, n_codes, n_documents, definitions, models) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (run_id, label, 1 if is_full else 0, n_codes, n_documents,
         definitions, ",".join(models)),
    )
    conn.commit()


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
    """Write these hits, replacing anything already stored for their codes.

    Keyed on the codes present in *hits*, so a per-code write replaces only that
    code. A code that found nothing contributes no hits and so clears nothing --
    its emptiness is recorded in the count matrix instead.
    """
    conn.executemany(
        "DELETE FROM coding_results WHERE run_id=? AND model=? AND code_name=?",
        [(run_id, model, c) for c in {h.code_name for h in hits}],
    )
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
    """Write these codes' counts for one model, replacing any already stored.

    Scoped to the codes being written, so this can be called once per code as a
    run progresses without a later call wiping earlier ones. Replacing rather
    than appending makes re-coding a single code idempotent.
    """
    conn.executemany(
        "DELETE FROM count_matrix WHERE run_id=? AND model=? AND code_name=?",
        [(run_id, model, c) for c in code_names],
    )
    conn.executemany(
        "INSERT INTO count_matrix (run_id, model, code_name, document, count) "
        "VALUES (?, ?, ?, ?, ?)",
        [(run_id, model, c, d, n) for c, d, n in _matrix_rows(matrix, code_names, documents)],
    )
    conn.commit()


def insert_majority_matrix(conn, run_id, matrix, code_names, documents) -> None:
    """Write the run's majority vote, replacing any vote already stored for it.

    Replace rather than append: a resumed run and every re-score recompute this
    table for the same run_id, and a plain INSERT silently stacks copies. Six
    copies had accumulated for the 2026-09-06 run before this was caught.
    """
    conn.execute("DELETE FROM majority_vote_matrix WHERE run_id=?", (run_id,))
    conn.executemany(
        "INSERT INTO majority_vote_matrix (run_id, code_name, document, count) "
        "VALUES (?, ?, ?, ?)",
        [(run_id, c, d, n) for c, d, n in _matrix_rows(matrix, code_names, documents)],
    )
    conn.commit()


def insert_ground_truth_matrix(conn, matrix, code_names, documents) -> None:
    """Replace the stored human ground truth.

    Unlike the model tables this carries no run_id: it is a snapshot of the
    input workbook, not a record of one run. Appending would duplicate every
    row on each re-run (5,824 of them for the current data) and inflate the
    CSV mirror, so the previous snapshot is cleared first.
    """
    conn.execute(
        "DELETE FROM ground_truth_matrix WHERE source='human_ground_truth'")
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
        target = config.KAPPA_TARGET
        w.writerow(
            ["Model", "Code", "Cohen's kappa", "n_documents",
             "n_human_present", "n_llm_present", f"meets_target_>{target:g}"]
        )
        for model, r in rows:
            meets = (not _isnan(r.kappa)) and r.kappa > target
            kappa_out = "" if _isnan(r.kappa) else round(r.kappa, 4)
            w.writerow([model, r.code_name, kappa_out, r.n_documents,
                        r.n_human_present, r.n_llm_present, meets])


def insert_kappa(conn, run_id, rows: list[tuple[str, KappaResult]]) -> None:
    """Write kappa, replacing only the (model, code) pairs being written.

    Appending left a resumed run holding two majority votes -- the original and
    one computed over just the models the resume coded -- and the summary
    averaged them into a figure matching neither. Replacing the *whole run* was
    the wrong correction: an invocation that scores a subset of codes then
    deletes every other code's kappa, which is how scoring the five procedural
    codes wiped 49 codes of results for seven models. Scope the replace to what
    is actually being rewritten, as the count-matrix write already does.
    """
    conn.executemany(
        "DELETE FROM kappa WHERE run_id=? AND model=? AND code_name=?",
        [(run_id, model, r.code_name) for model, r in rows],
    )
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


def load_count_matrices(conn, run_id: str) -> dict[str, dict]:
    """Every model's count matrix for a run: {model: {code: {document: count}}}.

    Lets a resumed run take its majority vote over all the models the run holds,
    not just the ones the current invocation coded.
    """
    out: dict[str, dict] = {}
    for model, code, doc, n in conn.execute(
            "SELECT model, code_name, document, count FROM count_matrix "
            "WHERE run_id=?", (run_id,)):
        out.setdefault(model, {}).setdefault(code, {})[doc] = n
    return out


def codes_already_coded(conn, run_id: str, model: str) -> set[str]:
    """Codes this model has already stored for this run.

    The count matrix is dense -- a code that found nothing still has a row per
    document -- so a code appears here whether or not it produced occurrences.
    That is what lets an interrupted run resume at the code it stopped on
    instead of paying for the whole model again.
    """
    return {r[0] for r in conn.execute(
        "SELECT DISTINCT code_name FROM count_matrix WHERE run_id=? AND model=?",
        (run_id, model))}


def load_coding_results(conn, run_id: str, model: str) -> list[CodeHit]:
    """Every occurrence stored for one model in one run, in insertion order."""
    return [CodeHit(document_title=r[0], code_name=r[1], quote=r[2], reason=r[3])
            for r in conn.execute(
                "SELECT document_title, code_name, quote, reason FROM coding_results "
                "WHERE run_id=? AND model=? ORDER BY id", (run_id, model))]
