"""Model-adapter registry.

``all_adapters()`` returns one instance of every known adapter; ``available_adapters()``
filters to those with credentials. The pipeline iterates over the available set,
so adding a model later is just adding it to ``_REGISTRY``.
"""

from __future__ import annotations

from .azure_openai import AzureOpenAIAdapter
from .base import CodeHit, CodingRequest, ModelAdapter
from .stubs import ClaudeAdapter, DeepSeekAdapter, GeminiAdapter, MistralAdapter

_REGISTRY: tuple[type[ModelAdapter], ...] = (
    AzureOpenAIAdapter,
    ClaudeAdapter,
    GeminiAdapter,
    DeepSeekAdapter,
    MistralAdapter,
)


def all_adapters() -> list[ModelAdapter]:
    return [cls() for cls in _REGISTRY]


def available_adapters() -> list[ModelAdapter]:
    return [a for a in all_adapters() if a.is_available()]


__all__ = [
    "CodeHit",
    "CodingRequest",
    "ModelAdapter",
    "AzureOpenAIAdapter",
    "ClaudeAdapter",
    "GeminiAdapter",
    "DeepSeekAdapter",
    "MistralAdapter",
    "all_adapters",
    "available_adapters",
]
