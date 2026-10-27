"""Multi-model support: routing, the new transports, and the unchanged default.

The CJBS gateway fronts three wire protocols on one key, and eight of the ten
catalog models are not yet entitled on that key — so the parts entitlement
blocks from live testing (Anthropic request shape, response parsing, truncation)
are pinned here instead. The last group of tests is the important one: adding
models to the catalog must not change what an ordinary run does.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import config
import models
from models.base import CodingRequest, TruncatedResponseError
from models.catalog import ROUTE_ANTHROPIC, ROUTE_AZURE_DEPLOYMENT, ROUTE_OPENAI_V1
from models.gateway import GatewayAnthropicAdapter, GatewayOpenAIAdapter

REQUEST = CodingRequest(
    code_name="worry",
    code_description="feeling worried",
    merged_document="...",
    document_titles=("a.txt", "b.txt"),
)


# --- routing -----------------------------------------------------------------

@pytest.mark.parametrize("model_id,route,cls", [
    ("gpt-5-mini", ROUTE_AZURE_DEPLOYMENT, models.AzureOpenAIAdapter),
    ("gpt-5.2", ROUTE_OPENAI_V1, GatewayOpenAIAdapter),
    ("gpt-5.6-luna", ROUTE_OPENAI_V1, GatewayOpenAIAdapter),
    ("claude-fable-5", ROUTE_ANTHROPIC, GatewayAnthropicAdapter),
    ("claude-sonnet-4-6", ROUTE_ANTHROPIC, GatewayAnthropicAdapter),
    ("Mistral-Large-3", ROUTE_OPENAI_V1, GatewayOpenAIAdapter),
    ("mistral-small-2503", ROUTE_OPENAI_V1, GatewayOpenAIAdapter),
])
def test_each_model_routes_to_its_protocol(model_id, route, cls):
    assert models.get_spec(model_id).route == route
    assert isinstance(models.adapter_for(model_id), cls)


def test_model_ids_are_matched_case_insensitively():
    assert models.get_spec("MISTRAL-LARGE-3").model_id == "Mistral-Large-3"


def test_unknown_claude_model_still_routes_to_anthropic():
    # The gateway may gain models faster than the catalog does; an unrecognised
    # id must not silently be sent to the wrong protocol.
    assert models.get_spec("claude-something-new").route == ROUTE_ANTHROPIC
    assert models.get_spec("some-new-gpt").route == ROUTE_OPENAI_V1


def test_adapter_names_are_unique_and_filesystem_safe():
    names = [a.name for a in models.catalog_adapters()]
    assert len(names) == len(set(names))
    assert all(set(n) <= set("abcdefghijklmnopqrstuvwxyz"
                             "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.") for n in names)


# --- Anthropic transport (cannot be exercised live: not entitled) ------------

def _anthropic_returning(payload, stop_reason="end_turn"):
    adapter = GatewayAnthropicAdapter("claude-sonnet-4-6")
    text = payload if isinstance(payload, str) else json.dumps(payload)
    sent = {}

    def fake_post(body):
        sent.update(body)
        return {"content": [{"type": "text", "text": text}], "stop_reason": stop_reason}

    adapter._post = fake_post  # type: ignore[method-assign]
    return adapter, sent


def test_anthropic_sends_the_messages_api_shape():
    adapter, sent = _anthropic_returning({"occurrences": []})
    adapter.code_one(REQUEST)
    # system is a top-level field on this API, never a message with role system.
    assert sent["system"].startswith("You are an expert qualitative-coding")
    assert [m["role"] for m in sent["messages"]] == ["user"]
    assert sent["model"] == "claude-sonnet-4-6"
    assert sent["max_tokens"] == config.MAX_OUTPUT_TOKENS
    assert "max_completion_tokens" not in sent


def test_anthropic_parses_occurrences():
    adapter, _ = _anthropic_returning({"occurrences": [
        {"document_title": "a.txt", "quote": "I worry", "reason": "worried"},
    ]})
    hits = adapter.code_one(REQUEST)
    assert [(h.document_title, h.quote, h.code_name) for h in hits] == [
        ("a.txt", "I worry", "worry")
    ]


def test_anthropic_tolerates_a_fenced_json_block():
    # There is no server-side JSON mode on this route, so a ```json fence is the
    # most likely deviation and must not cost us the code's occurrences.
    adapter, _ = _anthropic_returning(
        '```json\n{"occurrences": [{"document_title": "b.txt", '
        '"quote": "q", "reason": "r"}]}\n```'
    )
    assert len(adapter.code_one(REQUEST)) == 1


def test_anthropic_max_tokens_stop_reason_raises_truncation():
    # Anthropic's equivalent of finish_reason == "length": treating it as an
    # empty answer would understate how often the code appears.
    adapter, _ = _anthropic_returning({"occurrences": []}, stop_reason="max_tokens")
    with pytest.raises(TruncatedResponseError):
        adapter.code_one(REQUEST)


def test_anthropic_does_not_retry_a_non_retryable_status(monkeypatch):
    calls = []

    class Resp:
        status_code, text = 403, '{"error":"ModelNotAllowed"}'

    def fake_post(url, headers, json, timeout):
        calls.append(headers)
        return Resp()

    monkeypatch.setattr("requests.post", fake_post)
    with pytest.raises(RuntimeError, match="403"):
        GatewayAnthropicAdapter("claude-fable-5").code_one(REQUEST)
    assert len(calls) == 1                      # a 403 is final, not transient
    assert "x-api-key" in calls[0]              # this route rejects api-key
    assert "anthropic-version" in calls[0]


# --- OpenAI-compatible transport --------------------------------------------

def _openai_v1_returning(content, finish_reason="stop"):
    adapter = GatewayOpenAIAdapter("gpt-5.2")
    text = content if isinstance(content, str) else json.dumps(content)
    adapter._create = lambda messages: SimpleNamespace(  # type: ignore[method-assign]
        choices=[SimpleNamespace(
            finish_reason=finish_reason,
            message=SimpleNamespace(content=text),
        )]
    )
    return adapter


def test_openai_v1_parses_like_the_azure_adapter():
    hits = _openai_v1_returning({"occurrences": [
        {"document_title": "a.txt", "quote": "q", "reason": "r"},
    ]}).code_one(REQUEST)
    assert len(hits) == 1 and hits[0].code_name == "worry"


def test_openai_v1_truncation_raises():
    with pytest.raises(TruncatedResponseError):
        _openai_v1_returning({"occurrences": []}, finish_reason="length").code_one(REQUEST)


def test_dropped_params_are_remembered_per_model_not_globally():
    # One model rejecting temperature must not reconfigure another's requests —
    # a shared flag would silently change what an unrelated model is sent.
    GatewayOpenAIAdapter._dropped.clear()
    GatewayOpenAIAdapter("gpt-5.2")._drop("temperature")
    assert "temperature" in GatewayOpenAIAdapter._dropped["gpt-5.2"]
    assert "gpt-5.2-chat" not in GatewayOpenAIAdapter._dropped
    GatewayOpenAIAdapter._dropped.clear()


def test_azure_adapter_deployment_override():
    a = models.AzureOpenAIAdapter("gpt-4.1-nano")
    assert a.deployment == "gpt-4.1-nano"
    assert a.name == "azure_openai-gpt-4.1-nano"
    # No argument still means "whatever CJBS_DEPLOYMENT_NAME says".
    assert models.AzureOpenAIAdapter().deployment == config.AZURE_DEPLOYMENT


# --- the default run must be untouched ---------------------------------------

def test_default_run_uses_only_the_configured_deployment(monkeypatch):
    monkeypatch.setattr(config, "SELECTED_MODELS", [])
    names = [a.name for a in models.available_adapters()]
    assert names == [f"azure_openai-{config.AZURE_DEPLOYMENT}"]


def test_registering_models_does_not_add_them_to_a_run(monkeypatch):
    monkeypatch.setattr(config, "SELECTED_MODELS", [])
    running = {a.name for a in models.available_adapters()}
    catalogued = {a.name for a in models.catalog_adapters()}
    assert len(catalogued) == 10
    assert running & catalogued == {f"azure_openai-{config.AZURE_DEPLOYMENT}"}


def test_selected_models_replaces_the_default_set(monkeypatch):
    monkeypatch.setattr(config, "SELECTED_MODELS", ["gpt-5-mini", "claude-sonnet-4-6"])
    assert [a.name for a in models.all_adapters()] == [
        "azure_openai-gpt-5-mini", "anthropic-claude-sonnet-4-6",
    ]


# --- two gateways ------------------------------------------------------------
#
# The newer models are entitled on a SECOND APIM subscription (n1ai) with its own
# endpoint and key, while gpt-5-mini stays on CJBS. The routes are identical, so
# what must be right is purely which credentials each adapter picks up — get that
# wrong and a model either 401s or, worse, silently queries the wrong tenant.

def test_catalog_splits_models_across_the_two_gateways():
    from models.catalog import CATALOG, GATEWAY_CJBS, GATEWAY_N1AI

    by_gw = {}
    for spec in CATALOG:
        by_gw.setdefault(spec.gateway, []).append(spec.model_id)
    # The incumbent deployment must not have been moved off CJBS.
    assert "gpt-5-mini" in by_gw[GATEWAY_CJBS]
    for model in ("gpt-5.2", "gpt-5.2-chat", "gpt-5.6-luna", "gpt-5.6-sol",
                  "claude-fable-5", "claude-sonnet-4-6",
                  "Mistral-Large-3", "mistral-small-2503"):
        assert model in by_gw[GATEWAY_N1AI], model


def test_gateway_specs_default_to_cjbs():
    """A spec written without a gateway keeps the original behaviour."""
    from models.catalog import GATEWAY_CJBS, ModelSpec

    assert ModelSpec("x", "y", ROUTE_OPENAI_V1).gateway == GATEWAY_CJBS


def test_adapters_resolve_credentials_from_their_own_gateway(monkeypatch):
    monkeypatch.setattr(config, "GATEWAYS", {
        "cjbs": ("https://cjbs/openai/v1", "https://cjbs/anthropic", "CJBS-KEY"),
        "n1ai": ("https://n1ai/openai/v1", "https://n1ai/anthropic", "N1AI-KEY"),
    })
    assert models.adapter_for("gpt-5.2")._creds == ("https://n1ai/openai/v1", "N1AI-KEY")
    assert models.adapter_for("claude-fable-5")._creds == ("https://n1ai/anthropic", "N1AI-KEY")
    # An OPENAI_V1 model left on CJBS still gets the CJBS credentials.
    from models.catalog import GATEWAY_CJBS, ModelSpec
    legacy = GatewayOpenAIAdapter("legacy", ModelSpec("legacy", "OpenAI",
                                                      ROUTE_OPENAI_V1,
                                                      gateway=GATEWAY_CJBS))
    assert legacy._creds == ("https://cjbs/openai/v1", "CJBS-KEY")


def test_unknown_gateway_falls_back_rather_than_raising():
    """A row naming an unconfigured gateway must not break the import path."""
    assert config.gateway_credentials("nope") == config.GATEWAYS["cjbs"]


def test_adapter_is_unavailable_when_its_gateway_has_no_key(monkeypatch):
    monkeypatch.setattr(config, "GATEWAYS", {
        "cjbs": ("https://cjbs/openai/v1", "https://cjbs/anthropic", "CJBS-KEY"),
        "n1ai": ("", "", None),
    })
    assert models.adapter_for("gpt-5.2").is_available() is False
    assert models.adapter_for("claude-fable-5").is_available() is False


def test_anthropic_adapter_drops_a_deprecated_temperature_and_retries(monkeypatch):
    """claude-fable-5 rejects `temperature` outright; the retry must not lose the call."""
    sent = []

    def fake_post(self, payload):
        sent.append(dict(payload))
        if "temperature" in payload:
            raise RuntimeError("HTTP 400: `temperature` is deprecated for this model.")
        return {"content": [{"type": "text", "text": json.dumps({"occurrences": []})}]}

    monkeypatch.setattr(GatewayAnthropicAdapter, "_post", fake_post)
    GatewayAnthropicAdapter._dropped.pop("claude-fable-5", None)
    adapter = models.adapter_for("claude-fable-5")
    req = CodingRequest("c", "d", "=== DOCUMENT: A ===\ntext\n", ("A",))
    assert adapter.code_one(req) == []
    assert "temperature" in sent[0] and "temperature" not in sent[1]

    # Remembered: the next call never sends it again.
    sent.clear()
    adapter.code_one(req)
    assert "temperature" not in sent[0]
    GatewayAnthropicAdapter._dropped.pop("claude-fable-5", None)


def test_a_non_temperature_error_is_not_swallowed_by_the_drop_retry(monkeypatch):
    def fake_post(self, payload):
        raise RuntimeError("HTTP 403: ModelNotAllowed")

    monkeypatch.setattr(GatewayAnthropicAdapter, "_post", fake_post)
    adapter = models.adapter_for("claude-sonnet-4-6")
    with pytest.raises(RuntimeError, match="ModelNotAllowed"):
        adapter.code_one(CodingRequest("c", "d", "=== DOCUMENT: A ===\nt\n", ("A",)))


# --- personal OpenAI key (USE_AZURE=false) ------------------------------------
#
# The same switch as code/interview.py. Off, the OpenAI models must go to
# api.openai.com on OPENAI_API_KEY and be labelled as such; n1ai and Claude rows
# must not move.

def _personal_key(monkeypatch, deployment="gpt-4o"):
    monkeypatch.setattr(config, "USE_AZURE", False)
    monkeypatch.setattr(config, "AZURE_DEPLOYMENT", deployment)
    monkeypatch.setitem(config.GATEWAYS, "openai",
                        ("https://api.openai.com/v1", "", "sk-test"))
    monkeypatch.setattr(config, "SELECTED_MODELS", [])


def test_personal_key_default_run_goes_to_openai(monkeypatch):
    _personal_key(monkeypatch)
    adapters = models.available_adapters()
    assert [a.name for a in adapters] == ["openai-gpt-4o"]
    assert adapters[0]._creds == ("https://api.openai.com/v1", "sk-test")


def test_personal_key_reroutes_azure_rows_and_unknown_ids(monkeypatch):
    _personal_key(monkeypatch)
    nano = models.adapter_for("gpt-4.1-nano")
    assert isinstance(nano, GatewayOpenAIAdapter) and nano.name == "openai-gpt-4.1-nano"
    # Catalog knowledge (gpt-5-mini rejects temperature) survives the reroute.
    assert models.adapter_for("gpt-5-mini").spec.supports_temperature is False
    assert models.adapter_for("gpt-4o-mini").name == "openai-gpt-4o-mini"


def test_personal_key_leaves_n1ai_and_claude_alone(monkeypatch):
    _personal_key(monkeypatch)
    assert models.adapter_for("gpt-5.2").name == "openai_v1-gpt-5.2"
    assert models.adapter_for("claude-sonnet-4-6").name == "anthropic-claude-sonnet-4-6"


def test_personal_key_without_a_key_is_unavailable(monkeypatch):
    _personal_key(monkeypatch)
    monkeypatch.setitem(config.GATEWAYS, "openai", ("https://api.openai.com/v1", "", None))
    assert models.available_adapters() == []


def test_personal_key_sends_no_azure_header(monkeypatch):
    _personal_key(monkeypatch)
    client = models.default_adapter()._get_client()
    assert "api-key" not in {k.lower() for k in client._custom_headers}
