"""Input-data provider (transcripts, codebook, ground truth).

Abstracts *where* the read-only input data comes from so the rest of the app is
identical in both modes:

* **File mode** (local dev / offline pipeline): parse from the configured
  on-disk paths, exactly as before.
* **Memory mode** (Render, data-privacy): hold the raw file bytes in process
  memory (downloaded from SharePoint at startup) and parse from those — the
  interview transcripts are never written to the container filesystem.

Interview data is the sensitive material, so in memory mode it lives only in RAM.
"""

from __future__ import annotations

import io
from pathlib import Path

import openpyxl

import config
from pipeline.codebook import Code, load_codebook, load_codebook_bytes
from pipeline.ground_truth import load_ground_truth_counts, load_ground_truth_counts_bytes
from pipeline.interviews import Interview, load_interviews, load_interviews_from_files


class InputStore:
    """Holds/serves the four inputs: interviews, codebook, GT counts, GT quotes."""

    def __init__(self, from_disk: bool | None = None):
        # Default: disk in file mode, in-memory bytes in memory mode.
        self._from_disk = (not config.MEMORY_DB) if from_disk is None else from_disk
        self._interview_files: dict[str, bytes] = {}
        self._codebook: bytes | None = None
        self._gt_counts: bytes | None = None
        self._gt_quotes: bytes | None = None

    # --- population (memory mode) ------------------------------------------
    def put_interview(self, name: str, data: bytes) -> None:
        self._interview_files[name] = data

    def set_codebook(self, data: bytes) -> None:
        self._codebook = data

    def set_gt_counts(self, data: bytes) -> None:
        self._gt_counts = data

    def set_gt_quotes(self, data: bytes) -> None:
        self._gt_quotes = data

    def route(self, rel_path: str, data: bytes) -> bool:
        """Store a downloaded file by its path relative to the SharePoint base.

        Returns True if the file was recognised as one of the four inputs.
        """
        name = Path(rel_path).name
        parts = {p.lower() for p in Path(rel_path).parts}
        interviews_dir = Path(config.INTERVIEWS_DIR).name.lower()
        if name.lower().endswith(".docx") and interviews_dir in parts:
            self.put_interview(name, data)
            return True
        if name == Path(config.CODEBOOK_PATH).name:
            self.set_codebook(data)
            return True
        if name == Path(config.GROUND_TRUTH_COUNTS_PATH).name:
            self.set_gt_counts(data)
            return True
        if name == Path(config.GROUND_TRUTH_QUOTES_PATH).name:
            self.set_gt_quotes(data)
            return True
        return False

    def has_interviews(self) -> bool:
        return self._from_disk or bool(self._interview_files)

    # --- parsed accessors --------------------------------------------------
    def interviews(self) -> list[Interview]:
        if self._from_disk:
            return load_interviews(config.INTERVIEWS_DIR)
        return load_interviews_from_files(self._interview_files)

    def codebook(self) -> list[Code]:
        if self._from_disk:
            return load_codebook(config.CODEBOOK_PATH)
        if self._codebook is None:
            raise RuntimeError("Codebook not loaded into memory.")
        return load_codebook_bytes(self._codebook)

    def ground_truth_counts(self) -> tuple[dict[str, dict[str, int]], list[str]]:
        if self._from_disk:
            return load_ground_truth_counts(
                config.GROUND_TRUTH_COUNTS_PATH, config.GROUND_TRUTH_COUNTS_SHEET)
        if self._gt_counts is None:
            raise RuntimeError("Ground-truth counts not loaded into memory.")
        return load_ground_truth_counts_bytes(self._gt_counts, config.GROUND_TRUTH_COUNTS_SHEET)

    def ground_truth_quotes_workbook(self):
        """Return an openpyxl (read-only) workbook of the GT quotes file."""
        if self._from_disk:
            return openpyxl.load_workbook(
                config.GROUND_TRUTH_QUOTES_PATH, data_only=True, read_only=True)
        if self._gt_quotes is None:
            raise RuntimeError("Ground-truth quotes not loaded into memory.")
        return openpyxl.load_workbook(
            io.BytesIO(self._gt_quotes), data_only=True, read_only=True)
