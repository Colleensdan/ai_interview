"""Canonical code-name handling, shared by every reader of an ATLAS.ti export.

The same code arrives spelled three different ways across the input files:

    Codebook.xlsx    ``anger\\\\/irritation``          (slashes backslash-escaped)
    Counts.xlsx      ``○ anger/irritation\\nGr=17``   (bullet prefix, Gr suffix)
    Quotations.xlsx  sheet ``anger irritation``      (slashes dropped, 31 chars)

Everything downstream joins on code names, so they are normalised in exactly one
place. Getting this wrong is silent: an unmatched code is treated as absent in
every document, which quietly produces a meaningless kappa rather than an error.
"""

from __future__ import annotations

import re

# ATLAS.ti prefixes code labels in the count matrix with a filled or hollow
# circle depending on whether the code is in a code group.
_BULLETS = "●○•‣▪◦"

# Trailing "Gr=NN" / "Gr=NN GS=NN" groundedness annotation on the first line.
_GR_RE = re.compile(r"\s*\bGr=\d+.*$")

# Backslashes escaping a forward slash in exported code names.
_ESCAPED_SLASH_RE = re.compile(r"\\+(?=/)")


def clean_code_name(label) -> str:
    """Return the canonical code name from any of the export spellings.

    >>> clean_code_name("○ anger/irritation\\nGr=17")
    'anger/irritation'
    >>> clean_code_name("anger\\\\\\\\/irritation")
    'anger/irritation'
    """
    if label is None:
        return ""
    text = str(label).split("\n", 1)[0]
    text = _GR_RE.sub("", text)
    text = text.lstrip(_BULLETS).strip()
    return _ESCAPED_SLASH_RE.sub("", text)


def groundedness(label) -> int | None:
    """Return the ``Gr=`` count on a code or document label, if present.

    This is ATLAS.ti's own count of quotations for that code/document. It is the
    only reliable way to tell apart quote sheets whose names collide after
    truncation, and a free cross-check that no quotations were lost on export.
    """
    if label is None:
        return None
    m = re.search(r"\bGr=(\d+)", str(label))
    return int(m.group(1)) if m else None


def norm_for_match(name: str) -> str:
    """Aggressively normalised form for comparing names across files."""
    return re.sub(r"[^a-z0-9]", "", str(name).lower())
