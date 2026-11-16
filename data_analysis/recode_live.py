"""Apply the quote check to the results the live site shows for one model.

Results coded before the quote check existed (2026-11-16, see
pipeline/quote_check.py) may file a quote under the wrong interview. For one
model, this takes each code's result the site shows -- the run its newest kappa
came from -- and applies exactly the rule a run now applies as it codes
(``pipeline.coding.apply_recodes``): every interview the check sends back is
coded again on its own, with the definition that result was coded under, up to
``config.RECODE_ATTEMPTS`` times.

The outcome is published as ONE new run (``<timestamp>_recode``) holding, for
every code that needed it, all of that code's occurrences (those not re-coded
are carried over from the source run unchanged), its count matrix and kappa.
Nothing is edited or deleted: the source runs stay as they were, and the site,
which shows each code's newest kappa, moves to the repaired result.

Refused, per code:

* a definition that changed after the source result was coded -- that needs a
  fresh re-analysis, not a repair;
* a source result whose stored counts do not follow from its stored quotes, or
  whose stored kappa this script does not reproduce from its counts (so a
  repaired kappa is computed exactly as the original was).

Safety as in sync_to_live.py: the live DB is backed up first, the definitions'
text is fingerprinted before and after, the upload carries If-Match, and what
landed is checked. The re-coding replies are stored locally in raw_responses
under the new run, like any run's replies.

    USE_AZURE=false python recode_live.py --model openai-gpt-5-mini --dry-run
    USE_AZURE=false python recode_live.py --model openai-gpt-5-mini
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import sys
import time
import uuid
from dataclasses import dataclass, field
from types import SimpleNamespace

import config
from app import sharepoint_io as sp
from models import available_adapters
from models.base import SCOPE_INTERVIEWEE, SCOPE_INTERVIEWER, CodeHit, CodingRequest
from models.prompting import SYSTEM_PROMPT, build_user_prompt
from pipeline import provenance, storage
from pipeline.agreement import match_codes, per_code_kappa
from pipeline.coding import (apply_recodes, code_batch, interviews_to_recode,
                             make_batches, recode_label)
from pipeline.ground_truth import load_ground_truth_counts
from pipeline.interviews import load_interviews, merge_documents, select_sample
from pipeline.matrices import build_count_matrix
from sync_to_live import (BACKUP_ROOT, RUN_TABLES, _remote_db, copy_rows,
                          definitions_fingerprint, load_remote)
from verify_run import _same_kappa


def _k(x) -> str:
    return "n/a" if x is None or x != x else f"{x:.4f}"


@dataclass
class Repair:
    code: str
    source_run: str
    definition: str
    hits: list
    cells: list[str]
    source_kappa: tuple
    notes: list[str] = field(default_factory=list)


def _run_started(local, run_id: str) -> dt.datetime:
    """When *run_id* started, in UTC. A local run recorded it; a website
    re-analysis is named after its start time on the server, which runs UTC."""
    row = local.execute("SELECT MIN(started_at) FROM run_invocations WHERE run_id=?",
                        (run_id,)).fetchone()
    if row and row[0]:
        return dt.datetime.strptime(row[0], "%Y-%m-%dT%H:%M:%S%z").astimezone(dt.timezone.utc)
    return dt.datetime.strptime(run_id[:15], "%Y%m%d_%H%M%S").replace(tzinfo=dt.timezone.utc)


def _utc(sqlite_ts: str) -> dt.datetime:
    return dt.datetime.strptime(sqlite_ts, "%Y-%m-%d %H:%M:%S").replace(tzinfo=dt.timezone.utc)


def plan(remote, local, model: str, sample, gt_counts, gt_keys) -> tuple[list[Repair], list[str]]:
    """``(repairs, refusals)`` for every code the site shows a *model* kappa for."""
    titles = [iv.title for iv in sample]
    batches = make_batches(sample)
    doc_pairs = [(iv.title, iv.key) for iv in sample if iv.key in gt_keys]
    shown = remote.execute(
        "SELECT code_name, run_id, kappa, n_documents, n_human_present, n_llm_present "
        "FROM kappa k WHERE model=? AND id=(SELECT MAX(id) FROM kappa WHERE "
        "model=k.model AND code_name=k.code_name) ORDER BY code_name", (model,)).fetchall()
    repairs, refusals = [], []
    for code, run_id, *stored_kappa in shown:
        hits = [CodeHit(d, code, q, r) for d, q, r in remote.execute(
            "SELECT document_title, quote, reason FROM coding_results WHERE run_id=? "
            "AND model=? AND code_name=? ORDER BY id", (run_id, model, code))]
        cells = interviews_to_recode(hits, batches)
        if not cells:
            continue
        why = []
        counts = dict(remote.execute(
            "SELECT document, count FROM count_matrix WHERE run_id=? AND model=? "
            "AND code_name=?", (run_id, model, code)).fetchall())
        if counts != build_count_matrix(hits, [code], titles)[code]:
            why.append("its stored counts do not follow from its stored quotes")
        code_to_gt = match_codes([code], list(gt_counts), aliases=config.GT_CODE_ALIASES)
        kr = per_code_kappa([code], gt_counts, {code: counts}, doc_pairs, code_to_gt)[0]
        if not (_same_kappa(kr.kappa, stored_kappa[0]) and
                (kr.n_documents, kr.n_human_present, kr.n_llm_present) == tuple(stored_kappa[1:])):
            why.append(f"rescoring its counts gives {kr} not the stored {stored_kappa}")
        versions = remote.execute(
            "SELECT definition, created_at, is_current FROM code_definitions "
            "WHERE code_name=? ORDER BY version DESC", (code,)).fetchall()
        current = [v for v in versions if v[2]]
        started = _run_started(local, run_id)
        if len(current) != 1 or not versions[0][2]:
            why.append("its current definition is not its newest version")
        elif max(_utc(v[1]) for v in versions) > started:
            why.append(f"its definition changed after {run_id} started ({started:%Y-%m-%d %H:%M} UTC)")
        if why:
            refusals.append(f"{code} ({run_id}): " + "; ".join(why))
            continue
        rep = Repair(code, run_id, current[0][0], hits, cells, tuple(stored_kappa))
        # Where the source run's replies are stored locally, prove the
        # definition is the one it was sent, not just older than the run.
        row = local.execute(
            "SELECT document_titles, prompt_sha256 FROM raw_responses WHERE run_id=? "
            "AND model=? AND code_name=? AND final=1 LIMIT 1", (run_id, model, code)).fetchone()
        if row is not None:
            by_title = {iv.title: iv for iv in sample}
            docs = [by_title[t] for t in json.loads(row[0])]
            req = CodingRequest(code, rep.definition, merge_documents(docs),
                                tuple(iv.title for iv in docs),
                                SCOPE_INTERVIEWER if code in config.INTERVIEWER_ONLY_CODES
                                else SCOPE_INTERVIEWEE)
            if provenance.prompt_sha256(SYSTEM_PROMPT, build_user_prompt(req)) != row[1]:
                refusals.append(f"{code} ({run_id}): the current definition is not the "
                                f"one its stored replies were sent")
                continue
            rep.notes.append("definition proven identical to the one sent (prompt digest)")
        else:
            rep.notes.append(f"definition unchanged since before the run ({started:%Y-%m-%d %H:%M} UTC)")
        repairs.append(rep)
    return repairs, refusals


def recode(repairs: list[Repair], adapter, local, sample, gt_counts, gt_keys,
           run_id: str, invocation_id: str) -> dict:
    """Re-code every repair's interviews alone; store the new run locally."""
    titles = [iv.title for iv in sample]
    batches = make_batches(sample)
    doc_pairs = [(iv.title, iv.key) for iv in sample if iv.key in gt_keys]
    outcome = {}
    for rep in repairs:
        code_attempt = uuid.uuid4().hex

        def recorder(req, batch_label, attempt, result, reply, detail, _ca=code_attempt):
            storage.insert_raw_response(
                local, run_id=run_id, invocation_id=invocation_id, model=adapter.name,
                code_name=req.code_name, code_attempt=_ca, batch_label=batch_label,
                document_titles=req.document_titles,
                prompt_sha256=provenance.prompt_sha256(SYSTEM_PROMPT, build_user_prompt(req)),
                attempt=attempt, outcome=result, detail=detail, reply=reply)

        def alone(iv, attempt, _rep=rep):
            return code_batch(adapter, _rep.code, _rep.definition, [iv], print,
                              recorder=recorder, batch_label=recode_label(iv.title, attempt))

        res = apply_recodes(rep.hits, batches, alone)
        matrix = build_count_matrix(res.hits, [rep.code], titles, {rep.code: res.unresolved})
        storage.persist_code(local, run_id, adapter.name, rep.code, matrix, titles,
                             res.hits, code_attempt)
        code_to_gt = match_codes([rep.code], list(gt_counts), aliases=config.GT_CODE_ALIASES)
        kr = per_code_kappa([rep.code], gt_counts, matrix, doc_pairs, code_to_gt)[0]
        storage.insert_kappa(local, run_id, [(adapter.name, kr)])
        before = {d for d, n in build_count_matrix(rep.hits, [rep.code], titles)[rep.code].items() if n}
        after = {d for d, n in matrix[rep.code].items() if n}
        outcome[rep.code] = {"source_run": rep.source_run, "recoded": res.recoded,
                             "unresolved": res.unresolved,
                             "now_present": sorted(after - before),
                             "now_absent": sorted(before - after - set(res.unresolved)),
                             "kappa_before": rep.source_kappa[0], "kappa_after": kr.kappa,
                             "n_documents": kr.n_documents}
    return outcome


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--model", required=True)
    ap.add_argument("--dry-run", action="store_true",
                    help="Report what would be re-coded; call no model, write nothing.")
    args = ap.parse_args()
    if not sp.configured():
        raise SystemExit("SharePoint is not configured (see .env).")

    sample = select_sample(load_interviews(config.INTERVIEWS_DIR))
    gt_counts, gt_keys = load_ground_truth_counts(
        config.GROUND_TRUTH_COUNTS_PATH, config.GROUND_TRUTH_COUNTS_SHEET)
    local = storage.init_db(config.DB_PATH)

    stamp = time.strftime("%Y%m%d_%H%M%S")
    blob, etag = sp.download_with_etag(_remote_db())
    bdir = BACKUP_ROOT / f"prerecode_{stamp}"
    bdir.mkdir(parents=True, exist_ok=True)
    (bdir / "coding.sqlite").write_bytes(blob)
    (bdir / "MANIFEST.json").write_text(json.dumps({
        "taken_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "remote": _remote_db(), "etag": etag, "bytes": len(blob),
        "sha256": hashlib.sha256(blob).hexdigest(), "about_to": f"recode {args.model}",
    }, indent=2))
    print(f"backed up live DB -> {bdir}  ({len(blob):,} B, etag {etag})")
    remote = load_remote(blob)
    before = definitions_fingerprint(remote)

    repairs, refusals = plan(remote, local, args.model, sample, gt_counts, gt_keys)
    print(f"\n{args.model}: {len(repairs)} code(s) to repair, {len(refusals)} refused")
    for rep in repairs:
        print(f"  {rep.code}  (from {rep.source_run}, kappa {_k(rep.source_kappa[0])})")
        print(f"    re-code alone: {', '.join(rep.cells)}")
        for n in rep.notes:
            print(f"    {n}")
    for r in refusals:
        print(f"  REFUSED {r}")
    if args.dry_run or not repairs:
        print("\nnothing written." if not repairs else "\n--dry-run: nothing written.")
        return 0 if not refusals else 1

    adapter = next((a for a in available_adapters() if a.name == args.model), None)
    if adapter is None:
        raise SystemExit(f"{args.model} is not available here (credentials / USE_AZURE?).")
    run_id = f"{stamp}_recode"
    invocation_id = uuid.uuid4().hex
    codes = [SimpleNamespace(name=r.code, definition=r.definition) for r in repairs]
    prov = provenance.build(codes=codes, sample=sample, adapters=[adapter],
                            definitions="current", definitions_src=f"live:{_remote_db()}",
                            definitions_max_version=None)
    prov["info"]["recode_of"] = {r.code: r.source_run for r in repairs}
    storage.record_run(local, run_id, label="recode", is_full=False, n_codes=len(repairs),
                       n_documents=len(sample), definitions="current", models=[adapter.name])
    storage.record_invocation(local, invocation_id=invocation_id, run_id=run_id,
                              started_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                              git_commit=prov["info"]["git_commit"],
                              git_dirty=prov["info"]["git_dirty"], models=[adapter.name],
                              argv=sys.argv, provenance=prov)
    outcome = recode(repairs, adapter, local, sample, gt_counts, gt_keys, run_id, invocation_id)
    storage.finish_invocation(local, invocation_id, time.strftime("%Y-%m-%dT%H:%M:%S%z"))
    (bdir / "recode_outcome.json").write_text(json.dumps(
        {"run_id": run_id, "model": args.model, "codes": outcome}, indent=2))
    print(f"\nrun {run_id}:")
    for code, o in outcome.items():
        print(f"  {code}: kappa {_k(o['kappa_before'])} -> {_k(o['kappa_after'])} "
              f"(n={o['n_documents']}); now present {o['now_present'] or '-'}, now absent "
              f"{o['now_absent'] or '-'}, unresolved {o['unresolved'] or '-'}")

    # --- merge into the live DB, exactly as sync_to_live does ---------------------
    remote.executescript(storage._SCHEMA)
    moved = {t: copy_rows(local, remote, t, run_id, with_vote=False) for t in RUN_TABLES}
    cols = [r[1] for r in local.execute("PRAGMA table_info(runs)")]
    remote.execute(f"INSERT INTO runs ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                   tuple(local.execute(f"SELECT {','.join(cols)} FROM runs WHERE run_id=?",
                                       (run_id,)).fetchone()))
    # The definition's recorded kappa follows the repair only where it was the
    # source result's (a re-analysis records it; a pipeline run does not).
    for code, o in outcome.items():
        remote.execute("UPDATE code_definitions SET kappa=? WHERE code_name=? AND "
                       "is_current=1 AND model=? AND ABS(kappa - ?) <= 1e-12",
                       (o["kappa_after"], code, args.model, o["kappa_before"]))
    remote.commit()
    if definitions_fingerprint(remote) != before:
        raise SystemExit(f"ABORTED: the merge changed definition text; nothing uploaded. "
                         f"Backup: {bdir}")
    if remote.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise SystemExit("ABORTED: merged database failed its integrity check; nothing uploaded.")
    out = remote.serialize()
    remote.close()
    try:
        new_etag = sp.upload_bytes(_remote_db(), out, if_match=etag)
    except sp.PreconditionFailed:
        raise SystemExit("ABORTED: the live database changed meanwhile; nothing "
                         f"overwritten. The new run is stored locally as {run_id}.")
    print(f"\nuploaded {len(out):,} B (etag {new_etag})")

    check = load_remote(sp.download_bytes(_remote_db()))
    ok = definitions_fingerprint(check) == before and all(
        check.execute(f"SELECT COUNT(*) FROM {t} WHERE run_id=?", (run_id,)).fetchone()[0]
        == moved[t] for t in RUN_TABLES)
    check.close()
    print(f"verified on the live copy: {'ok' if ok else 'MISMATCH'}")
    if not ok:
        print(f"!! Restore from {bdir}/coding.sqlite")
        return 1
    print(f"Done. Backup and outcome: {bdir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
