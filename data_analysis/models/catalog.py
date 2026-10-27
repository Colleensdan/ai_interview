"""Which models exist behind the CJBS gateway, and how each one is reached.

The CJBS endpoint is an Azure API Management gateway, not a bare Azure OpenAI
resource, and it fronts three different wire protocols on the SAME credentials:

  /openai/deployments/{name}/chat/completions   Azure OpenAI (api-key header)
  /openai/v1/chat/completions                   OpenAI-compatible (Bearer/api-key)
  /anthropic/v1/messages                        Anthropic Messages (x-api-key)

All three were confirmed live against the key in .env. Entitlement is separate
from routing: the gateway answers 403 ``ModelNotAllowed`` for any model the
subscription has not been granted, which is why a model can be listed here and
still be unusable today. ``check_models.py`` reports the real state.

Adding a model is one row in ``CATALOG`` — no adapter code changes.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Azure OpenAI deployment route — the path the pipeline has always used.
ROUTE_AZURE_DEPLOYMENT = "azure_deployment"
#: OpenAI-compatible route on the gateway (model named in the request body).
ROUTE_OPENAI_V1 = "openai_v1"
#: Anthropic-native Messages route on the gateway.
ROUTE_ANTHROPIC = "anthropic"

#: The original CJBS APIM subscription (CJBS_API_ENDPOINT / CJBS_API_KEY).
GATEWAY_CJBS = "cjbs"
#: The n1ai APIM subscription (N1AI_API_ENDPOINT / N1AI_API_KEY). Same two route
#: shapes as CJBS — what differs is which models the subscription is entitled to.
GATEWAY_N1AI = "n1ai"
#: OpenAI directly (OPENAI_API_KEY), used for the OpenAI models when USE_AZURE=false.
GATEWAY_OPENAI = "openai"


@dataclass(frozen=True)
class ModelSpec:
    """One callable model behind a gateway."""

    #: Model / deployment id exactly as the gateway expects it in the request.
    model_id: str
    #: Vendor, for reporting only.
    provider: str
    #: Which wire protocol above to speak.
    route: str
    #: False for reasoning models that reject ``temperature`` outright. This is
    #: only a starting guess — every adapter also drops a parameter the API
    #: rejects at runtime, so a wrong entry here self-corrects on the first call.
    supports_temperature: bool = True
    #: False where the provider has no server-side JSON mode (Anthropic).
    supports_json_mode: bool = True
    #: Which APIM subscription serves this model. Defaults to CJBS so every
    #: pre-existing row keeps its behaviour exactly.
    gateway: str = GATEWAY_CJBS


CATALOG: tuple[ModelSpec, ...] = (
    # --- the incumbent; unchanged, and still the only model used by default ---
    ModelSpec("gpt-5-mini", "OpenAI", ROUTE_AZURE_DEPLOYMENT, supports_temperature=False),
    ModelSpec("gpt-4.1-nano", "OpenAI", ROUTE_AZURE_DEPLOYMENT),
    # --- models entitled on the n1ai gateway ---------------------------------
    # Verified live 2026-09-06: all eight answer a real coding call. The two
    # Mistral models reject `max_completion_tokens` and need `max_tokens`; that
    # is detected and remembered at runtime by GatewayOpenAIAdapter rather than
    # declared here, so it self-corrects if the gateway's shim changes.
    ModelSpec("gpt-5.2", "OpenAI", ROUTE_OPENAI_V1,
              supports_temperature=False, gateway=GATEWAY_N1AI),
    ModelSpec("gpt-5.2-chat", "OpenAI", ROUTE_OPENAI_V1, gateway=GATEWAY_N1AI),
    ModelSpec("gpt-5.6-luna", "OpenAI", ROUTE_OPENAI_V1,
              supports_temperature=False, gateway=GATEWAY_N1AI),
    ModelSpec("gpt-5.6-sol", "OpenAI", ROUTE_OPENAI_V1,
              supports_temperature=False, gateway=GATEWAY_N1AI),
    ModelSpec("claude-fable-5", "Anthropic", ROUTE_ANTHROPIC,
              supports_json_mode=False, gateway=GATEWAY_N1AI),
    ModelSpec("claude-sonnet-4-6", "Anthropic", ROUTE_ANTHROPIC,
              supports_json_mode=False, gateway=GATEWAY_N1AI),
    ModelSpec("Mistral-Large-3", "Mistral", ROUTE_OPENAI_V1, gateway=GATEWAY_N1AI),
    ModelSpec("mistral-small-2503", "Mistral", ROUTE_OPENAI_V1, gateway=GATEWAY_N1AI),
)

_BY_ID = {spec.model_id.lower(): spec for spec in CATALOG}


def get_spec(model_id: str) -> ModelSpec:
    """Look a model up case-insensitively.

    An unknown id is not an error: the gateway may gain models faster than this
    file does. Unknown ids default to the OpenAI-compatible route, which is the
    one the gateway exposes for everything except Anthropic.
    """
    spec = _BY_ID.get(model_id.strip().lower())
    if spec is not None:
        return spec
    lowered = model_id.strip().lower()
    if lowered.startswith("claude"):
        return ModelSpec(model_id, "Anthropic", ROUTE_ANTHROPIC, supports_json_mode=False)
    return ModelSpec(model_id, "unknown", ROUTE_OPENAI_V1)


def model_ids() -> list[str]:
    return [spec.model_id for spec in CATALOG]
