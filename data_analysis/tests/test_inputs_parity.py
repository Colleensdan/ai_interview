"""Byte-based (RAM-only) loaders must produce IDENTICAL results to disk loaders.

If these ever diverge, the in-memory app would analyse different data than the
offline pipeline — a silent correctness/data risk. Uses the real sample inputs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import config
from app.inputs import InputStore
from pipeline.codebook import load_codebook, load_codebook_bytes
from pipeline.ground_truth import load_ground_truth_counts, load_ground_truth_counts_bytes
from pipeline.interviews import load_interviews, load_interviews_from_files

TEMPLATE = Path(config.TEMPLATE_ROOT)
pytestmark = pytest.mark.skipif(
    not TEMPLATE.is_dir(), reason="sample input data not present")


def test_codebook_bytes_matches_path():
    from_path = load_codebook(config.CODEBOOK_PATH)
    from_bytes = load_codebook_bytes(Path(config.CODEBOOK_PATH).read_bytes())
    assert from_bytes == from_path
    assert len(from_bytes) > 0


def test_ground_truth_counts_bytes_matches_path():
    a = load_ground_truth_counts(config.GROUND_TRUTH_COUNTS_PATH, config.GROUND_TRUTH_COUNTS_SHEET)
    b = load_ground_truth_counts_bytes(
        Path(config.GROUND_TRUTH_COUNTS_PATH).read_bytes(), config.GROUND_TRUTH_COUNTS_SHEET)
    assert a == b


def test_interviews_bytes_matches_path():
    from_path = load_interviews(config.INTERVIEWS_DIR)
    files = {p.name: p.read_bytes() for p in Path(config.INTERVIEWS_DIR).iterdir()
             if p.is_file()}
    from_bytes = load_interviews_from_files(files)
    assert [iv.title for iv in from_bytes] == [iv.title for iv in from_path]
    assert {iv.title: iv.text for iv in from_bytes} == {iv.title: iv.text for iv in from_path}
    assert len(from_bytes) == 14


def test_inputstore_memory_matches_disk_end_to_end():
    disk = InputStore(from_disk=True)
    mem = InputStore(from_disk=False)
    # Populate the memory store by routing every input file, as startup_sync will.
    base = TEMPLATE
    for f in base.rglob("*"):
        if f.is_file():
            mem.route(str(f.relative_to(base)), f.read_bytes())

    assert mem.codebook() == disk.codebook()
    assert mem.ground_truth_counts() == disk.ground_truth_counts()
    assert {iv.title: iv.text for iv in mem.interviews()} == \
           {iv.title: iv.text for iv in disk.interviews()}
    # GT quotes: compare sheet names resolve equally.
    assert mem.ground_truth_quotes_workbook().sheetnames == \
           disk.ground_truth_quotes_workbook().sheetnames
