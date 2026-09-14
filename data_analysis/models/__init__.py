"""Model-adapter registry.

``all_adapters()`` returns one instance of every known adapter; ``available_adapters()``
filters to those with credentials. The pipeline iterates over the available set,
so adding a model later is just adding it to ``_REGISTRY``.

Beyond that default set, ``adapter_for()`` builds an adapter for any model in
``models.catalog`` — the newer GPT, Claude and Mistral models the CJBS gateway
fronts on the same credentials. Those are strictly opt-in: unless
``AICODE_MODELS`` names them, ``available_adapters()`` returns exactly what it
always has (the single ``CJBS_DEPLOYMENT_NAME`` deployment), so registering a
model does not change what a run does.
"""

from __future__ import annotations

import config

from .azure_openai import AzureOpenAIAdapter
from .base import CodeHit, CodingRequest, ModelAdapter
from .catalog import (
    CATALOG,
    ROUTE_ANTHROPIC,
    ROUTE_AZURE_DEPLOYMENT,
    ROUTE_OPENAI_V1,
    ModelSpec,
    get_spec,
    model_ids,
)
from .gateway import GatewayAnthropicAdapter, GatewayOpenAIAdapter
from .stubs import ClaudeAdapter, DeepSeekAdapter, GeminiAdapter, MistralAdapter

_REGISTRY: tuple[type[ModelAdapter], ...] = (
    AzureOpenAIAdapter,
    ClaudeAdapter,
    GeminiAdapter,
    DeepSeekAdapter,
    MistralAdapter,
)


def adapter_for(model_id: str) -> ModelAdapter:
    """Build the right adapter for one catalog model id.

    The route decides the adapter; the caller only needs the model's name.
    """
    spec = get_spec(model_id)
    if spec.route == ROUTE_ANTHROPIC:
        return GatewayAnthropicAdapter(spec.model_id, spec)
    if spec.route == ROUTE_AZURE_DEPLOYMENT:
        return AzureOpenAIAdapter(spec.model_id)
    return GatewayOpenAIAdapter(spec.model_id, spec)


def catalog_adapters() -> list[ModelAdapter]:
    """One adapter per catalog entry — for connectivity checks, not for runs."""
    return [adapter_for(spec.model_id) for spec in CATALOG]


def all_adapters() -> list[ModelAdapter]:
    # An explicit AICODE_MODELS list replaces the default registry outright, so
    # a multi-model run contains those models and nothing else.
    if config.SELECTED_MODELS:
        return [adapter_for(m) for m in config.SELECTED_MODELS]
    return [cls() for cls in _REGISTRY]


def available_adapters() -> list[ModelAdapter]:
    return [a for a in all_adapters() if a.is_available()]


__all__ = [
    "CodeHit",
    "CodingRequest",
    "ModelAdapter",
    "ModelSpec",
    "AzureOpenAIAdapter",
    "GatewayOpenAIAdapter",
    "GatewayAnthropicAdapter",
    "ClaudeAdapter",
    "GeminiAdapter",
    "DeepSeekAdapter",
    "MistralAdapter",
    "CATALOG",
    "ROUTE_ANTHROPIC",
    "ROUTE_AZURE_DEPLOYMENT",
    "ROUTE_OPENAI_V1",
    "get_spec",
    "model_ids",
    "adapter_for",
    "catalog_adapters",
    "all_adapters",
    "available_adapters",
]
