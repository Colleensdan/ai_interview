"""Live Azure OpenAI adapter.

Client construction and the chat-completions call mirror code/interview.py and
code/bench_llm.py (AzureOpenAI + client.chat.completions.create with
max_completion_tokens). One call per code, returning structured JSON we parse
into CodeHit rows.
"""

from __future__ import annotations

import json
import re

import config
from .base import CodeHit, CodingRequest, ModelAdapter

_SYSTEM_PROMPT = (
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


def _extract_json(text: str) -> dict:
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


class AzureOpenAIAdapter(ModelAdapter):
    name = "azure_openai"

    def __init__(self) -> None:
        # Use the deployment name in the model column so results are
        # self-describing (e.g. "azure_openai-gpt-4o").
        if config.AZURE_DEPLOYMENT:
            self.name = f"azure_openai-{config.AZURE_DEPLOYMENT}"
        self._client = None

    def is_available(self) -> bool:
        return bool(
            config.AZURE_API_KEY
            and config.AZURE_ENDPOINT
            and config.AZURE_DEPLOYMENT
        )

    def _get_client(self):
        if self._client is None:
            from openai import AzureOpenAI

            self._client = AzureOpenAI(
                api_key=config.AZURE_API_KEY,
                api_version=config.AZURE_API_VERSION,
                azure_endpoint=config.AZURE_ENDPOINT,
                timeout=config.REQUEST_TIMEOUT,
                max_retries=2,
            )
        return self._client

    # Class-level so an unsupported param (e.g. gpt-5-mini rejects temperature)
    # is dropped once and not re-sent on every subsequent call.
    _send_temperature = True
    _send_response_format = True

    def _create(self, messages):
        """Call chat.completions, gracefully degrading on unsupported params.

        Uses JSON mode so the API guarantees syntactically valid JSON (verbatim
        transcript quotes otherwise break the response with unescaped quotes /
        control characters). Falls back cleanly if a param is unsupported and
        remembers the result so later calls don't repeat the failed request.
        """
        client = self._get_client()
        while True:
            kwargs = dict(
                model=config.AZURE_DEPLOYMENT,
                messages=messages,
                max_completion_tokens=config.MAX_OUTPUT_TOKENS,
            )
            if type(self)._send_temperature:
                kwargs["temperature"] = 0
            if type(self)._send_response_format:
                kwargs["response_format"] = {"type": "json_object"}
            try:
                return client.chat.completions.create(**kwargs)
            except Exception as exc:  # noqa: BLE001
                msg = str(exc).lower()
                if "temperature" in msg and type(self)._send_temperature:
                    type(self)._send_temperature = False
                    continue
                if "response_format" in msg and type(self)._send_response_format:
                    type(self)._send_response_format = False
                    continue
                raise

    def code_one(self, request: CodingRequest) -> list[CodeHit]:
        user_prompt = (
            f"CODE NAME: {request.code_name}\n"
            f"CODE DEFINITION: {request.code_description}\n\n"
            f"Valid document titles: {list(request.document_titles)}\n\n"
            "INTERVIEW DOCUMENTS:\n"
            f"{request.merged_document}"
        )
        resp = self._create(
            [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ]
        )
        choice = resp.choices[0]
        content = choice.message.content or ""
        # Fail safe: a truncated (finish_reason="length") or malformed response
        # must not crash a multi-code run. Log and treat as "no occurrences".
        if choice.finish_reason == "length":
            print(f"    ! {request.code_name}: response hit token limit "
                  f"(finish_reason=length); treating as no occurrences.")
            return []
        try:
            data = _extract_json(content)
        except (ValueError, TypeError) as exc:
            print(f"    ! {request.code_name}: could not parse model response "
                  f"({exc}); treating as no occurrences.")
            return []

        valid_titles = set(request.document_titles)
        hits: list[CodeHit] = []
        for occ in data.get("occurrences", []):
            title = str(occ.get("document_title", "")).strip()
            quote = str(occ.get("quote", "")).strip()
            reason = str(occ.get("reason", "")).strip()
            if not quote:
                continue
            # Snap an unrecognised title to a valid one if it is a clear
            # substring match; otherwise keep what the model said.
            if title not in valid_titles:
                match = next(
                    (t for t in valid_titles if title and title in t), None
                )
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
        return hits
