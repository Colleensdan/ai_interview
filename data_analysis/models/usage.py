"""Per-call token usage, recorded so prompt caching can be verified.

Adapters discarded ``usage`` entirely, so an 85% saving from caching could be
claimed but never demonstrated -- and a silently broken cache (any byte change
ahead of the corpus invalidates the prefix) looks exactly like a working one.
``cached`` is the signal: if it stays at zero across a run, caching is not
engaging and something is invalidating the prefix.

Recording never raises. A usage field that a provider omits, or an object shaped
differently from what we expect, must not fail a coding call that succeeded.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

_lock = threading.Lock()
_records: list["Call"] = []


@dataclass(frozen=True)
class Call:
    model: str
    prompt: int = 0
    completion: int = 0
    cached: int = 0          # prefix served from cache (billed at ~0.1x)
    cache_write: int = 0     # prefix written to cache (billed at ~1.25x)
    reasoning: int = 0       # billed as output, never returned in the content


def record(model: str, *, prompt=0, completion=0, cached=0, cache_write=0,
           reasoning=0) -> None:
    with _lock:
        _records.append(Call(model, int(prompt or 0), int(completion or 0),
                             int(cached or 0), int(cache_write or 0),
                             int(reasoning or 0)))


def _num(obj, *names):
    """First present attribute/key among *names*, else 0. Tolerates any shape."""
    for n in names:
        v = getattr(obj, n, None)
        if v is None and isinstance(obj, dict):
            v = obj.get(n)
        if isinstance(v, (int, float)):
            return v
    return 0


def record_openai(model: str, usage) -> None:
    """OpenAI-shaped usage, SDK object or plain dict."""
    if usage is None:
        return
    try:
        def sub(name):
            d = getattr(usage, name, None)
            if d is None and isinstance(usage, dict):
                d = usage.get(name)
            return d
        pd, cd = sub("prompt_tokens_details"), sub("completion_tokens_details")
        # Reasoning tokens are billed as output but never appear in the returned
        # content, so nothing reconstructed from stored results can see them.
        # On gpt-5-mini they are the majority of output.
        record(model,
               prompt=_num(usage, "prompt_tokens", "input_tokens"),
               completion=_num(usage, "completion_tokens", "output_tokens"),
               cached=_num(pd, "cached_tokens") if pd is not None else 0,
               reasoning=_num(cd, "reasoning_tokens") if cd is not None else 0)
    except Exception:  # noqa: BLE001 - telemetry must never break a run
        pass


def record_anthropic(model: str, usage) -> None:
    if usage is None:
        return
    try:
        record(model,
               prompt=_num(usage, "input_tokens"),
               completion=_num(usage, "output_tokens"),
               cached=_num(usage, "cache_read_input_tokens"),
               cache_write=_num(usage, "cache_creation_input_tokens"))
    except Exception:  # noqa: BLE001
        pass


def calls() -> list[Call]:
    with _lock:
        return list(_records)


def reset() -> None:
    with _lock:
        _records.clear()


def summary() -> dict[str, dict]:
    """Per-model totals, plus the cache-hit rate on input tokens."""
    out: dict[str, dict] = {}
    for c in calls():
        t = out.setdefault(c.model, {"calls": 0, "prompt": 0, "completion": 0,
                                     "cached": 0, "cache_write": 0, "reasoning": 0})
        t["calls"] += 1
        for k in ("prompt", "completion", "cached", "cache_write", "reasoning"):
            t[k] += getattr(c, k)
    for t in out.values():
        billable = t["prompt"]
        t["cache_hit_rate"] = (t["cached"] / billable) if billable else 0.0
    return out
