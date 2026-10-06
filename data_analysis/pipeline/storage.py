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

-- Every model reply, verbatim, one row per request attempt -- accepted or not.
-- The count matrix must be re-derivable from these rows alone (verify_run.py
-- checks that it is), so a published kappa can be audited down to the text
-- the model returned. ``final`` marks the accepted replies that produced the
-- stored results for their code; earlier attempts stay, with final=0, so every
-- request made is accounted for. Local only: sync_to_live.py does not copy it.
CREATE TABLE IF NOT EXISTS raw_responses (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT NOT NULL,
    invocation_id   TEXT NOT NULL,
    model           TEXT NOT NULL,
    code_name       TEXT NOT NULL,
    code_attempt    TEXT NOT NULL,   -- one id per time a code is coded
    batch_label     TEXT NOT NULL,   -- "2", or "2.0"/"2.1" after a split
    document_titles TEXT NOT NULL,   -- JSON list, in prompt order
    prompt_sha256   TEXT NOT NULL,   -- of system prompt + user prompt
    attempt         INTEGER NOT NULL,
    outcome         TEXT NOT NULL,   -- accepted|malformed|truncated|error
    detail          TEXT,
    finish_reason   TEXT,
    params          TEXT,            -- JSON: request parameters actually sent
    prompt_tokens   INTEGER,
    completion_tokens INTEGER,
    content         TEXT,
    final           INTEGER NOT NULL DEFAULT 0,
    created_at      TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS raw_responses_key
    ON raw_responses (run_id, model, code_name);

-- One row per run_pipeline invocation (a resume is a new invocation of the
-- same run). Records exactly what produced the results: code version, input
-- fingerprints, and the settings that change model behaviour or parsing.
CREATE TABLE IF NOT EXISTS run_invocations (
    invocation_id TEXT PRIMARY KEY,
    run_id        TEXT NOT NULL,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    git_commit    TEXT,
    git_dirty     INTEGER,
    models        TEXT,
    argv          TEXT,
    provenance    TEXT NOT NULL      -- JSON, see run_pipeline.provenance()
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
    conn: sqlite3.Connection, run_id: str, model: str, hits: list[CodeHit],
    code_names=None,
) -> None:
    """Write these hits, replacing anything already stored for their codes.

    Pass *code_names* to say which codes are being rewritten. Without it the
    scope is inferred from the codes present in *hits* -- which means a code
    re-coded to zero hits would keep its OLD quotes while its count matrix said
    zero. Every per-code writer must pass it.
    """
    scope = set(code_names) if code_names is not None else {h.code_name for h in hits}
    conn.executemany(
        "DELETE FROM coding_results WHERE run_id=? AND model=? AND code_name=?",
        [(run_id, model, c) for c in scope],
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


# --- provenance --------------------------------------------------------------

def record_invocation(conn, *, invocation_id: str, run_id: str, started_at: str,
                      git_commit: str | None, git_dirty: bool | None,
                      models: list[str], argv: list[str], provenance: dict) -> None:
    import json
    conn.execute(
        "INSERT INTO run_invocations (invocation_id, run_id, started_at, "
        "git_commit, git_dirty, models, argv, provenance) VALUES (?,?,?,?,?,?,?,?)",
        (invocation_id, run_id, started_at, git_commit,
         None if git_dirty is None else int(git_dirty), ",".join(models),
         json.dumps(argv), json.dumps(provenance, sort_keys=True)))
    conn.commit()


def finish_invocation(conn, invocation_id: str, finished_at: str) -> None:
    conn.execute("UPDATE run_invocations SET finished_at=? WHERE invocation_id=?",
                 (finished_at, invocation_id))
    conn.commit()


def invocations(conn, run_id: str) -> list[dict]:
    """Every recorded invocation of a run, in the order they were started."""
    import json
    rows = conn.execute(
        "SELECT invocation_id, started_at, finished_at, git_commit, git_dirty, "
        "models, argv, provenance FROM run_invocations WHERE run_id=? "
        # Insertion order. started_at has one-second resolution, so ordering
        # by it (with the random id as tie-break) could return two quick
        # invocations in the wrong order -- and "first" is what a resume must
        # match, "last" is whose scoring is stored.
        "ORDER BY rowid", (run_id,)).fetchall()
    keys = ("invocation_id", "started_at", "finished_at", "git_commit",
            "git_dirty", "models", "argv", "provenance")
    out = []
    for r in rows:
        d = dict(zip(keys, r))
        d["provenance"] = json.loads(d["provenance"])
        out.append(d)
    return out


def insert_raw_response(conn, *, run_id: str, invocation_id: str, model: str,
                        code_name: str, code_attempt: str, batch_label: str,
                        document_titles, prompt_sha256: str, attempt: int,
                        outcome: str, detail: str = "", reply=None) -> None:
    import json
    conn.execute(
        "INSERT INTO raw_responses (run_id, invocation_id, model, code_name, "
        "code_attempt, batch_label, document_titles, prompt_sha256, attempt, "
        "outcome, detail, finish_reason, params, prompt_tokens, "
        "completion_tokens, content) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (run_id, invocation_id, model, code_name, code_attempt, batch_label,
         json.dumps(list(document_titles)), prompt_sha256, attempt, outcome,
         detail or None,
         getattr(reply, "finish_reason", None),
         json.dumps(getattr(reply, "params", None) or {}, sort_keys=True)
         if reply is not None else None,
         getattr(reply, "prompt_tokens", None),
         getattr(reply, "completion_tokens", None),
         getattr(reply, "content", None)))
    conn.commit()


def persist_code(conn, run_id: str, model: str, code_name: str, matrix,
                 documents, hits: list[CodeHit], code_attempt: str | None) -> None:
    """Store one completed code atomically: counts, quotes, and replies' status.

    One transaction, so a crash can never leave a code whose count matrix is
    written (which makes resume treat it as done) but whose quotes are not, or
    whose replies are not marked as the ones that produced it. Re-coding a code
    replaces all three, including clearing old quotes when it now has none.
    """
    with conn:
        conn.execute("DELETE FROM count_matrix WHERE run_id=? AND model=? AND code_name=?",
                     (run_id, model, code_name))
        conn.executemany(
            "INSERT INTO count_matrix (run_id, model, code_name, document, count) "
            "VALUES (?, ?, ?, ?, ?)",
            [(run_id, model, c, d, n)
             for c, d, n in _matrix_rows(matrix, [code_name], documents)])
        conn.execute("DELETE FROM coding_results WHERE run_id=? AND model=? AND code_name=?",
                     (run_id, model, code_name))
        conn.executemany(
            "INSERT INTO coding_results "
            "(run_id, model, document_title, code_name, quote, reason) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [(run_id, model, h.document_title, h.code_name, h.quote, h.reason)
             for h in hits])
        conn.execute("UPDATE raw_responses SET final=0 "
                     "WHERE run_id=? AND model=? AND code_name=?",
                     (run_id, model, code_name))
        if code_attempt is not None:
            conn.execute("UPDATE raw_responses SET final=1 WHERE run_id=? AND "
                         "model=? AND code_name=? AND code_attempt=? AND "
                         "outcome='accepted'",
                         (run_id, model, code_name, code_attempt))
