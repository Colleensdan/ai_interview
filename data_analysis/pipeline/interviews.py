"""Read interview transcripts, select a sample, and merge into one document.

Transcripts are .docx today (local). The source directory is configurable so it
can later point at processed/translated SharePoint data. ``.Identifier`` /
``:Zone.Identifier`` sidecar files are always ignored.
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass
from pathlib import Path

import docx

import config


@dataclass(frozen=True)
class Interview:
    title: str  # filename, e.g. "13876S.MP3.docx" — used as the document title
    key: str    # canonical join key (leading digits), e.g. "13876"
    text: str


def _doc_key(filename: str) -> str:
    """Canonical document key = leading digits of the filename.

    Joins LLM output (keyed by filename) with the human ground-truth matrix
    (whose columns are ATLAS.ti doc names like "13876S"). The 5-digit numeric
    prefix is the stable identifier across both. Falls back to the stem.
    """
    m = re.match(r"(\d+)", filename)
    return m.group(1) if m else Path(filename).stem


def _is_ignored(name: str) -> bool:
    return any(name.endswith(suf) for suf in config.IGNORE_SUFFIXES)


def _read_docx(path: Path) -> str:
    document = docx.Document(str(path))
    return "\n".join(p.text for p in document.paragraphs if p.text.strip())


def load_interviews(directory: str | Path) -> list[Interview]:
    """Load all .docx transcripts in *directory* (sorted, .Identifier ignored)."""
    directory = Path(directory)
    interviews: list[Interview] = []
    for path in sorted(directory.iterdir()):
        if not path.is_file() or _is_ignored(path.name):
            continue
        if path.suffix.lower() != ".docx":
            continue
        interviews.append(
            Interview(
                title=path.name,
                key=_doc_key(path.name),
                text=_read_docx(path),
            )
        )
    return interviews


def select_sample(
    interviews: list[Interview],
    fraction: float = config.SAMPLE_FRACTION,
    seed: int = config.RANDOM_SEED,
) -> list[Interview]:
    """Randomly select ``fraction`` of interviews (seeded, reproducible).

    Rounds to the nearest whole transcript with a floor of 1.
    """
    n = max(1, round(len(interviews) * fraction))
    rng = random.Random(seed)
    chosen = rng.sample(interviews, n)
    # Return in stable (title) order for deterministic, readable output.
    return sorted(chosen, key=lambda i: i.title)


def merge_documents(interviews: list[Interview]) -> str:
    """Merge transcripts into one delimited document the LLM can parse.

    Each transcript is wrapped in clear BEGIN/END markers carrying its title,
    so the model can attribute every quote to the right file while we send the
    set in a single request (spec 4.1).
    """
    blocks = []
    for iv in interviews:
        blocks.append(
            f"===== BEGIN DOCUMENT | title: {iv.title} =====\n"
            f"{iv.text}\n"
            f"===== END DOCUMENT | title: {iv.title} ====="
        )
    return "\n\n".join(blocks)
