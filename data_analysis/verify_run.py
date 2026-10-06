"""Audit a run: re-derive every stored result from the stored raw replies.

A published kappa should be traceable, without calling any model, back through
the count matrix to the exact text each model returned and the exact prompt it
was sent. This script checks that chain for one run and exits non-zero if any
link fails:

1. provenance  -- every invocation is recorded, all of them agree on everything
                  that shapes a request or its parsing, and the code was
                  committed when they ran;
2. inputs      -- the codebook/definitions and transcripts on disk now are the
                  ones the run used (same fingerprints);
3. replies     -- for every (model, code) the run stored, the accepted replies
                  cover every document exactly once, each was sent the prompt
                  rebuilt from today's inputs (SHA-256 match), and re-parsing
                  them reproduces the stored count matrix and quotes exactly;
4. scoring     -- recomputing the majority vote and every kappa from the stored
                  matrices with ``pipeline.scoring.score`` reproduces the
                  stored kappa table exactly.

    python verify_run.py --run-id <id>
    python verify_run.py --run-id <id> --definitions-db <archived coding.sqlite>

``--definitions-db`` defaults to the definitions database the run recorded
using; pass it only if that file has moved.
"""

from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from collections import Counter

import config
from models.base import SCOPE_INTERVIEWEE, SCOPE_INTERVIEWER, CodingRequest
from models.prompting import (PARSER_VERSION, SYSTEM_PROMPT, build_user_prompt,
                              extract_json, parse_occurrences)
from pipeline import provenance, scoring, storage
from pipeline.agreement import match_codes
from pipeline.ground_truth import load_ground_truth_counts
from pipeline.interviews import load_interviews, merge_documents, select_sample
from pipeline.matrices import build_count_matrix


class Report:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.lines: list[str] = []

    def check(self, ok: bool, what: str, detail: str = "") -> bool:
        self.lines.append(f"  [{'PASS' if ok else 'FAIL'}] {what}"
                          + (f"\n         {detail}" if detail and not ok else ""))
        if not ok:
            self.failures.append(what + (f": {detail}" if detail else ""))
        return ok

    def note(self, text: str) -> None:
        self.lines.append(f"         {text}")

    def section(self, title: str) -> None:
        self.lines.append(f"\n{title}")

    @property
    def ok(self) -> bool:
        return not self.failures

    def render(self) -> str:
        return "\n".join(self.lines)


def _is_empty_answer(content: str | None) -> bool:
    try:
        return extract_json(content or "") == {"occurrences": []}
    except ValueError:
        return False


def _same_kappa(a, b) -> bool:
    if a is None or (isinstance(a, float) and math.isnan(a)):
        return b is None or (isinstance(b, float) and math.isnan(b))
    return b is not None and not (isinstance(b, float) and math.isnan(b)) \
        and abs(a - b) <= 1e-12


def verify(conn: sqlite3.Connection, run_id: str, definitions_db=None,
           *, require_clean: bool = True) -> Report:
    import run_pipeline

    r = Report()
    r.section(f"run {run_id}")
    run = conn.execute("SELECT is_full, definitions, models FROM runs WHERE run_id=?",
                       (run_id,)).fetchone()
    if not r.check(run is not None, "run is recorded"):
        return r

    # 1. provenance -----------------------------------------------------------
    r.section("1. provenance")
    invs = storage.invocations(conn, run_id)
    if not r.check(bool(invs), "invocations are recorded",
                   "this run predates provenance recording; its results cannot "
                   "be traced to raw replies"):
        return r
    first = invs[0]["provenance"]
    for inv in invs[1:]:
        diff = provenance.mismatches(first, inv["provenance"])
        r.check(not diff, f"invocation {inv['invocation_id'][:8]} matches the first",
                "; ".join(diff))
    r.check(first["must_match"]["parser_version"] == PARSER_VERSION,
            f"run was parsed under the current parser (v{PARSER_VERSION})",
            f"run used v{first['must_match']['parser_version']}")
    for inv in invs:
        tag = f"invocation {inv['invocation_id'][:8]} ({inv['started_at']})"
        r.check(inv["git_commit"] is not None, f"{tag}: code version recorded")
        if require_clean:
            r.check(not inv["git_dirty"], f"{tag}: ran from committed code",
                    f"commit {inv['git_commit']} had uncommitted changes")
        r.note(f"{tag}: commit {(inv['git_commit'] or '?')[:12]}, "
               f"models {inv['models']}, finished {inv['finished_at'] or 'NO (interrupted)'}")
    commits = sorted({inv["git_commit"] for inv in invs})
    if len(commits) > 1:
        r.note(f"invocations span {len(commits)} commits: "
               + ", ".join(c[:12] for c in commits if c))

    # 2. inputs -----------------------------------------------------------------
    r.section("2. inputs on disk are the inputs the run used")
    mm = first["must_match"]
    if definitions_db is None and mm["definitions"] == "current":
        # Default to the definitions store the run recorded using; the
        # fingerprint comparison below still proves it holds the same text.
        definitions_db = first["info"].get("definitions_src")
    codes, src, max_v = run_pipeline.load_codes(mm["definitions"], definitions_db)
    by_name = {c.name: c for c in codes}
    missing = [n for n in mm["codes"] if n not in by_name]
    if not r.check(not missing, "every recorded code exists in today's codebook",
                   f"missing: {missing}"):
        return r
    codes = [by_name[n] for n in mm["codes"]]
    sample = select_sample(load_interviews(config.INTERVIEWS_DIR))[:mm["n_documents"]]
    now = provenance.build(codes=codes, sample=sample, adapters=[],
                           definitions=mm["definitions"], definitions_src=src,
                           definitions_max_version=max_v)
    diff = provenance.mismatches(first, now)
    if not r.check(not diff, "definitions, transcripts, prompt and settings match",
                   "; ".join(diff)):
        return r
    titles = [iv.title for iv in sample]
    docs_by_title = {iv.title: iv for iv in sample}

    # 3. replies ----------------------------------------------------------------
    r.section("3. stored results re-derived from raw replies")
    stored = storage.load_count_matrices(conn, run_id)
    outcome_counts: dict[str, Counter] = {}
    for model, outcome, n in conn.execute(
            "SELECT model, outcome, COUNT(*) FROM raw_responses WHERE run_id=? "
            "GROUP BY model, outcome", (run_id,)):
        outcome_counts.setdefault(model, Counter())[outcome] = n
    checked = bad = 0
    for model in sorted(stored):
        problems = []
        for code_name in sorted(stored[model]):
            checked += 1
            code = by_name[code_name]
            rows = conn.execute(
                "SELECT code_attempt, batch_label, document_titles, prompt_sha256, "
                "outcome, content FROM raw_responses WHERE run_id=? AND model=? "
                "AND code_name=? AND final=1 ORDER BY batch_label",
                (run_id, model, code_name)).fetchall()
            why = None
            if not rows:
                why = "no final replies"
            elif len({row[0] for row in rows}) != 1:
                why = "final replies come from more than one coding attempt"
            elif any(row[4] != "accepted" for row in rows):
                why = "a final reply was not an accepted one"
            else:
                covered = Counter(t for row in rows for t in json.loads(row[2]))
                if covered != Counter(titles):
                    why = "accepted replies do not cover every document exactly once"
            hits = []
            if why is None:
                scope = (SCOPE_INTERVIEWER if code_name in config.INTERVIEWER_ONLY_CODES
                         else SCOPE_INTERVIEWEE)
                for row in rows:
                    batch_titles = tuple(json.loads(row[2]))
                    req = CodingRequest(
                        code_name=code_name, code_description=code.definition,
                        merged_document=merge_documents(
                            [docs_by_title[t] for t in batch_titles]),
                        document_titles=batch_titles, scope=scope)
                    digest = provenance.prompt_sha256(SYSTEM_PROMPT, build_user_prompt(req))
                    if digest != row[3]:
                        why = f"batch {row[1]}: prompt differs from the one sent"
                        break
                    hits.extend(parse_occurrences(row[5], req))
            if why is None:
                rebuilt = build_count_matrix(hits, [code_name], titles)[code_name]
                if {d: rebuilt.get(d, 0) for d in titles} != \
                        {d: stored[model][code_name].get(d, 0) for d in titles}:
                    why = "re-parsed count matrix differs from the stored one"
            if why is None:
                quotes = Counter((h.document_title, h.quote, h.reason) for h in hits)
                stored_q = Counter(tuple(q) for q in conn.execute(
                    "SELECT document_title, quote, reason FROM coding_results "
                    "WHERE run_id=? AND model=? AND code_name=?",
                    (run_id, model, code_name)))
                if quotes != stored_q:
                    why = "re-parsed quotes differ from the stored ones"
            if why is not None:
                problems.append(f"{code_name}: {why}")
        bad += len(problems)
        oc = outcome_counts.get(model, Counter())
        r.check(not problems, f"{model}: {len(stored[model])} code(s) re-derived",
                "; ".join(problems[:5]) + (f" (+{len(problems) - 5} more)"
                                           if len(problems) > 5 else ""))
        r.note(f"requests: {sum(oc.values())} -- " + ", ".join(
            f"{k} {v}" for k, v in sorted(oc.items())))
        # Disclosure, not a check: a valid empty answer cannot be rejected
        # without biasing the result, but a model that often answers "absent"
        # in a handful of tokens is worth reporting next to its kappa.
        finals = conn.execute(
            "SELECT content, completion_tokens FROM raw_responses WHERE run_id=? "
            "AND model=? AND final=1", (run_id, model)).fetchall()
        empty = [t for c, t in finals if _is_empty_answer(c)]
        if finals:
            short = sum(1 for t in empty if t is not None and t <= 30)
            r.note(f"accepted replies saying 'absent in every document': "
                   f"{len(empty)}/{len(finals)} ({len(empty) / len(finals):.0%}), "
                   f"{short} of them in <=30 output tokens")
    failed_codes = conn.execute(
        "SELECT model, code_name FROM raw_responses WHERE run_id=? "
        "GROUP BY model, code_name", (run_id,)).fetchall()
    unstored = sorted((m, c) for m, c in failed_codes
                      if c not in stored.get(m, {}))
    if unstored:
        r.note(f"{len(unstored)} (model, code) pair(s) were attempted but never "
               f"completed (FAILED, excluded from scoring): "
               + ", ".join(f"{m}/{c}" for m, c in unstored[:10]))

    # 4. scoring ----------------------------------------------------------------
    r.section("4. kappa recomputed from the stored matrices")
    last = invs[-1]["provenance"]["info"]
    r.check(provenance.sha256_file(config.GROUND_TRUTH_COUNTS_PATH)
            == last.get("ground_truth_counts_sha256"),
            "ground truth on disk is the one the run was scored against")
    gt_counts, gt_keys = load_ground_truth_counts(
        config.GROUND_TRUTH_COUNTS_PATH, config.GROUND_TRUTH_COUNTS_SHEET)
    doc_pairs = [(iv.title, iv.key) for iv in sample if iv.key in gt_keys]
    code_names = mm["codes"]
    code_to_gt = match_codes(code_names, list(gt_counts),
                             aliases=config.GT_CODE_ALIASES)
    majority, rows = scoring.score(sorted(stored.items()), code_names, titles,
                                   doc_pairs, gt_counts, code_to_gt)
    want = {(m, kr.code_name): kr for m, kr in rows}
    have = {(row[0], row[1]): row[2:] for row in conn.execute(
        "SELECT model, code_name, kappa, n_documents, n_human_present, "
        "n_llm_present FROM kappa WHERE run_id=?", (run_id,))}
    r.check(set(want) == set(have), "kappa rows cover the same (model, code) pairs",
            f"recomputed-only {len(set(want) - set(have))}, "
            f"stored-only {len(set(have) - set(want))}")
    diffs = [k for k in set(want) & set(have)
             if not _same_kappa(want[k].kappa, have[k][0])
             or (want[k].n_documents, want[k].n_human_present,
                 want[k].n_llm_present) != tuple(have[k][1:])]
    r.check(not diffs, f"{len(set(want) & set(have))} stored kappa value(s) reproduced exactly",
            f"{len(diffs)} differ, e.g. {sorted(diffs)[:3]}")
    stored_vote = {}
    for c, d, n in conn.execute("SELECT code_name, document, count FROM "
                                "majority_vote_matrix WHERE run_id=?", (run_id,)):
        stored_vote.setdefault(c, {})[d] = n
    r.check(stored_vote == {c: dict(v) for c, v in majority.items()},
            "stored majority vote reproduced exactly")
    return r


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--definitions-db", default=None)
    ap.add_argument("--allow-dirty", action="store_true",
                    help="Do not fail invocations that ran from uncommitted code.")
    args = ap.parse_args()
    conn = storage.init_db(config.DB_PATH)
    try:
        report = verify(conn, args.run_id, args.definitions_db,
                        require_clean=not args.allow_dirty)
    finally:
        conn.close()
    print(report.render())
    print(f"\n{'VERIFIED' if report.ok else 'NOT VERIFIED'}: "
          f"{len(report.failures)} failure(s)")
    for f in report.failures:
        print(f"  - {f}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
