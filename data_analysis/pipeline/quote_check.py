"""Is a model's quote really in the interview it says it came from?

A model codes 25 interviews per request and names, for every occurrence, the
interview its quote comes from. Nothing used to check that name against the
transcript, and a model sometimes reuses a sentence from one interview as the
evidence for another interview in the same request -- typically a standard
closing message borrowed by an interview that ended early ("finished"), or a
neighbour's answer on a similar theme. Across all 25,400 stored occurrences
(2026-11-16) 86 failed this check: 76 were verbatim from another interview of
the same batch, 8 were short quotes ("4", "don't know") found only elsewhere,
and 2 were found in no interview at all. The model is not mistyping the ID: in
nearly every case it had already coded the true source correctly in the same
reply.

The check has to tolerate the ways models legitimately quote, or it would throw
away real evidence. Every one of these occurs in stored replies and is accepted:

* case, punctuation and spacing differences;
* answers from several turns joined into one quote, or shortened with "...";
* a word or two changed, or a clause repeated;
* apostrophes and dashes returned as stray control characters (gpt-5-mini).

Calibrated on the stored replies: the rules below reject exactly those 86 and
accept every other stored occurrence. Bump ``QUOTE_CHECK_VERSION`` whenever they change; it
is recorded with every run and checked on resume.
"""

from __future__ import annotations

import difflib
import re

#: Recorded in each run's provenance (``must_match``).
#:   1 -- 2026-11-16: the rules in :func:`quote_in_text`.
QUOTE_CHECK_VERSION = 1

#: Below this many letters/digits a quote is too short to be matched loosely
#: ("4", "Yes.", "I don't know"): it must appear exactly, as whole words.
SHORT_QUOTE_CHARS = 12
FRAGMENT_COVERAGE = 0.80   # share of the quote's sentences/turns found
FOUR_GRAM_COVERAGE = 0.80  # share of its 4-word sequences found
IN_ORDER_COVERAGE = 0.85   # share of its characters found in order ...
IN_ORDER_MIN_RUN = 8       # ... counting only runs of at least this many

# A run of control characters, with the digits/whitespace a broken encoding
# leaves around them ("I\x039m", "No idea \x0e2\n0\x0e2\n0\x1a the"), is a
# corrupted apostrophe or dash: read it as a gap, never as text.
_GARBLED = re.compile(r"(?:[\x00-\x08\x0b\x0c\x0e-\x1f][\d\s]*)+")
_ROLE_TAG = re.compile(r"\[(?:interviewer|interviewee)[^\]]*\]")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")
_FRAGMENT_BREAK = re.compile(r"(?<=[.?!])\s+|\n+|\.\.\.|…|\[\.\.\.\]|\[…\]")


def _degarble(s: str) -> str:
    return _GARBLED.sub("'", s or "")


def _words(s: str) -> str:
    """Lower-case letters and digits only, single-spaced, role tags removed."""
    s = _ROLE_TAG.sub(" ", _degarble(s).lower())
    return " ".join(_NON_ALNUM.sub(" ", s).split())


def _spaced(s: str) -> str:
    """Whitespace-collapsed lower-case text, punctuation kept (for "????")."""
    return " ".join(_degarble(s).replace("’", "'").lower().split())


class Transcript:
    """A transcript normalised once, for checking many quotes against it."""

    __slots__ = ("words", "padded", "spaced")

    def __init__(self, text: str):
        self.words = _words(text)
        self.padded = f" {self.words} "
        self.spaced = _spaced(text)


def quote_in_text(quote: str, transcript: str | Transcript) -> bool:
    """True if *quote* can be located in *transcript* (see module docstring)."""
    t = transcript if isinstance(transcript, Transcript) else Transcript(transcript)
    q = _words(quote)
    if not q:
        # Nothing but punctuation ("????????????"): compare the text as written.
        s = _spaced(quote)
        return bool(s) and s in t.spaced
    if len(q.replace(" ", "")) < SHORT_QUOTE_CHARS:
        return f" {q} " in t.padded
    if q in t.words:
        return True

    fragments = [f for f in (_words(p) for p in _FRAGMENT_BREAK.split(_degarble(quote))) if f]
    total = sum(len(f) for f in fragments)
    if total and sum(len(f) for f in fragments if f in t.words) / total >= FRAGMENT_COVERAGE:
        return True

    words = q.split()
    grams = [" ".join(words[i:i + 4]) for i in range(len(words) - 3)]
    if grams and sum(f" {g} " in t.padded for g in grams) / len(grams) >= FOUR_GRAM_COVERAGE:
        return True

    blocks = difflib.SequenceMatcher(None, q, t.words, autojunk=False).get_matching_blocks()
    in_order = sum(b.size for b in blocks if b.size >= IN_ORDER_MIN_RUN)
    return in_order / len(q) >= IN_ORDER_COVERAGE
