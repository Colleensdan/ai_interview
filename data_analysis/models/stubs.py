"""Stub adapters for models we don't have keys for yet.

Claude, Gemini, DeepSeek and Mistral all plug in here. Each declares the env
var that will hold its key; until that var is set, ``is_available()`` is False
and the registry skips it. To bring one live, fill in ``code_one`` with the
vendor call (same CodeHit contract as AzureOpenAIAdapter) — nothing else in the
pipeline changes, and majority voting starts counting it as another voter.
"""

from __future__ import annotations

import os

from .base import CodeHit, CodingRequest, ModelAdapter


class _StubAdapter(ModelAdapter):
    #: Env var that will hold this provider's API key once available.
    key_env: str = ""

    def is_available(self) -> bool:
        return bool(self.key_env and os.getenv(self.key_env))

    def code_one(self, request: CodingRequest) -> list[CodeHit]:
        raise NotImplementedError(
            f"{self.name} is not implemented yet. Set {self.key_env} and "
            f"implement {self.__class__.__name__}.code_one to enable it."
        )


class ClaudeAdapter(_StubAdapter):
    name = "claude"
    key_env = "ANTHROPIC_API_KEY"


class GeminiAdapter(_StubAdapter):
    name = "gemini"
    key_env = "GEMINI_API_KEY"


class DeepSeekAdapter(_StubAdapter):
    name = "deepseek"
    key_env = "DEEPSEEK_API_KEY"


class MistralAdapter(_StubAdapter):
    name = "mistral"
    key_env = "MISTRAL_API_KEY"
