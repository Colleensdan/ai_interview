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


# --- speaker roles (patch 2) ------------------------------------------------
ROLE_INTERVIEWEE = "INTERVIEWEE"
ROLE_INTERVIEWER = "INTERVIEWER"


def _header_re() -> re.Pattern:
    """Match a turn header: a known speaker label at line start (e.g.
    'Speaker 2:', 'user:', 'assistant') with optional inline content."""
    labels = sorted(
        set(config.INTERVIEWEE_LABELS + config.INTERVIEWER_LABELS),
        key=len, reverse=True,
    )
    alts = [re.escape(l) for l in labels] + [r"speaker\s*\d+"]
    return re.compile(r"^\s*(" + "|".join(alts) + r")\s*(?::\s*(.*))?$", re.I)


_HEADER_RE = _header_re()


def classify_role(label: str) -> str | None:
    """Return the EXPLICIT role for a label, or None if the label is positional
    (e.g. 'Speaker 1') or a name — i.e. not a reliable role signal. Only
    explicit labels (user/assistant/interviewee/interviewer) drive role-coding."""
    l = label.strip().lower()
    if any(x in l for x in config.INTERVIEWER_LABELS):
        return ROLE_INTERVIEWER
    if any(x in l for x in config.INTERVIEWEE_LABELS):
        return ROLE_INTERVIEWEE
    return None


def parse_turns(text: str) -> list[tuple[str, str, str]]:
    """Split a transcript into (role, label, content) turns by speaker headers.

    If no speaker headers are found, the whole transcript is treated as one
    interviewee turn (so coding still works on unlabelled data)."""
    turns: list[tuple[str, str, str]] = []
    role, label, buf, started = ROLE_INTERVIEWEE, "", [], False
    for line in text.split("\n"):
        m = _HEADER_RE.match(line)
        if m:
            if started:
                turns.append((role, label, "\n".join(buf).strip()))
            label = m.group(1).strip()
            role = classify_role(label)
            inline = m.group(2) or ""
            buf = [inline] if inline.strip() else []
            started = True
        else:
            buf.append(line)
    if started:
        turns.append((role, label, "\n".join(buf).strip()))
    return turns or [(None, "", text.strip())]


def has_explicit_roles(text: str) -> bool:
    """True if the transcript uses explicit role labels (user/assistant/…),
    so role-coding can be applied reliably. Positional 'Speaker N'/name data
    returns False and is left un-restricted (all speakers coded)."""
    return any(role is not None for role, _, _ in parse_turns(text))


def annotate_roles(text: str) -> str:
    """Tag turns INTERVIEWEE/INTERVIEWER **only** when the transcript uses
    explicit role labels, so the model never codes the interviewer there.
    For positional/name-labelled transcripts (no reliable role signal), the
    text is returned unchanged and all speakers remain codeable. Content is kept
    verbatim, so returned quotes still match the raw transcript for highlighting.
    """
    if not has_explicit_roles(text):
        return text
    parts = []
    for role, label, content in parse_turns(text):
        tag = ("[INTERVIEWER — do NOT code this]" if role == ROLE_INTERVIEWER
               else "[INTERVIEWEE — you MAY code this]")
        head = f"{tag} {label}:" if label else tag
        parts.append(f"{head}\n{content}")
    return "\n\n".join(parts)


def merge_documents(interviews: list[Interview]) -> str:
    """Merge transcripts into one delimited document the LLM can parse.

    Each transcript is wrapped in clear BEGIN/END markers carrying its title,
    so the model can attribute every quote to the right file while we send the
    set in a single request (spec 4.1). Turns are tagged INTERVIEWEE/INTERVIEWER
    only for transcripts with explicit role labels, so the model codes only the
    interviewee there; positional/name-labelled transcripts are left untagged
    (all speakers coded).
    """
    blocks = []
    for iv in interviews:
        blocks.append(
            f"===== BEGIN DOCUMENT | title: {iv.title} =====\n"
            f"{annotate_roles(iv.text)}\n"
            f"===== END DOCUMENT | title: {iv.title} ====="
        )
    return "\n\n".join(blocks)
