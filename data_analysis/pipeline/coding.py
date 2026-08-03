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

from concurrent.futures import ThreadPoolExecutor, as_completed

import config
from models.base import CodingRequest, TruncatedResponseError
from pipeline.interviews import Interview, merge_documents


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
    req = CodingRequest(
        code_name=code_name,
        code_description=definition,
        merged_document=merge_documents(docs),
        document_titles=tuple(iv.title for iv in docs),
    )
    try:
        return adapter.code_one(req)
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
