"""Live Azure OpenAI adapter (the ``/openai/deployments/...`` gateway route).

Client construction and the chat-completions call mirror code/interview.py and
code/bench_llm.py (AzureOpenAI + client.chat.completions.create with
max_completion_tokens). One call per code, returning structured JSON we parse
into CodeHit rows.

The deployment is a constructor argument so several deployments can be driven
side by side; it still defaults to ``config.AZURE_DEPLOYMENT``, so constructing
this adapter with no arguments behaves exactly as it always has.
"""

from __future__ import annotations

import config
from .base import CodeHit, CodingRequest, ModelAdapter, TruncatedResponseError
from .prompting import (
    SYSTEM_PROMPT as _SYSTEM_PROMPT,
    build_user_prompt,
    extract_json as _extract_json,
    parse_occurrences,
)

__all__ = ["AzureOpenAIAdapter"]


class AzureOpenAIAdapter(ModelAdapter):
    name = "azure_openai"

    def __init__(self, deployment: str | None = None) -> None:
        # Use the deployment name in the model column so results are
        # self-describing (e.g. "azure_openai-gpt-4o").
        self.deployment = deployment or config.AZURE_DEPLOYMENT
        if self.deployment:
            self.name = f"azure_openai-{self.deployment}"
        self._client = None

    def is_available(self) -> bool:
        return bool(
            config.AZURE_API_KEY
            and config.AZURE_ENDPOINT
            and self.deployment
        )

    def _get_client(self):
        if self._client is None:
            from openai import AzureOpenAI

            self._client = AzureOpenAI(
                api_key=config.AZURE_API_KEY,
                api_version=config.AZURE_API_VERSION,
                azure_endpoint=config.AZURE_ENDPOINT,
                timeout=config.REQUEST_TIMEOUT,
                # The SDK retries 429s with exponential backoff; batches now run
                # concurrently, so give it more room before surfacing an error.
                max_retries=5,
            )
        return self._client

    # Class-level so an unsupported param (e.g. gpt-5-mini rejects temperature)
    # is dropped once and not re-sent on every subsequent call. Keyed by
    # deployment: two deployments can disagree about what they accept, and one
    # model's rejection must not silently reconfigure another's requests.
    _dropped: dict[str, set[str]] = {}

    def _drop(self, param: str) -> None:
        type(self)._dropped.setdefault(self.deployment or "", set()).add(param)

    def _dropped_for_self(self) -> set[str]:
        return type(self)._dropped.get(self.deployment or "", set())

    def _create(self, messages):
        """Call chat.completions, gracefully degrading on unsupported params.

        Uses JSON mode so the API guarantees syntactically valid JSON (verbatim
        transcript quotes otherwise break the response with unescaped quotes /
        control characters). Falls back cleanly if a param is unsupported and
        remembers the result so later calls don't repeat the failed request.

        The retry decision is based on what *this* attempt actually sent, not on
        the shared flag. Batches run concurrently, so several threads meet the
        same rejection at once: reading the flag would let the first thread flip
        it and the rest conclude they had nothing left to drop, and the error
        would escape.
        """
        client = self._get_client()
        dropped = self._dropped_for_self()
        sent_temperature = "temperature" not in dropped
        sent_response_format = "response_format" not in dropped
        while True:
            kwargs = dict(
                model=self.deployment,
                messages=messages,
                max_completion_tokens=config.MAX_OUTPUT_TOKENS,
            )
            if sent_temperature:
                kwargs["temperature"] = 0
            if sent_response_format:
                kwargs["response_format"] = {"type": "json_object"}
            try:
                return client.chat.completions.create(**kwargs)
            except Exception as exc:  # noqa: BLE001
                msg = str(exc).lower()
                if "temperature" in msg and sent_temperature:
                    sent_temperature = False
                    self._drop("temperature")
                    continue
                if "response_format" in msg and sent_response_format:
                    sent_response_format = False
                    self._drop("response_format")
                    continue
                raise

    def code_one(self, request: CodingRequest) -> list[CodeHit]:
        resp = self._create(
            [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": build_user_prompt(request)},
            ]
        )
        choice = resp.choices[0]
        content = choice.message.content or ""
        # A truncated answer is not an empty answer. Surfacing it lets the
        # caller retry on fewer documents; swallowing it used to make a code
        # that appears everywhere look like a code that appears nowhere.
        if choice.finish_reason == "length":
            raise TruncatedResponseError(
                f"{request.code_name}: hit the {config.MAX_OUTPUT_TOKENS}-token "
                f"output limit across {len(request.document_titles)} document(s)"
            )
        return parse_occurrences(content, request)
