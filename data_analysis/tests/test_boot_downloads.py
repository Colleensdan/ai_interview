"""Booting must not depend on downloading the app's own state, and must survive
a dropped connection.

2026-11-16: a deploy failed to start because one SharePoint download was reset
mid-transfer (requests' ChunkedEncodingError). Every boot was downloading the
whole data folder -- 344 MB in 149 files, almost all of it the results DB, its
daily snapshots and conflict copies -- to keep about 3 MB of inputs, and a
single reset anywhere stopped the app.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import requests

import config
from app import sharepoint_io as sp, startup_sync
from app.inputs import InputStore

from test_integration_memory import fake_env  # noqa: F401  (fixture)


def _state_files(fake):
    """The kinds of state that live beside the inputs on SharePoint."""
    for path in (f"{config.SHAREPOINT_STATE_DIR}/backups/2026-11-16/coding.sqlite",
                 f"{config.SHAREPOINT_STATE_DIR}/conflicts/coding_conflict.sqlite",
                 f"{config.SHAREPOINT_STATE_DIR}/export/code_interview_matrix.xlsx",
                 f"{config.SHAREPOINT_DIR}/state-staging/coding.sqlite"):
        fake.state[path] = (b"x" * 1000, "e0")


def test_boot_downloads_only_the_inputs_and_keeps_all_of_them(fake_env, monkeypatch):  # noqa: F811
    _state_files(fake_env)
    real = fake_env.download_bytes
    fetched = []
    monkeypatch.setattr(sp, "download_bytes", lambda p: fetched.append(p) or real(p))
    base = config.SHAREPOINT_DIR

    store = InputStore(from_disk=False)
    n = startup_sync._download_into_store(base, base, store)

    assert fetched and n == len(fetched)
    assert not [p for p in fetched if "state" in p.split("/")[1]], fetched
    # The same inputs as routing every file in the folder, as boot used to.
    everything = InputStore(from_disk=False)
    def walk(remote):
        for it in fake_env.list_folder(remote):
            if it["is_folder"]:
                walk(it["path"])
            else:
                everything.route(it["path"][len(base):].lstrip("/"), real(it["path"]))
    walk(base)
    assert store._interview_files == everything._interview_files
    assert (store._codebook, store._gt_counts, store._gt_quotes) == \
        (everything._codebook, everything._gt_counts, everything._gt_quotes)
    assert store._interview_files and store._codebook and store._gt_counts


def test_recognising_a_file_needs_only_its_path():
    s = InputStore(from_disk=False)
    chats = Path(config.INTERVIEWS_DIR).name
    assert s.recognises(f"{chats}/R_abc.txt")
    assert s.recognises(Path(config.GROUND_TRUTH_COUNTS_PATH).name)
    assert not s.recognises("state/backups/2026-11-16/coding.sqlite")
    assert not s.recognises("R_abc.txt")          # a transcript outside the chats folder


# --- retried reads ----------------------------------------------------------------

class _Resp:
    def __init__(self, status=200, content=b"ok", headers=None):
        self.status_code, self.content, self.text = status, content, ""
        self.headers = headers or {}


@pytest.fixture
def scripted_get(monkeypatch):
    monkeypatch.setattr(sp.time, "sleep", lambda _s: None)
    monkeypatch.setattr(sp, "_headers", lambda: ("drive", {}))
    calls = []

    def install(*outcomes):
        queue = list(outcomes)

        def get(url, headers=None, timeout=None):
            calls.append(url)
            item = queue.pop(0)
            if isinstance(item, BaseException):
                raise item
            return item
        monkeypatch.setattr(sp.requests, "get", get)
        return calls
    return install


def test_a_download_reset_mid_transfer_is_retried(scripted_get):
    reset = requests.exceptions.ChunkedEncodingError("Connection broken: reset by peer")
    calls = scripted_get(reset, requests.exceptions.ConnectionError("reset"), _Resp(content=b"data"))
    assert sp.download_bytes("AI-Interviewer-Data/Counts.xlsx") == b"data"
    assert len(calls) == 3


def test_a_transient_status_is_retried_but_a_missing_file_is_not(scripted_get):
    calls = scripted_get(_Resp(503), _Resp(content=b"data"))
    assert sp.download_bytes("x") == b"data" and len(calls) == 2
    calls = scripted_get(_Resp(404))
    with pytest.raises(sp.SharePointError):
        sp.download_bytes("x")
    assert len(calls) == 3                                 # 2 before + 1, no retry


def test_a_read_that_keeps_failing_still_fails(scripted_get):
    reset = requests.exceptions.ChunkedEncodingError("reset")
    calls = scripted_get(*[reset] * sp.READ_ATTEMPTS)
    with pytest.raises(requests.exceptions.ChunkedEncodingError):
        sp.download_bytes("x")
    assert len(calls) == sp.READ_ATTEMPTS
