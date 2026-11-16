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
from dataclasses import dataclass, field
from typing import Callable

import config
from models.base import (CodingRequest, MalformedResponseError, SCOPE_INTERVIEWEE,
                         SCOPE_INTERVIEWER, TruncatedResponseError)
from models.prompting import parse_occurrences
from pipeline.interviews import Interview, merge_documents
from pipeline.quote_check import Transcript, quote_in_text

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
    if isinstance(exc, (TruncatedResponseError, MalformedResponseError)):
        return None           # each has its own handler; neither is infrastructure
    text = f"{type(exc).__name__}: {exc}".lower()
    if any(m in text for m in _QUOTA_MARKERS):
        return "quota"
    if any(m in text for m in _TRANSIENT_MARKERS):
        return "transient"
    return None


def is_transient(exc: BaseException) -> bool:
    """True if *exc* is worth retrying at all (either kind)."""
    return failure_kind(exc) is not None


def returns_raw(adapter) -> bool:
    """True if *adapter* exposes its raw replies (``ModelAdapter.complete``).

    Duck-typed adapters that only provide ``code_one`` (test doubles) do not.
    """
    has = getattr(adapter, "has_raw_replies", None)
    return bool(callable(has) and has())


def _attempt(adapter, req: CodingRequest):
    """One request: ``(hits, reply)``. ``reply`` is None for an adapter that
    does not expose raw replies (test doubles, stubs)."""
    if returns_raw(adapter):
        reply = adapter.complete(req)
        try:
            return parse_occurrences(reply.content, req), reply
        except MalformedResponseError as exc:
            exc.reply = reply
            raise
    return adapter.code_one(req), None


def call_with_retry(adapter, req: CodingRequest, note=print,
                    attempts: int | None = None, *, recorder=None,
                    batch_label: str = "0") -> list:
    """``adapter.code_one`` with retries, recording every attempt.

    Two independent retry budgets:

    * infrastructure faults (``failure_kind``) -- exponential backoff with
      jitter. Both APIM gateways front the same Azure AI Foundry resource, so
      parallel models can trip a shared circuit breaker; unjittered retries
      from parallel workers would all return at once and re-trip it.
    * malformed replies (``MalformedResponseError``) -- the request is re-issued
      unchanged, up to ``config.MALFORMED_RETRY_ATTEMPTS`` times in total. Only
      a reply that cannot be read as an answer is re-asked; a valid answer is
      never re-asked because of what it says, which would bias the result.
      When the budget is spent the error propagates and the code is recorded
      as FAILED -- never as absent.

    ``recorder(req, batch_label, attempt, outcome, reply, detail)`` is called
    once per attempt with ``outcome`` in accepted | malformed | truncated |
    error, so the stored replies account for every request made.
    """
    attempts = attempts or config.TRANSIENT_RETRY_ATTEMPTS
    malformed_budget = config.MALFORMED_RETRY_ATTEMPTS
    infra_failures = malformed = 0
    n = 0

    def record(outcome, reply=None, detail=""):
        if recorder is not None:
            recorder(req, batch_label, n, outcome, reply, detail)

    while True:
        n += 1
        try:
            hits, reply = _attempt(adapter, req)
        except MalformedResponseError as exc:
            malformed += 1
            record("malformed", getattr(exc, "reply", None), str(exc))
            if malformed >= malformed_budget:
                raise
            note(f"    · {req.code_name}: malformed reply ({exc}); "
                 f"re-asking {malformed}/{malformed_budget - 1}")
            continue
        except TruncatedResponseError as exc:
            record("truncated", None, str(exc))
            raise
        except Exception as exc:  # noqa: BLE001
            kind = failure_kind(exc)
            record("error", None, f"{kind or 'permanent'}: {type(exc).__name__}: {exc}"[:2000])
            infra_failures += 1
            if kind is None or infra_failures >= attempts:
                raise
            if kind == "quota":
                # A spent allowance refills on the provider's clock, not ours.
                delay = min(config.QUOTA_RETRY_BASE_SECONDS * infra_failures,
                            config.QUOTA_RETRY_MAX_SECONDS)
            else:
                delay = min(2 ** (infra_failures - 1) * 5, 90)
            delay *= (0.6 + 0.8 * random.random())
            note(f"    · {req.code_name}: {kind} "
                 f"({type(exc).__name__}); retry {infra_failures}/{attempts - 1} "
                 f"in {delay:.0f}s")
            time.sleep(delay)
            continue
        record("accepted", reply)
        return hits


def make_batches(sample: list[Interview], size: int | None = None) -> list[list[Interview]]:
    """Split the sampled documents into per-call batches."""
    size = size or config.DOCS_PER_CALL
    if size <= 0:
        return [list(sample)]
    return [sample[i:i + size] for i in range(0, len(sample), size)]


def code_batch(adapter, code_name: str, definition: str,
               docs: list[Interview], note=print, *, recorder=None,
               batch_label: str = "0") -> list:
    """Code one code over one batch, halving the batch on truncation.

    A single document that still overflows cannot be split further. It raises,
    so the code is recorded as FAILED: recording it as "code absent" would be a
    lie, and so would silently skipping the document (it used to return []).
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
        return call_with_retry(adapter, req, note, recorder=recorder,
                               batch_label=batch_label)
    except TruncatedResponseError:
        if len(docs) == 1:
            note(f"    ! {code_name}: output limit hit on a single document "
                 f"({docs[0].title}); the code cannot be completed.")
            raise
        mid = len(docs) // 2
        note(f"    · {code_name}: output limit hit on {len(docs)} documents, "
             f"splitting into {mid} + {len(docs) - mid}")
        return (code_batch(adapter, code_name, definition, docs[:mid], note,
                           recorder=recorder, batch_label=f"{batch_label}.0")
                + code_batch(adapter, code_name, definition, docs[mid:], note,
                             recorder=recorder, batch_label=f"{batch_label}.1"))


def code_across_batches(adapter, code_name: str, definition: str,
                        batches: list[list[Interview]], note=print, *,
                        recorder=None) -> list:
    """Code one code across every batch, batches in parallel.

    Results are concatenated in batch order, so a run is reproducible regardless
    of which request finishes first.
    """
    if len(batches) == 1:
        return code_batch(adapter, code_name, definition, batches[0], note,
                          recorder=recorder, batch_label="0")

    results: list[list] = [[] for _ in batches]
    with ThreadPoolExecutor(max_workers=config.MAX_CONCURRENT_CALLS) as pool:
        futures = {
            pool.submit(code_batch, adapter, code_name, definition, docs, note,
                        recorder=recorder, batch_label=str(idx)): idx
            for idx, docs in enumerate(batches)
        }
        for fut in as_completed(futures):
            results[futures[fut]] = fut.result()
    return [hit for batch in results for hit in batch]


# --- quote check and re-coding --------------------------------------------------
#
# A model coding 25 interviews at once sometimes files a sentence from one
# interview as the evidence for another (see pipeline.quote_check). The batch
# reply's verdict on that interview is then unreliable, so the interview is
# coded again ON ITS OWN, where there is no other interview to borrow from, and
# the new verdict replaces the batch's. Only the transcript decides what is
# re-coded -- never the human coding -- so the rule cannot steer a result toward
# the ground truth.

#: batch_label prefix of a re-coding request: "recode:<title>:<attempt>".
RECODE_LABEL = "recode"


def recode_label(title: str, attempt: int) -> str:
    return f"{RECODE_LABEL}:{title}:{attempt}"


@dataclass
class CodeResult:
    """One code's occurrences for one model, after the quote check."""

    hits: list
    #: Interviews coded again on their own, in batch order.
    recoded: list[str] = field(default_factory=list)
    #: Of those, interviews whose quotes still could not be found after
    #: ``config.RECODE_ATTEMPTS`` tries. Their verdict is unknown: they carry no
    #: occurrences and are left out of this code's kappa, never scored absent.
    unresolved: list[str] = field(default_factory=list)


def interviews_to_recode(hits, batches: list[list[Interview]]) -> list[str]:
    """Interviews whose verdict for this code must be re-coded alone.

    * every interview named by an occurrence whose quote is not in it;
    * the interview the quote really came from, when that is exactly one other
      interview of the same batch and the reply gave it no valid occurrence --
      its "absent" is as unreliable as the named interview's "present". A quote
      found in several other interviews ("I don't know") identifies no source.
    """
    text = {iv.title: Transcript(iv.text) for docs in batches for iv in docs}
    batch_of = {iv.title: [d.title for d in docs] for docs in batches for iv in docs}
    valid, bad = set(), []
    for h in hits:
        if quote_in_text(h.quote, text[h.document_title]):
            valid.add(h.document_title)
        else:
            bad.append(h)
    cells = {h.document_title for h in bad}
    for h in bad:
        sources = [d for d in batch_of[h.document_title]
                   if d != h.document_title and quote_in_text(h.quote, text[d])]
        if len(sources) == 1 and sources[0] not in valid:
            cells.add(sources[0])
    return [iv.title for docs in batches for iv in docs if iv.title in cells]


def apply_recodes(hits, batches: list[list[Interview]],
                  fetch: Callable[[Interview, int], list],
                  note=print) -> CodeResult:
    """The re-coding rule, independent of where re-coded replies come from.

    ``fetch(interview, attempt)`` returns the occurrences of one request that
    sends *interview* alone. The coding pipeline asks the model; ``verify_run``
    replays the stored reply. An attempt is accepted when every quote in it is
    found in the interview (an empty answer is accepted: "absent"), and then
    replaces everything the batch said about that interview.
    """
    cells = interviews_to_recode(hits, batches)
    if not cells:
        return CodeResult(list(hits))
    by_title = {iv.title: iv for docs in batches for iv in docs}
    kept = [h for h in hits if h.document_title not in cells]
    unresolved = []
    for title in cells:
        iv, transcript = by_title[title], Transcript(by_title[title].text)
        for attempt in range(1, config.RECODE_ATTEMPTS + 1):
            new = fetch(iv, attempt)
            if all(quote_in_text(h.quote, transcript) for h in new):
                kept.extend(new)
                break
            note(f"    · re-coding {title} alone (attempt {attempt}): a quote is "
                 f"still not in the interview")
        else:
            unresolved.append(title)
            note(f"    ! {title}: quotes still not found after "
                 f"{config.RECODE_ATTEMPTS} attempt(s); left out of this code's kappa")
    return CodeResult(kept, recoded=cells, unresolved=unresolved)


def code_and_check(adapter, code_name: str, definition: str,
                   batches: list[list[Interview]], note=print, *,
                   recorder=None) -> CodeResult:
    """Code one code across every batch, then re-code alone any interview the
    quote check finds unreliable (see :func:`apply_recodes`)."""
    hits = code_across_batches(adapter, code_name, definition, batches, note,
                               recorder=recorder)

    def ask_alone(iv: Interview, attempt: int) -> list:
        return code_batch(adapter, code_name, definition, [iv], note,
                          recorder=recorder, batch_label=recode_label(iv.title, attempt))

    return apply_recodes(hits, batches, ask_alone, note)
