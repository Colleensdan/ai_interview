"""Connectivity check for every model in the catalog.

Exercises the REAL coding path — ``adapter.code_one()`` on a tiny synthetic
transcript — rather than a bare ping, so a model only passes if it authenticates,
accepts our parameters, respects the role-tag rule, and returns JSON we can parse
into CodeHit rows. That is the same contract the pipeline depends on.

    python check_models.py                       # every catalog model
    python check_models.py gpt-5-mini claude-fable-5

Exit status is 0 if at least one model passes, 1 if none do.
"""

from __future__ import annotations

import sys
import time

from models import adapter_for, catalog_adapters, get_spec
from models.base import CodingRequest, TruncatedResponseError

# One document, one obvious hit for the code, and one interviewer turn that must
# NOT be coded — enough to tell "the model works" from "the model answers".
_TITLE = "TestInterview01"
_REQUEST = CodingRequest(
    code_name="time pressure",
    code_description=(
        "The participant describes feeling rushed, short of time, or unable to "
        "fit their work into the hours available."
    ),
    merged_document=(
        f"=== DOCUMENT: {_TITLE} ===\n"
        "[INTERVIEWER — do NOT code this] Do you ever feel short of time?\n"
        "[INTERVIEWEE — you MAY code this] Honestly I never have enough hours in "
        "the day, I'm always rushing to finish before the deadline.\n"
        "[INTERVIEWER — do NOT code this] I'm always rushing too, no time at all.\n"
        "[INTERVIEWEE — you MAY code this] My commute is about forty minutes.\n"
    ),
    document_titles=(_TITLE,),
)


def check(adapter) -> tuple[str, str, float]:
    """Return (status, detail, seconds) for one adapter."""
    started = time.time()
    if not adapter.is_available():
        return "SKIP", "no credentials configured", 0.0
    try:
        hits = adapter.code_one(_REQUEST)
    except TruncatedResponseError as exc:
        return "FAIL", f"truncated: {exc}", time.time() - started
    except Exception as exc:  # noqa: BLE001
        detail = str(exc).replace("\n", " ")
        if "ModelNotAllowed" in detail or "DeploymentNotAllowed" in detail:
            detail = "not entitled on this subscription (403)"
        return "FAIL", detail[:200], time.time() - started
    elapsed = time.time() - started

    if not hits:
        # Parsed fine but found nothing in a passage written to be an obvious
        # match: the call works, the answer is poor. Worth flagging separately.
        return "WEAK", "returned 0 occurrences on an obvious match", elapsed
    bad_title = [h.document_title for h in hits if h.document_title != _TITLE]
    if bad_title:
        return "WEAK", f"unexpected document title(s): {bad_title}", elapsed
    coded_interviewer = any("no time at all" in h.quote for h in hits)
    note = f"{len(hits)} occurrence(s)"
    if coded_interviewer:
        return "WEAK", f"{note}, but coded an INTERVIEWER turn", elapsed
    return "PASS", f"{note}; quote={hits[0].quote[:60]!r}", elapsed


def main() -> int:
    wanted = sys.argv[1:]
    adapters = [adapter_for(m) for m in wanted] if wanted else catalog_adapters()

    print(f"Checking {len(adapters)} model(s)\n")
    results = []
    for adapter in adapters:
        spec = get_spec(getattr(adapter, "model_id", None)
                        or getattr(adapter, "deployment", "") or "")
        label = f"{spec.model_id} [{spec.provider}/{spec.route}]"
        print(f"  {label:52s} ...", end="", flush=True)
        status, detail, elapsed = check(adapter)
        print(f" {status:5s} {elapsed:6.1f}s  {detail}")
        results.append((spec.model_id, status))

    print("\nSummary")
    for status in ("PASS", "WEAK", "FAIL", "SKIP"):
        names = [m for m, s in results if s == status]
        if names:
            print(f"  {status}: {', '.join(names)}")
    usable = [m for m, s in results if s in ("PASS", "WEAK")]
    return 0 if usable else 1


if __name__ == "__main__":
    raise SystemExit(main())
