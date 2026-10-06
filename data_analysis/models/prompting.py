"""Prompt text and response parsing shared by every model adapter.

Extracted verbatim from the original Azure adapter so a new provider (Anthropic,
Mistral, another OpenAI deployment) is scored on the same prompt and parsed by
the same rules. A model that only differs in its transport should not also
differ in what it was asked or how its answer was read — otherwise a kappa
comparison measures the adapter, not the model.
"""

from __future__ import annotations

import json
import re

import config

from .base import SCOPE_INTERVIEWER, CodeHit, CodingRequest, MalformedResponseError

SYSTEM_PROMPT = (
    "You are an expert qualitative-coding assistant for social-science research. "
    "You apply ONE code from a codebook to interview transcripts. You are given "
    "the code's name, the code's definition, and a set of interview documents "
    "(each clearly delimited and titled).\n\n"
    "ROLE TAGS: some transcripts tag each turn by role. Where a turn is tagged "
    "'[INTERVIEWER — do NOT code this]' (the researcher; e.g. 'assistant'), you "
    "must NEVER code it, even if it matches the definition. Turns tagged "
    "'[INTERVIEWEE — you MAY code this]' (the participant; e.g. 'user') and any "
    "UNTAGGED transcript text may be coded if they fit. The role tags are not "
    "part of the transcript text — do not include them in quotes.\n\n"
    "Find EVERY passage where the code genuinely applies. Participants rarely use "
    "the code's exact words, so include implicit, paraphrased, or subtle "
    "expressions of the concept — but only where the passage truly reflects the "
    "definition. Do not stretch the definition to fit unrelated text. For each "
    "occurrence, return the exact verbatim quote (copied from the transcript) "
    "and a short reason explaining why the code fits, citing the part of the "
    "definition it matches.\n\n"
    "Respond with a single JSON object and nothing else, in this exact shape:\n"
    '{"occurrences": [{"document_title": "<one of the given titles>", '
    '"quote": "<verbatim quote>", "reason": "<why the code applies>"}]}\n'
    'If the code does not genuinely apply anywhere, return {"occurrences": []}.'
)


# Overrides the transcript's own role tag, for the handful of procedural codes
# whose referent IS the interviewer. Carried in the *user* message rather than
# SYSTEM_PROMPT deliberately: the system prompt then stays byte-identical across
# every code, so runs of the substantive codes remain directly comparable with
# those already reported, and a future cacheable prefix is not split per code.
_INTERVIEWER_SCOPE = (
    "\nSCOPE FOR THIS CODE: this code describes the INTERVIEWER's own behaviour, "
    "not anything the participant said. For this code only, the "
    "'[INTERVIEWER \u2014 do NOT code this]' tag does NOT apply: you must code "
    "ONLY interviewer turns, "
    "and must NOT code interviewee turns.\n"
)


def build_user_prompt(request: CodingRequest) -> str:
    """The per-code user message. Identical across providers.

    With the default interviewee scope the text is byte-identical to the form
    used by earlier runs, so reinstating the procedural codes does not silently
    re-prompt the 49 substantive ones.
    """
    scope = _INTERVIEWER_SCOPE if request.scope == SCOPE_INTERVIEWER else ""
    if config.PROMPT_ORDER == "documents_first":
        # Everything before CODE NAME is identical for every code in a batch, so
        # it forms a cacheable prefix. The document titles belong in it: they
        # describe the batch, not the code. Nothing dynamic may ever be added
        # above this line or the prefix stops matching and caching silently dies.
        return (
            "INTERVIEW DOCUMENTS:\n"
            f"{request.merged_document}\n\n"
            f"Valid document titles: {list(request.document_titles)}\n\n"
            f"CODE NAME: {request.code_name}\n"
            f"CODE DEFINITION: {request.code_description}\n"
            f"{scope}"
        )
    return (
        f"CODE NAME: {request.code_name}\n"
        f"CODE DEFINITION: {request.code_description}\n"
        f"{scope}"
        "\n"
        f"Valid document titles: {list(request.document_titles)}\n\n"
        "INTERVIEW DOCUMENTS:\n"
        f"{request.merged_document}"
    )


#: Bumped whenever the acceptance rules below change. Recorded with every run
#: and checked on resume, so one run is never parsed under two sets of rules.
#:   1 -- lenient: any unusable reply was read as "no occurrences" (to 2026-10).
#:   2 -- strict: an unusable reply raises MalformedResponseError.
PARSER_VERSION = 2

_DECODER = json.JSONDecoder(strict=False)   # tolerate raw control chars in strings
_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


def _json_objects(text: str) -> list[object]:
    """Every top-level JSON object in *text*, scanning left to right.

    Each ``{`` is tried as the start of an object; on success the scan resumes
    after it, so nested objects are not double-counted. Deterministic: the same
    text always yields the same list in the same order.
    """
    found, i = [], text.find("{")
    while i != -1:
        try:
            obj, end = _DECODER.raw_decode(text, i)
        except json.JSONDecodeError:
            i = text.find("{", i + 1)
            continue
        found.append(obj)
        i = text.find("{", end)
    return found


def extract_json(text: str) -> dict:
    """The one JSON object in a model reply that answers the coding question.

    Accepts a bare object, a ```json fence, and prose before or after the
    object -- including prose that contains braces, which used to make the
    whole reply unparseable. Rejects (``MalformedResponseError``):

    * no object carrying an ``occurrences`` key -- e.g. a valid JSON answer to
      a different question, which was being read as "absent everywhere";
    * more than one such object -- ambiguous, and picking one would be a guess.
    """
    stripped = text.strip()
    fence = _FENCE.match(stripped)
    if fence:
        stripped = fence.group(1).strip()
    if not stripped:
        raise MalformedResponseError("empty reply", text)
    candidates = [o for o in _json_objects(stripped)
                  if isinstance(o, dict) and "occurrences" in o]
    if not candidates:
        raise MalformedResponseError(
            "no JSON object with an 'occurrences' key", text)
    if len(candidates) > 1:
        raise MalformedResponseError(
            f"{len(candidates)} JSON objects with an 'occurrences' key", text)
    return candidates[0]


def _resolve_title(title: str, valid: tuple[str, ...]) -> str | None:
    """Map a returned title to exactly one document in the batch, or None.

    An exact match wins. Otherwise a title that is a substring of exactly ONE
    valid title (a model dropping ``.txt``) is snapped to it. Zero or several
    matches resolve to nothing: attributing a quote to a guessed document would
    corrupt the count matrix, and the old first-match rule depended on set
    iteration order, which varies between processes.
    """
    if title in valid:
        return title
    if not title:
        return None
    matches = [t for t in valid if title in t]
    return matches[0] if len(matches) == 1 else None


def parse_occurrences(content: str, request: CodingRequest) -> list[CodeHit]:
    """Turn a model's raw reply into CodeHit rows, or raise.

    The accepted shape is exactly what the system prompt asks for: one object
    whose ``occurrences`` is a list, each element an object with a non-empty
    string ``quote`` and a ``document_title`` naming a document in this batch
    (``reason`` optional). Anything else raises ``MalformedResponseError`` --
    the whole reply, not just the bad element, because a reply that breaks the
    contract in one place is not evidence about the rest. An empty list is the
    only way to say "absent".
    """
    data = extract_json(content)
    occurrences = data["occurrences"]
    if not isinstance(occurrences, list):
        raise MalformedResponseError(
            f"'occurrences' is a {type(occurrences).__name__}, not a list", content)
    hits: list[CodeHit] = []
    for n, occ in enumerate(occurrences):
        if not isinstance(occ, dict):
            raise MalformedResponseError(
                f"occurrence {n} is a {type(occ).__name__}, not an object", content)
        title, quote = occ.get("document_title"), occ.get("quote")
        reason = occ.get("reason", "")
        if not isinstance(quote, str) or not quote.strip():
            raise MalformedResponseError(f"occurrence {n} has no quote", content)
        if not isinstance(title, str):
            raise MalformedResponseError(f"occurrence {n} has no document_title", content)
        resolved = _resolve_title(title.strip(), request.document_titles)
        if resolved is None:
            raise MalformedResponseError(
                f"occurrence {n} names {title!r}, which is not exactly one "
                f"document in this batch", content)
        hits.append(CodeHit(
            document_title=resolved,
            code_name=request.code_name,
            quote=quote.strip(),
            reason=reason.strip() if isinstance(reason, str) else str(reason),
        ))
    return hits
