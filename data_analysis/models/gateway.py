"""Adapters for the CJBS gateway's non-deployment routes.

Two protocols, one set of credentials (see models/catalog.py):

* :class:`GatewayOpenAIAdapter` — ``/openai/v1/chat/completions``. The
  OpenAI-compatible surface, where the model is named in the request body
  instead of the URL. Used for the newer GPT models and for Mistral, which the
  gateway serves through the same OpenAI-shaped API.
* :class:`GatewayAnthropicAdapter` — ``/anthropic/v1/messages``. The native
  Anthropic Messages API, which differs in three ways that matter here: it
  authenticates with ``x-api-key``, it takes the system prompt as a top-level
  ``system`` field rather than a message, and it has no server-side JSON mode.

Both produce the same CodeHit rows from the same prompt as the Azure adapter,
so results are comparable across models.
"""

from __future__ import annotations

import json
import time

import config
from .base import CodeHit, CodingRequest, ModelAdapter, TruncatedResponseError
from .catalog import ModelSpec, get_spec
from . import usage
from .prompting import SYSTEM_PROMPT, build_user_prompt, parse_occurrences


def _slug(model_id: str) -> str:
    """Filesystem-safe form of a model id, for CSV names and the DB column."""
    return "".join(c if c.isalnum() or c in "-_." else "-" for c in model_id)


class GatewayOpenAIAdapter(ModelAdapter):
    """Any model reachable over the gateway's OpenAI-compatible route."""

    def __init__(self, model_id: str, spec: ModelSpec | None = None) -> None:
        self.model_id = model_id
        self.spec = spec or get_spec(model_id)
        self.name = f"openai_v1-{_slug(model_id)}"
        self._client = None

    @property
    def _creds(self) -> tuple[str, str | None]:
        """(base_url, api_key) for whichever gateway serves this model."""
        openai_base, _, key = config.gateway_credentials(self.spec.gateway)
        return openai_base, key

    def is_available(self) -> bool:
        base, key = self._creds
        return bool(key and base)

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI

            base, key = self._creds
            # Both gateways accept the subscription key in an `api-key` header.
            # It is also passed as api_key so the client sends an Authorization
            # bearer: CJBS honours that form, n1ai ignores it in favour of the
            # header, and sending both keeps one client shape for both.
            self._client = OpenAI(
                api_key=key,
                base_url=base,
                timeout=config.REQUEST_TIMEOUT,
                max_retries=5,
                default_headers={"api-key": key or ""},
            )
        return self._client

    #: Parameters a given model has rejected, remembered per model id so one
    #: model's rejection never reconfigures another's requests.
    _dropped: dict[str, set[str]] = {}

    def _drop(self, param: str) -> None:
        type(self)._dropped.setdefault(self.model_id, set()).add(param)

    def _create(self, messages):
        """Call the model, dropping whatever parameter it rejects.

        Models on this route disagree about three parameters: reasoning models
        reject ``temperature``, some reject ``response_format``, and models
        served through a compatibility shim (Mistral) may only understand
        ``max_tokens`` rather than ``max_completion_tokens``. Rather than
        hardcode a matrix of who supports what — which goes stale the moment the
        gateway adds a model — each rejection is detected, dropped, and
        remembered. As in the Azure adapter the retry decision is based on what
        *this* attempt sent, so concurrent batches meeting the same rejection
        each still have something left to drop.
        """
        client = self._get_client()
        dropped = type(self)._dropped.get(self.model_id, set())
        sent_temperature = self.spec.supports_temperature and "temperature" not in dropped
        sent_response_format = self.spec.supports_json_mode and "response_format" not in dropped
        token_param = "max_tokens" if "max_completion_tokens" in dropped else "max_completion_tokens"
        while True:
            kwargs = {
                "model": self.model_id,
                "messages": messages,
                token_param: config.MAX_OUTPUT_TOKENS,
            }
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
                if "max_completion_tokens" in msg and token_param == "max_completion_tokens":
                    token_param = "max_tokens"
                    self._drop("max_completion_tokens")
                    continue
                raise

    def code_one(self, request: CodingRequest) -> list[CodeHit]:
        resp = self._create(
            [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_user_prompt(request)},
            ]
        )
        usage.record_openai(self.name, getattr(resp, "usage", None))
        choice = resp.choices[0]
        content = choice.message.content or ""
        if choice.finish_reason == "length":
            raise TruncatedResponseError(
                f"{request.code_name}: hit the {config.MAX_OUTPUT_TOKENS}-token "
                f"output limit across {len(request.document_titles)} document(s)"
            )
        return parse_occurrences(content, request)


class GatewayAnthropicAdapter(ModelAdapter):
    """Claude models over the gateway's native Anthropic Messages route."""

    #: Statuses worth retrying: rate limiting and transient gateway faults.
    _RETRY_STATUSES = (408, 409, 429, 500, 502, 503, 504)
    _MAX_ATTEMPTS = 5

    def __init__(self, model_id: str, spec: ModelSpec | None = None) -> None:
        self.model_id = model_id
        self.spec = spec or get_spec(model_id)
        self.name = f"anthropic-{_slug(model_id)}"

    @property
    def _creds(self) -> tuple[str, str | None]:
        """(base_url, api_key) for whichever gateway serves this model."""
        _, anthropic_base, key = config.gateway_credentials(self.spec.gateway)
        return anthropic_base, key

    def is_available(self) -> bool:
        base, key = self._creds
        return bool(key and base)

    def _post(self, payload: dict) -> dict:
        """POST to /v1/messages with backoff. Raises on a non-retryable error.

        Hand-rolled rather than using the Anthropic SDK: the gateway needs no
        vendor-specific behaviour beyond the header set, and requests is already
        a dependency, so this avoids adding an SDK to the deployment for one
        endpoint.
        """
        import requests

        base, key = self._creds
        url = f"{base.rstrip('/')}/v1/messages"
        headers = {
            # This route authenticates with x-api-key; api-key gets a 401.
            "x-api-key": key or "",
            "anthropic-version": config.ANTHROPIC_VERSION,
            "content-type": "application/json",
        }
        last_exc: Exception | None = None
        for attempt in range(self._MAX_ATTEMPTS):
            try:
                resp = requests.post(
                    url, headers=headers, json=payload,
                    timeout=config.REQUEST_TIMEOUT,
                )
            except requests.RequestException as exc:
                last_exc = exc
            else:
                if resp.status_code == 200:
                    return resp.json()
                if resp.status_code not in self._RETRY_STATUSES:
                    raise RuntimeError(
                        f"{self.name}: HTTP {resp.status_code} from the gateway: "
                        f"{resp.text[:300]}"
                    )
                last_exc = RuntimeError(
                    f"{self.name}: HTTP {resp.status_code}: {resp.text[:200]}"
                )
            if attempt < self._MAX_ATTEMPTS - 1:
                time.sleep(2 ** attempt)
        raise RuntimeError(f"{self.name}: giving up after {self._MAX_ATTEMPTS} "
                           f"attempts: {last_exc}")

    #: Parameters a given model has rejected, remembered per model id — the same
    #: self-correcting scheme as GatewayOpenAIAdapter. Newer Claude models
    #: deprecate ``temperature`` and reject the request outright rather than
    #: ignoring it, and which models do that changes faster than this file, so a
    #: rejection is detected once and then never re-sent.
    _dropped: dict[str, set[str]] = {}

    def _send(self, payload: dict) -> dict:
        """POST, dropping whatever parameter this model rejects."""
        while True:
            try:
                return self._post(payload)
            except RuntimeError as exc:
                msg = str(exc).lower()
                if "temperature" in msg and "temperature" in payload:
                    payload.pop("temperature")
                    type(self)._dropped.setdefault(self.model_id, set()).add("temperature")
                    continue
                raise

    def code_one(self, request: CodingRequest) -> list[CodeHit]:
        payload = {
            "model": self.model_id,
            "max_tokens": config.MAX_OUTPUT_TOKENS,
            # Anthropic takes the system prompt as a top-level field, not a
            # message with role "system".
            "system": SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": build_user_prompt(request)}],
        }
        if (self.spec.supports_temperature
                and "temperature" not in type(self)._dropped.get(self.model_id, set())):
            payload["temperature"] = 0
        data = self._send(payload)

        # There is no JSON mode on this API. The system prompt already demands a
        # bare JSON object, and parse_occurrences copes with a ```json fence or
        # surrounding prose, which is how a Claude model typically deviates.
        usage.record_anthropic(self.name, data.get("usage"))
        parts = data.get("content") or []
        content = "".join(
            p.get("text", "") for p in parts
            if isinstance(p, dict) and p.get("type") == "text"
        )
        # Anthropic's equivalent of finish_reason == "length".
        if data.get("stop_reason") == "max_tokens":
            raise TruncatedResponseError(
                f"{request.code_name}: hit the {config.MAX_OUTPUT_TOKENS}-token "
                f"output limit across {len(request.document_titles)} document(s)"
            )
        return parse_occurrences(content, request)
