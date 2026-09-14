"""Run one code over a document set, in batches, tolerating output truncation.

Shared by the batch pipeline (``run_pipeline``) and the app's re-analysis job
(``app.jobs``) so both handle a truncated model response the same way.

Sending every document in a single request is what the spec asks for (4.1: merge
the transcripts, 4.2: one code at a time), and it is fine while the corpus is
small. It stops being fine as the corpus grows: the output token budget is
shared with the model's reasoning tokens, so a code that appears in most
documents will overflow it, and the reply comes back truncated mid-list. That
looks exactly like a short answer, so it used to be recorded as "code found
nowhere". Batching bounds each answer, and a truncated batch is halved and
retried rather than believed.
"""

from __future__ import annotations

import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import config
from models.base import CodingRequest, SCOPE_INTERVIEWEE, SCOPE_INTERVIEWER, TruncatedResponseError
from pipeline.interviews import Interview, merge_documents

#: Substrings marking a failure of the infrastructure rather than of the model.
#: These must be retried, not recorded: the caller's fallback is to DROP the code
#: from the run, and a code dropped because a shared backend tripped its circuit
#: breaker leaves a hole in the model x code matrix. Holes are not neutral — they
#: silently make a model's mean kappa an average over a different, easier subset
#: of codes than its rivals were scored on, which is a comparison bug, not a
#: missing data point.
_TRANSIENT_MARKERS = (
    "circuitbreaker",
    "temporarily unavailable",
    "rate limit",
    "ratelimit",
    "too many requests",
    "internalservererror",
    "internal server error",
    "bad gateway",
    "service unavailable",
    "gateway timeout",
    "timed out",
    "timeout",
    "connection reset",
    "connection aborted",
    "connection error",
    "remote end closed",
    " 429",
    " 500",
    " 502",
    " 503",
    " 504",
)

#: Quota exhaustion is transient too, but on a different clock. It arrives as a
#: 403 PermissionDeniedError, which reads like a permanent authorisation failure
#: and is easy to mistake for one — the subscription is entitled, it has simply
#: spent its token allowance for the current window. It needs a longer, flatter
#: backoff than a circuit breaker: retrying in 5s cannot help, and the window is
#: measured in minutes.
_QUOTA_MARKERS = ("quota", "exceeded your current quota", "insufficient_quota")


def failure_kind(exc: BaseException) -> str | None:
    """Classify a failure: ``"transient"``, ``"quota"``, or None for permanent.

    Deliberately text-based: the call can surface as an openai SDK exception, a
    requests exception, or a bare RuntimeError from our own Anthropic adapter,
    and the union of their type hierarchies is both large and unstable. A false
    positive costs a retry; a false negative costs a silently dropped code.

    Quota is separated from the other transient faults because it needs a much
    longer, flatter backoff — retrying a spent token allowance five seconds
    later cannot possibly help, whereas a circuit breaker often clears that
    fast.
    """
    if isinstance(exc, TruncatedResponseError):
        return None           # has its own (correct) batch-splitting handler
    text = f"{type(exc).__name__}: {exc}".lower()
    if any(m in text for m in _QUOTA_MARKERS):
        return "quota"
    if any(m in text for m in _TRANSIENT_MARKERS):
        return "transient"
    return None


def is_transient(exc: BaseException) -> bool:
    """True if *exc* is worth retrying at all (either kind)."""
    return failure_kind(exc) is not None


def call_with_retry(adapter, req: CodingRequest, note=print,
                    attempts: int | None = None) -> list:
    """``adapter.code_one`` with backoff on transient infrastructure faults.

    Both APIM gateways in use front the same Azure AI Foundry resource, so
    running several models at once can trip a shared circuit breaker that has
    nothing to do with any one model. Backoff is exponential with jitter — the
    breaker stays open for a fixed window, so unjittered retries from parallel
    workers would all return at the same instant and re-trip it.
    """
    attempts = attempts or config.TRANSIENT_RETRY_ATTEMPTS
    for attempt in range(attempts):
        try:
            return adapter.code_one(req)
        except Exception as exc:  # noqa: BLE001
            kind = failure_kind(exc)
            if kind is None or attempt == attempts - 1:
                raise
            if kind == "quota":
                # A spent allowance refills on the provider's clock, not ours.
                delay = min(config.QUOTA_RETRY_BASE_SECONDS * (attempt + 1),
                            config.QUOTA_RETRY_MAX_SECONDS)
            else:
                delay = min(2 ** attempt * 5, 90)
            delay *= (0.6 + 0.8 * random.random())
            note(f"    · {req.code_name}: {kind} "
                 f"({type(exc).__name__}); retry {attempt + 1}/{attempts - 1} "
                 f"in {delay:.0f}s")
            time.sleep(delay)
    raise AssertionError("unreachable")


def make_batches(sample: list[Interview], size: int | None = None) -> list[list[Interview]]:
    """Split the sampled documents into per-call batches."""
    size = size or config.DOCS_PER_CALL
    if size <= 0:
        return [list(sample)]
    return [sample[i:i + size] for i in range(0, len(sample), size)]


def code_batch(adapter, code_name: str, definition: str,
               docs: list[Interview], note=print) -> list:
    """Code one code over one batch, halving the batch on truncation.

    A single document that still overflows is reported and skipped: it cannot be
    split further, and recording it as "code absent" would be a lie.
    """
    # Scope is a property of the code, so it is resolved here rather than
    # threaded through every caller (and every recursive split below).
    req = CodingRequest(
        code_name=code_name,
        code_description=definition,
        merged_document=merge_documents(docs),
        document_titles=tuple(iv.title for iv in docs),
        scope=(SCOPE_INTERVIEWER if code_name in config.INTERVIEWER_ONLY_CODES
               else SCOPE_INTERVIEWEE),
    )
    try:
        return call_with_retry(adapter, req, note)
    except TruncatedResponseError:
        if len(docs) == 1:
            note(f"    ! {code_name}: output limit hit on a single document "
                 f"({docs[0].title}); its occurrences for this code are missing.")
            return []
        mid = len(docs) // 2
        note(f"    · {code_name}: output limit hit on {len(docs)} documents, "
             f"splitting into {mid} + {len(docs) - mid}")
        return (code_batch(adapter, code_name, definition, docs[:mid], note)
                + code_batch(adapter, code_name, definition, docs[mid:], note))


def code_across_batches(adapter, code_name: str, definition: str,
                        batches: list[list[Interview]], note=print) -> list:
    """Code one code across every batch, batches in parallel.

    Results are concatenated in batch order, so a run is reproducible regardless
    of which request finishes first.
    """
    if len(batches) == 1:
        return code_batch(adapter, code_name, definition, batches[0], note)

    results: list[list] = [[] for _ in batches]
    with ThreadPoolExecutor(max_workers=config.MAX_CONCURRENT_CALLS) as pool:
        futures = {
            pool.submit(code_batch, adapter, code_name, definition, docs, note): idx
            for idx, docs in enumerate(batches)
        }
        for fut in as_completed(futures):
            results[futures[fut]] = fut.result()
    return [hit for batch in results for hit in batch]
