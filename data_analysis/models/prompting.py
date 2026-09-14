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

from .base import SCOPE_INTERVIEWER, CodeHit, CodingRequest

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
    return (
        f"CODE NAME: {request.code_name}\n"
        f"CODE DEFINITION: {request.code_description}\n"
        f"{scope}"
        "\n"
        f"Valid document titles: {list(request.document_titles)}\n\n"
        "INTERVIEW DOCUMENTS:\n"
        f"{request.merged_document}"
    )


def extract_json(text: str) -> dict:
    """Robustly pull the JSON object out of a model response.

    Handles bare JSON, ```json fenced blocks, and leading/trailing prose.
    """
    text = text.strip()
    # Strip a ```json ... ``` (or ``` ... ```) fence if present.
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Fall back to the outermost { ... } span.
    start, end = text.find("{"), text.rfind("}")
    span = text[start : end + 1] if (start != -1 and end > start) else text
    try:
        return json.loads(span)
    except json.JSONDecodeError:
        # Last resort: strip raw control characters that can appear unescaped
        # inside quoted transcript text and re-parse.
        cleaned = re.sub(r"[\x00-\x1f]", " ", span)
        return json.loads(cleaned)


def parse_occurrences(content: str, request: CodingRequest) -> list[CodeHit]:
    """Turn a model's raw reply into CodeHit rows.

    Never raises on a malformed reply: a model that answers badly should cost us
    one code's occurrences, not the whole run.
    """
    try:
        data = extract_json(content)
    except (ValueError, TypeError) as exc:
        print(f"    ! {request.code_name}: could not parse model response "
              f"({exc}); treating as no occurrences.")
        return []

    valid_titles = set(request.document_titles)
    hits: list[CodeHit] = []
    occurrences = data.get("occurrences", []) if isinstance(data, dict) else []
    if isinstance(occurrences, dict):      # a lone object instead of a list
        occurrences = [occurrences]
    elif not isinstance(occurrences, list):
        occurrences = []
    malformed = 0
    for occ in occurrences:
        # JSON mode guarantees valid JSON, not a particular shape: across a
        # few hundred calls the model occasionally emits a bare string in
        # place of an occurrence object. Skip those rather than crashing the
        # run — and rather than guessing which document they belong to,
        # since a wrongly attributed quote corrupts the count matrix.
        if not isinstance(occ, dict):
            malformed += 1
            continue
        title = str(occ.get("document_title", "")).strip()
        quote = str(occ.get("quote", "")).strip()
        reason = str(occ.get("reason", "")).strip()
        if not quote:
            continue
        # Snap an unrecognised title to a valid one if it is a clear
        # substring match; otherwise keep what the model said.
        if title not in valid_titles:
            match = next((t for t in valid_titles if title and title in t), None)
            if match:
                title = match
        hits.append(
            CodeHit(
                document_title=title,
                code_name=request.code_name,
                quote=quote,
                reason=reason,
            )
        )
    if malformed:
        print(f"    ! {request.code_name}: skipped {malformed} malformed "
              f"occurrence(s) that were not objects.", flush=True)
    return hits
