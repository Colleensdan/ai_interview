"""Background analysis jobs with progress, ETA and a live "finds" feed.

The Re-Analyse flow makes many sequential LLM calls (minutes), so it runs in a
daemon thread, NOT inside the HTTP request (Render would time out). Job state and
every positive hit are persisted to SQLite, so the loading screen's progress /
ETA / live feed survive a page refresh mid-run.

Single web worker + SQLite-on-disk (see deploy notes) keeps this consistent;
a dyno restart would kill an in-flight job, which is acceptable at this scale.
"""

from __future__ import annotations

import json
import math
import threading
import time
import uuid
from collections import deque

import config
import db as _db
from models import available_adapters
from models.base import CodingRequest
from pipeline import storage
from pipeline.agreement import match_codes, per_code_kappa
from pipeline.ground_truth import load_ground_truth_counts
from pipeline.interviews import load_interviews, merge_documents, select_sample
from pipeline.matrices import build_count_matrix, majority_vote
from .definitions import DefinitionStore

MAJORITY = "majority_vote"
FEED_LIMIT = 5  # most-recent hits returned to the UI

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    type        TEXT NOT NULL,
    status      TEXT NOT NULL,          -- running | done | error
    total       INTEGER NOT NULL DEFAULT 0,
    done        INTEGER NOT NULL DEFAULT 0,
    message     TEXT,
    eta_seconds REAL,
    error       TEXT,
    results_json TEXT,
    scope       TEXT,
    models_json TEXT,
    started_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS job_hits (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id    TEXT NOT NULL,
    code_name TEXT NOT NULL,
    quote     TEXT NOT NULL,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_job_hits_job ON job_hits(job_id, id);
"""


def _ensure_tables() -> None:
    with _db.connect() as c:
        c.executescript(_SCHEMA)


def _clean(k):
    return None if (isinstance(k, float) and math.isnan(k)) else k


# --- job row helpers --------------------------------------------------------

def _create_job(job_id: str, jtype: str, total: int, scope: str) -> None:
    now = time.time()
    with _db.connect() as c:
        c.execute(
            "INSERT INTO jobs (id, type, status, total, done, message, scope, "
            "started_at, updated_at) VALUES (?,?,?,?,0,?,?,?,?)",
            (job_id, jtype, "running", total, "Starting…", scope, now, now),
        )


def _update_job(job_id: str, **fields) -> None:
    if not fields:
        return
    fields["updated_at"] = time.time()
    cols = ", ".join(f"{k}=?" for k in fields)
    with _db.connect() as c:
        c.execute(f"UPDATE jobs SET {cols} WHERE id=?", (*fields.values(), job_id))


def _add_hit(job_id: str, code: str, quote: str) -> None:
    with _db.connect() as c:
        c.execute(
            "INSERT INTO job_hits (job_id, code_name, quote, created_at) VALUES (?,?,?,?)",
            (job_id, code, quote[:400], time.time()),
        )


def status(job_id: str) -> dict | None:
    _ensure_tables()
    with _db.connect(row_factory=True) as c:
        row = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None:
            return None
        hits = c.execute(
            "SELECT code_name, quote FROM job_hits WHERE job_id=? ORDER BY id DESC LIMIT ?",
            (job_id, FEED_LIMIT),
        ).fetchall()
    total, done = row["total"], row["done"]
    percent = round(100 * done / total) if total else 0
    return {
        "id": row["id"],
        "status": row["status"],
        "total": total,
        "done": done,
        "percent": percent,
        "eta_seconds": row["eta_seconds"],
        "message": row["message"],
        "error": row["error"],
        "results": json.loads(row["results_json"]) if row["results_json"] else [],
        "models": json.loads(row["models_json"]) if row["models_json"] else [],
        "scope": row["scope"],
        "feed": [{"code": h["code_name"], "quote": h["quote"]} for h in hits],
    }


# --- runner -----------------------------------------------------------------

def start_reanalyze(store: DefinitionStore, codes: list[str], scope: str) -> str:
    _ensure_tables()
    job_id = uuid.uuid4().hex[:12]
    adapters = available_adapters()
    if scope != "all":
        adapters = adapters[:1]
    _create_job(job_id, "reanalyze", total=len(adapters) * len(codes), scope=scope)
    threading.Thread(target=_run, args=(job_id, store, codes, scope), daemon=True).start()
    return job_id


def _run(job_id: str, store: DefinitionStore, codes: list[str], scope: str) -> None:
    try:
        adapters = available_adapters()
        if not adapters:
            raise RuntimeError("No model adapters available (no credentials).")
        if scope != "all":
            adapters = adapters[:1]
        _update_job(job_id, models_json=json.dumps([a.name for a in adapters]))

        defs = {}
        for code in codes:
            row = store.current(code)
            defs[code] = row["definition"] if row else ""

        sample = select_sample(load_interviews(config.INTERVIEWS_DIR))
        titles = [iv.title for iv in sample]
        merged = merge_documents(sample)
        gt_counts, gt_keys = load_ground_truth_counts(
            config.GROUND_TRUTH_COUNTS_PATH, config.GROUND_TRUTH_COUNTS_SHEET)
        doc_pairs = [(iv.title, iv.key) for iv in sample if iv.key in gt_keys]
        code_to_gt = match_codes(codes, list(gt_counts))

        run_id = time.strftime("%Y%m%d_%H%M%S") + "_reanalyze"
        conn = storage.init_db(config.DB_PATH)

        durations: deque[float] = deque(maxlen=8)  # rolling window for ETA
        done = 0
        total = len(adapters) * len(codes)
        per_model_matrices: list[tuple[str, dict]] = []
        model_kappa: dict[str, dict[str, float]] = {}

        for adapter in adapters:
            hits = []
            for code in codes:
                _update_job(job_id, message=f"{adapter.name}: {code}")
                t0 = time.time()
                code_hits = adapter.code_one(CodingRequest(code, defs[code], merged, tuple(titles)))
                hits.extend(code_hits)
                # Live feed: persist each positive hit (code + quote) as it lands.
                for h in code_hits:
                    _add_hit(job_id, h.code_name, h.quote)
                durations.append(time.time() - t0)
                done += 1
                avg = sum(durations) / len(durations)
                eta = avg * (total - done)
                _update_job(job_id, done=done, eta_seconds=round(eta, 1))
            storage.insert_coding_results(conn, run_id, adapter.name, hits)
            matrix = build_count_matrix(hits, codes, titles)
            storage.insert_count_matrix(conn, run_id, adapter.name, matrix, codes, titles)
            per_model_matrices.append((adapter.name, matrix))
            krs = per_code_kappa(codes, gt_counts, matrix, doc_pairs, code_to_gt)
            storage.insert_kappa(conn, run_id, [(adapter.name, kr) for kr in krs])
            model_kappa[adapter.name] = {kr.code_name: kr.kappa for kr in krs}

        if len(per_model_matrices) > 1:
            maj = majority_vote([m for _, m in per_model_matrices], codes, titles)
            storage.insert_majority_matrix(conn, run_id, maj, codes, titles)
            krs = per_code_kappa(codes, gt_counts, maj, doc_pairs, code_to_gt)
            storage.insert_kappa(conn, run_id, [(MAJORITY, kr) for kr in krs])
        conn.close()

        headline = adapters[0].name
        results = []
        for code in codes:
            prev = store.previous_kappa(code)
            new = _clean(model_kappa[headline].get(code))
            store.record_kappa(code, new, headline)
            results.append({"code": code, "previous_kappa": _clean(prev), "new_kappa": new})

        _update_job(job_id, status="done", message="Complete",
                    eta_seconds=0, results_json=json.dumps(results))
    except Exception as exc:  # noqa: BLE001
        _update_job(job_id, status="error", error=str(exc))
