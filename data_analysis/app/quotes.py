"""Locate coded quotes inside a transcript so the UI can highlight them.

Both LLM quotes (verbatim from the merged doc) and human ground-truth quotes
(ATLAS export, with U+2029 line separators and interleaved speaker labels) are
matched into the displayed transcript text by a whitespace-insensitive search,
with a fuzzy fallback. We return character spans in the *original* text so the
frontend can wrap exactly those ranges.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Span:
    start: int
    end: int


def build_norm(text: str) -> tuple[str, list[int]]:
    """Public helper so callers can precompute (and cache) per transcript."""
    return _normalize_with_map(text)


def _normalize_with_map(text: str) -> tuple[str, list[int]]:
    """Lowercase + collapse all whitespace, keeping a map back to original idx."""
    out: list[str] = []
    idx_map: list[int] = []
    prev_space = False
    for i, ch in enumerate(text):
        if ch.isspace() or ch == " ":
            if prev_space:
                continue
            out.append(" ")
            idx_map.append(i)
            prev_space = True
        else:
            out.append(ch.lower())
            idx_map.append(i)
            prev_space = False
    return "".join(out), idx_map


def _normalize(text: str) -> str:
    return " ".join(text.lower().replace(" ", " ").split())


def locate(transcript: str, quote: str, norm: tuple[str, list[int]] | None = None,
           anchor_chars: int = 40) -> Span | None:
    """Return the character span of *quote* within *transcript*, or None.

    Whitespace-insensitive exact match first; if that fails, an O(n) anchor
    match — locate the quote's opening fragment and span the quote's length from
    there (handles minor transcription drift cheaply, avoiding an expensive
    full fuzzy diff so the endpoint stays fast with many codes). Pass a cached
    ``norm`` (from :func:`build_norm`) to avoid re-normalising per call.
    """
    norm_t, idx_map = norm if norm is not None else _normalize_with_map(transcript)
    norm_q = _normalize(quote)
    if not norm_q:
        return None

    pos = norm_t.find(norm_q)
    if pos != -1:
        return _map_span(idx_map, pos, pos + len(norm_q))

    # Anchor fallback: find the opening fragment, span the quote's length.
    if len(norm_q) > anchor_chars:
        anchor = norm_q[:anchor_chars]
        pos = norm_t.find(anchor)
        if pos != -1:
            end = min(pos + len(norm_q), len(norm_t))
            return _map_span(idx_map, pos, end)
    return None


def _map_span(idx_map: list[int], n_start: int, n_end: int) -> Span:
    n_end = min(n_end, len(idx_map))
    start = idx_map[n_start]
    end = idx_map[n_end - 1] + 1
    return Span(start=start, end=end)


def merge_overlaps(spans: list[tuple[int, int, dict]]) -> list[tuple[int, int, list[dict]]]:
    """Group overlapping spans so the frontend can render nested highlights.

    Input: (start, end, payload). Output: non-overlapping segments, each with the
    list of payloads (codes) covering it.
    """
    if not spans:
        return []
    # Boundary sweep.
    points = sorted({p for s, e, _ in spans for p in (s, e)})
    segments: list[tuple[int, int, list[dict]]] = []
    for a, b in zip(points, points[1:]):
        covering = [pl for s, e, pl in spans if s <= a and e >= b]
        if covering:
            segments.append((a, b, covering))
    return segments
