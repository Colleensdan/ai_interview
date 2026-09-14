"""Retry behaviour for infrastructure faults during coding.

A dropped code is not a neutral missing value: the caller's fallback excludes it
from the run, so a model that lost three codes to a shared circuit breaker has
its mean kappa computed over a different, and probably easier, subset of codes
than the models it is being compared against. These tests pin down what counts
as transient and prove a real failure is still raised.
"""

from __future__ import annotations

import pytest

import config
from models.base import CodingRequest, TruncatedResponseError
from pipeline.coding import call_with_retry, is_transient

REQ = CodingRequest("c", "d", "=== DOCUMENT: A ===\ntext\n", ("A",))


class _Adapter:
    """Fails with *exc* for the first *n* calls, then succeeds."""

    def __init__(self, exc, n):
        self.exc, self.n, self.calls = exc, n, 0

    def code_one(self, req):
        self.calls += 1
        if self.calls <= self.n:
            raise self.exc
        return ["ok"]


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    monkeypatch.setattr("pipeline.coding.time.sleep", lambda _s: None)


@pytest.mark.parametrize("message", [
    "InternalServerError: is temporarily unavailable. The CircuitBreaker breakerRule is open.",
    "RateLimitError: 429 Too Many Requests",
    "HTTP 503: Service Unavailable",
    "HTTP 502: Bad Gateway",
    "ReadTimeout: request timed out",
    "ConnectionError: connection reset by peer",
])
def test_infrastructure_faults_are_transient(message):
    assert is_transient(RuntimeError(message)) is True


@pytest.mark.parametrize("message", [
    "BadRequestError: Unsupported parameter: 'temperature'",
    "403 ModelNotAllowed",
    "KeyError: 'occurrences'",
    "ValueError: could not parse the model's JSON",
])
def test_real_failures_are_not_transient(message):
    assert is_transient(RuntimeError(message)) is False


def test_truncation_is_never_transient():
    """It has its own batch-splitting handler; retrying would just re-truncate."""
    assert is_transient(TruncatedResponseError("too long")) is False


def test_retries_then_succeeds():
    a = _Adapter(RuntimeError("CircuitBreaker is open"), n=3)
    assert call_with_retry(a, REQ, note=lambda _m: None) == ["ok"]
    assert a.calls == 4


def test_gives_up_after_the_configured_attempts():
    a = _Adapter(RuntimeError("CircuitBreaker is open"), n=99)
    with pytest.raises(RuntimeError, match="CircuitBreaker"):
        call_with_retry(a, REQ, note=lambda _m: None, attempts=3)
    assert a.calls == 3


def test_a_permanent_error_is_raised_immediately_without_retrying():
    a = _Adapter(RuntimeError("403 ModelNotAllowed"), n=99)
    with pytest.raises(RuntimeError, match="ModelNotAllowed"):
        call_with_retry(a, REQ, note=lambda _m: None)
    assert a.calls == 1, "a permanent failure must not burn the retry budget"


def test_truncation_propagates_for_the_batch_splitter():
    a = _Adapter(TruncatedResponseError("too long"), n=99)
    with pytest.raises(TruncatedResponseError):
        call_with_retry(a, REQ, note=lambda _m: None)
    assert a.calls == 1


def test_backoff_is_jittered(monkeypatch):
    """Parallel workers must not all return the instant the breaker's window ends."""
    delays = []
    monkeypatch.setattr("pipeline.coding.time.sleep", delays.append)
    monkeypatch.setattr("pipeline.coding.random.random", lambda: 0.0)
    call_with_retry(_Adapter(RuntimeError("503"), n=2), REQ, note=lambda _m: None)
    lo = list(delays)
    delays.clear()
    monkeypatch.setattr("pipeline.coding.random.random", lambda: 1.0)
    call_with_retry(_Adapter(RuntimeError("503"), n=2), REQ, note=lambda _m: None)
    assert all(h > l for h, l in zip(delays, lo)), "jitter must widen the delay"


def test_code_batch_uses_the_retrying_path(monkeypatch):
    from pipeline import coding

    a = _Adapter(RuntimeError("CircuitBreaker is open"), n=2)
    monkeypatch.setattr(config, "TRANSIENT_RETRY_ATTEMPTS", 5)
    from pipeline.interviews import Interview
    docs = [Interview(title="A.txt", key="A", text="hello")]
    assert coding.code_batch(a, "c", "d", docs, note=lambda _m: None) == ["ok"]
    assert a.calls == 3


# --- quota ------------------------------------------------------------------
#
# Regression: _QUOTA_MARKERS was once defined but never referenced by the
# classifier, so quota errors were treated as permanent and 181 codes were
# dropped from a live run. The existing tests all passed, because none of them
# fed the classifier a quota string. These do.

@pytest.mark.parametrize("message", [
    "PermissionDeniedError: OpenAI token quota is already exceeded",
    "RuntimeError: HTTP 403 from the gateway: OpenAI token quota is already exceeded",
    "RateLimitError: You exceeded your current quota",
    "APIError: insufficient_quota",
])
def test_quota_is_retryable_not_permanent(message):
    from pipeline.coding import failure_kind

    assert failure_kind(RuntimeError(message)) == "quota"
    assert is_transient(RuntimeError(message)) is True


def test_quota_is_classified_separately_from_other_transients():
    """The two need different backoffs, so they must not collapse into one kind."""
    from pipeline.coding import failure_kind

    assert failure_kind(RuntimeError("CircuitBreaker is open")) == "transient"
    assert failure_kind(RuntimeError("quota is already exceeded")) == "quota"
    assert failure_kind(RuntimeError("403 ModelNotAllowed")) is None


def test_a_quota_failure_is_actually_retried():
    a = _Adapter(RuntimeError("OpenAI token quota is already exceeded"), n=2)
    assert call_with_retry(a, REQ, note=lambda _m: None) == ["ok"]
    assert a.calls == 3


def test_quota_backoff_is_longer_than_circuit_breaker_backoff(monkeypatch):
    """Retrying a spent allowance in 5s cannot help; a breaker often clears."""
    monkeypatch.setattr("pipeline.coding.random.random", lambda: 0.5)

    def delays_for(message):
        out = []
        monkeypatch.setattr("pipeline.coding.time.sleep", out.append)
        try:
            call_with_retry(_Adapter(RuntimeError(message), n=99), REQ,
                            note=lambda _m: None, attempts=3)
        except RuntimeError:
            pass
        return out

    quota = delays_for("OpenAI token quota is already exceeded")
    breaker = delays_for("CircuitBreaker is open")
    assert quota[0] > breaker[0], (quota, breaker)


def test_every_marker_list_is_reachable_from_the_classifier():
    """Guards the exact bug: a marker list defined but never consulted."""
    from pipeline.coding import _QUOTA_MARKERS, _TRANSIENT_MARKERS, failure_kind

    for marker in _TRANSIENT_MARKERS:
        assert failure_kind(RuntimeError(f"boom {marker.strip()} boom")) is not None, marker
    for marker in _QUOTA_MARKERS:
        assert failure_kind(RuntimeError(f"boom {marker} boom")) == "quota", marker
