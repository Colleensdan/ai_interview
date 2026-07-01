"""SharePoint ETag / If-Match optimistic-concurrency behaviour (HTTP mocked)."""

from __future__ import annotations

import pytest

from app import sharepoint_io as sp


class _Resp:
    def __init__(self, status_code, json_body=None, content=b""):
        self.status_code = status_code
        self._json = json_body if json_body is not None else {}
        self.content = content
        self.text = ""

    def json(self):
        return self._json


@pytest.fixture(autouse=True)
def _fake_headers(monkeypatch):
    # Bypass token + drive-id lookups.
    monkeypatch.setattr(sp, "_headers", lambda: ("DRIVE", {"Authorization": "Bearer x"}))


def test_upload_sends_if_match_and_returns_new_etag(monkeypatch):
    captured = {}

    def fake_put(url, headers=None, data=None, timeout=None):
        captured["headers"] = headers
        return _Resp(200, {"eTag": "NEW-ETAG"})

    monkeypatch.setattr(sp.requests, "put", fake_put)
    new = sp.upload_bytes("Test Data/state/coding.sqlite", b"abc", if_match="OLD-ETAG")
    assert captured["headers"]["If-Match"] == "OLD-ETAG"
    assert new == "NEW-ETAG"


def test_upload_without_if_match_omits_header(monkeypatch):
    captured = {}

    def fake_put(url, headers=None, data=None, timeout=None):
        captured["headers"] = headers
        return _Resp(201, {"eTag": "E1"})

    monkeypatch.setattr(sp.requests, "put", fake_put)
    sp.upload_bytes("Test Data/x", b"abc")
    assert "If-Match" not in captured["headers"]


def test_upload_412_raises_precondition_failed(monkeypatch):
    monkeypatch.setattr(sp.requests, "put", lambda *a, **k: _Resp(412))
    with pytest.raises(sp.PreconditionFailed):
        sp.upload_bytes("Test Data/x", b"abc", if_match="STALE")


def test_upload_other_error_raises_sharepoint_error(monkeypatch):
    monkeypatch.setattr(sp.requests, "put", lambda *a, **k: _Resp(500))
    with pytest.raises(sp.SharePointError):
        sp.upload_bytes("Test Data/x", b"abc")


def test_etag_returns_none_for_missing_item(monkeypatch):
    monkeypatch.setattr(sp.requests, "get", lambda *a, **k: _Resp(404))
    assert sp.etag("Test Data/state/coding.sqlite") is None


def test_download_with_etag_returns_both(monkeypatch):
    def fake_get(url, headers=None, timeout=None):
        if url.endswith(":/content"):
            return _Resp(200, content=b"DBBYTES")
        return _Resp(200, {"eTag": "E-DL"})

    monkeypatch.setattr(sp.requests, "get", fake_get)
    content, tag = sp.download_with_etag("Test Data/state/coding.sqlite")
    assert content == b"DBBYTES"
    assert tag == "E-DL"


def test_precondition_failed_is_a_sharepoint_error():
    # Callers that only catch SharePointError still behave safely.
    assert issubclass(sp.PreconditionFailed, sp.SharePointError)
