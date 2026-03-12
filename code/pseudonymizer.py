"""Utilities for pseudonymising interview transcripts before persistence."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Set, Tuple


# Mapping derived from spaCy entity labels to placeholders requested by governance.
PLACEHOLDER_BY_LABEL = {
    "PERSON": "<name>",
    "ORG": "<organisation>",
    "GPE": "<place>",
    "LOC": "<place>",
    "NORP": "<group>",
}


GENERIC_FACILITY_TERMS = {
    "airport",
    "airfield",
    "clinic",
    "factory",
    "gym",
    "harbor",
    "harbour",
    "hospital",
    "lab",
    "laboratory",
    "laboratory",
    "library",
    "office",
    "plant",
    "port",
    "school",
    "station",
    "terminal",
    "university",
    "warehouse",
    "field",
}


FACILITY_TYPE_KEYWORDS = {
    "airport": {"airport", "airfield", "air base", "air force base"},
    "campus": {"campus"},
    "factory": {"factory", "plant"},
    "harbor": {"harbor", "harbour", "port"},
    "hospital": {"hospital", "clinic", "infirmary", "medical center", "medical centre"},
    "lab": {"lab", "laboratory"},
    "library": {"library"},
    "office": {"office", "headquarters", "hq", "tower", "center", "centre"},
    "school": {"school", "academy", "elementary", "high school"},
    "station": {"station", "terminal", "depot"},
    "stadium": {"stadium", "arena", "field"},
    "university": {"university", "college", "institute"},
}


DATE_YEAR_PATTERN = re.compile(r"\b(1[89]\d{2}|20\d{2}|21\d{2})\b")


# Named calendar terms that make a DATE entity specific enough to pseudonymise.
# Includes both English and German terms so the filter works with de_core_news_*.
SPECIFIC_DATE_WORDS: frozenset = frozenset({
    # English months
    "january", "february", "march", "april", "june", "july",
    "august", "september", "october", "november", "december",
    "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept",
    "oct", "nov", "dec",
    # English weekdays
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "mon", "tue", "wed", "thu", "fri", "sat", "sun",
    # German months
    "januar", "februar", "märz", "mai", "juni", "juli",
    "oktober",
    # German weekdays
    "montag", "dienstag", "mittwoch", "donnerstag", "freitag", "samstag", "sonntag",
    "mo", "di", "mi", "do", "fr", "sa", "so",
})


WORD_START_PATTERN = re.compile(r"\b[a-z][a-z'\-]*")


EMAIL_PATTERN = re.compile(
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
)


NAME_FALLBACK_PATTERN = re.compile(
    r"\b(?:my\s+name\s+is|name\s*[:=]"
    r"|mein\s+name\s+ist|ich\s+hei[sß]e|ich\s+bin)\s+"
    r"(?P<name>[A-Za-z\u00C0-\u024F][A-Za-z\u00C0-\u024F'\-]*"
    r"(?:\s+[A-Za-z\u00C0-\u024F][A-Za-z\u00C0-\u024F'\-]*){0,3})",
    re.IGNORECASE,
)


_DEFAULT_PHRASES_DIR = Path(__file__).parent / "psudoanonymised-phrases"

# Splits a template line into alternating literal-text / placeholder tokens.
# e.g. "My name is <name>" → ["My name is ", "<name>", ""]
_TEMPLATE_SPLIT_RE = re.compile(r"(<[^>]+>)")

# Trailing capture group: up to 5 whitespace-separated tokens, no sentence
# punctuation, so "My name is Alice Smith" captures "Alice Smith" not the rest.
_TRAILING_CAPTURE = r"([^\s,\.!?;\n]+(?:\s+[^\s,\.!?;\n]+){0,4})"

# Inner capture group (placeholder has something after it in the template):
# lazy so it stops as soon as the next literal fragment can match.
_INNER_CAPTURE = r"(.+?)"


def _parse_template(
    line: str,
) -> "Optional[Tuple[re.Pattern[str], List[str], List[str]]]":
    """Convert a template line into a compiled regex plus part lists.

    Returns ``(pattern, text_parts, placeholders)`` or ``None`` if the line
    contains no placeholder tokens.

    *text_parts* are the literal fragments between placeholders (len = len(placeholders)+1).
    *placeholders* are the ``<...>`` tokens in order.
    """
    parts = _TEMPLATE_SPLIT_RE.split(line)
    text_parts: List[str] = parts[0::2]   # indices 0, 2, 4, …
    ph_parts: List[str] = parts[1::2]     # indices 1, 3, 5, …

    if not ph_parts:
        return None

    pattern_str = ""
    for i, text in enumerate(text_parts):
        pattern_str += re.escape(text)
        if i < len(ph_parts):
            is_last = i == len(ph_parts) - 1
            pattern_str += _TRAILING_CAPTURE if is_last else _INNER_CAPTURE

    try:
        return re.compile(pattern_str, re.IGNORECASE), text_parts, ph_parts
    except re.error:
        return None


class PhrasePseudonymizer:
    """Redact PII using trigger-phrase templates loaded from a directory.

    This is the default pseudonymisation strategy.  It requires no external NLP
    models.  Each ``.txt`` file in *phrases_dir* should contain one template per
    line, e.g.::

        My name is <name>
        I work at <organisation>

    When a template matches in user input the captured value is replaced by the
    placeholder token (``<name>``, ``<organisation>``, etc.).  Matching is
    case-insensitive.  Blank lines and lines starting with ``#`` are ignored.
    """

    def __init__(self, phrases_dir: Optional[Path] = None) -> None:
        self._phrases_dir = phrases_dir or _DEFAULT_PHRASES_DIR
        self._rules: "List[Tuple[re.Pattern[str], List[str], List[str]]]" = []
        self._load()
        self._observed: "List[EntityMapping]" = []

    def _load(self) -> None:
        """Load all template rules from every ``.txt`` file in the phrases directory."""
        self._rules = []
        if not self._phrases_dir.is_dir():
            return
        for txt_file in sorted(self._phrases_dir.glob("*.txt")):
            for raw in txt_file.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                result = _parse_template(line)
                if result:
                    self._rules.append(result)

    def reset(self) -> None:
        """Clear per-session observations so each interview starts clean."""
        self._observed = []

    def pseudonymize(self, text: str) -> str:
        """Return *text* with template-matched values replaced by their placeholders."""
        if not text or not self._rules:
            return text
        for pattern, text_parts, ph_parts in self._rules:
            text = pattern.sub(self._make_repl(text_parts, ph_parts), text)
        return text

    def _make_repl(
        self, text_parts: "List[str]", ph_parts: "List[str]"
    ) -> "Callable[[re.Match], str]":
        """Return a replacement function that records observations and rebuilds the text."""
        observed = self._observed

        def _repl(m: re.Match) -> str:
            result = text_parts[0]
            for i, ph in enumerate(ph_parts):
                captured = m.group(i + 1)
                observed.append(EntityMapping(captured.strip(), ph, "PHRASE"))
                result += ph + text_parts[i + 1]
            return result

        return _repl

    def export_mappings(self) -> "List[EntityMapping]":
        return list(self._observed)


@dataclass
class EntityMapping:
    """Record of how a specific entity was pseudonymised."""

    original: str
    placeholder: str
    label: str


def _load_spacy_model(preferred_names: Sequence[str] | None = None) -> "Language":
    """Return a spaCy German model, raising a clear error if none are installed."""

    import spacy  # lazy — only needed when InterviewPseudonymizer is used
    from spacy.language import Language  # noqa: F401 (used for return annotation only)

    names: Iterable[str]
    if preferred_names:
        names = preferred_names
    else:
        names = ("de_core_news_sm", "de_core_news_md", "de_core_news_lg")

    for name in names:
        try:
            return spacy.load(name)
        except OSError:
            continue

    raise RuntimeError(
        "No spaCy German model is installed. Run 'python -m spacy download de_core_news_sm' "
        "to enable interview text pseudonymisation."
    )


def _get_spacy_stopwords() -> Set[str]:
    """Return German stop-words from spaCy (lazy import)."""
    from spacy.lang.de.stop_words import STOP_WORDS  # type: ignore[import]
    return {word.lower() for word in STOP_WORDS}


class InterviewPseudonymizer:
    """Replace directly identifiable entities with governance-approved placeholders."""

    def __init__(self, model_name: Optional[str] = None):
        self._nlp = _load_spacy_model((model_name,) if model_name else None)
        self._entity_memory: Dict[str, EntityMapping] = {}

    def reset(self) -> None:
        """Clear entity memory so a new interview starts from a clean slate."""

        self._entity_memory.clear()

    def pseudonymize(self, text: str) -> str:
        """Return text with direct identifiers replaced by placeholders."""

        if not text:
            return text

        doc = self._nlp(text)
        # Filter base NER entities to remove likely false positives before
        # extending with high-precision supplemental detections.
        entities = [e for e in doc.ents if not self._is_likely_false_positive(e)]
        replacements: List[Tuple[int, int, str]] = []

        if entities:
            occupied = {(ent.start_char, ent.end_char) for ent in entities}
        else:
            occupied = set()

        # entities.extend(self._case_normalised_entities(doc, text, occupied))
        entities.extend(self._supplement_person_entities(doc, occupied))
        entities.extend(self._supplement_email_entities(doc, occupied))
        entities = [self._normalise_email_entity(doc, ent) for ent in entities]

        for ent in entities:
            placeholder = self._placeholder_for_entity(ent)
            if not placeholder:
                continue

            key = self._normalised_key(ent)
            mapping = self._entity_memory.get(key)
            if not mapping:
                mapping = EntityMapping(ent.text, placeholder, ent.label_)
                self._entity_memory[key] = mapping

            replacements.append((ent.start_char, ent.end_char, mapping.placeholder))

        return self._apply_replacements(text, replacements)

    def export_mappings(self) -> List[EntityMapping]:
        """Expose anonymisation mappings for optional governance storage."""

        return list(self._entity_memory.values())

    def _supplement_person_entities(
        self, doc: "Doc", seen_ranges: Set[Tuple[int, int]]
    ) -> List["Span"]:
        """Return PERSON spans derived from explicit self-disclosure patterns."""

        extra_spans: List[Span] = []
        text = doc.text

        for match in NAME_FALLBACK_PATTERN.finditer(text):
            start = match.start("name")
            end = match.end("name")
            if (start, end) in seen_ranges:
                continue

            span = doc.char_span(start, end, label="PERSON", alignment_mode="contract")
            if not span:
                continue

            cleaned = span.text.strip().strip(",.;:!?")
            if not cleaned:
                continue

            seen_ranges.add((start, end))
            extra_spans.append(span)

        return extra_spans

    def _supplement_email_entities(
        self, doc: "Doc", seen_ranges: Set[Tuple[int, int]]
    ) -> List["Span"]:
        """Return EMAIL spans detected via pattern matching."""

        extra_spans: List[Span] = []
        label_id = self._ensure_email_label(doc)
        text = doc.text

        for match in EMAIL_PATTERN.finditer(text):
            start, end = match.span()
            if (start, end) in seen_ranges:
                continue
            span = doc.char_span(
                start, end, label=label_id, alignment_mode="contract"
            )
            if not span:
                continue
            seen_ranges.add((start, end))
            extra_spans.append(span)

        return extra_spans

    def _case_normalised_entities(
        self, doc: "Doc", text: str, seen_ranges: Set[Tuple[int, int]]
    ) -> List["Span"]:
        """Augment detections by capitalising word starts before running NER again."""

        fallback_text = _capitalise_candidate_words(text)
        if fallback_text == text:
            return []

        fallback_doc = self._nlp(fallback_text)
        extra_spans: List[Span] = []

        for ent in fallback_doc.ents:
            start, end = ent.start_char, ent.end_char
            if (start, end) in seen_ranges:
                continue
            span = doc.char_span(start, end, label=ent.label_, alignment_mode="contract")
            if not span:
                continue
            seen_ranges.add((start, end))
            extra_spans.append(span)

        return extra_spans

    def _is_likely_false_positive(self, ent: "Span") -> bool:
        """Return True if a base NER entity looks like a misclassification.

        Applied only to entities from doc.ents (base model output).  Supplemental
        detections from pattern matching are high-precision and bypass this gate.
        """
        label = ent.label_

        if label in ("PERSON", "ORG", "NORP", "GPE", "LOC"):
            # If every token is lowercase and not an all-caps acronym the span is
            # almost certainly a common word the model has misclassified.
            if not self._has_meaningful_capitalisation(ent):
                return True
            # A single-token entity immediately preceded by an article is a
            # generic noun ("the company", "a state"), not a proper noun.
            if len(ent) == 1 and self._preceded_by_article(ent):
                return True

        if label == "DATE":
            # Only replace dates with concrete calendar references; vague phrases
            # ("at the time", "a while", "soon") should pass through unchanged.
            if not self._date_is_specific(ent):
                return True

        return False

    def _has_meaningful_capitalisation(self, ent: "Span") -> bool:
        """Return True if at least one token is capitalised beyond sentence position."""
        for token in ent:
            if len(token.text) < 2:
                continue
            # All-caps acronym (e.g. "WHO", "NHS", "NASA").
            if token.text.isupper():
                return True
            # Mixed / title case that is not just sentence-initial capitalisation.
            if token.text != token.text.lower() and not token.is_sent_start:
                return True
        return False

    def _preceded_by_article(self, ent: "Span") -> bool:
        """Return True if the entity is immediately preceded by a, an, or the."""
        if ent.start == 0:
            return False
        return ent.doc[ent.start - 1].lower_ in {"a", "an", "the"}

    def _date_is_specific(self, ent: "Span") -> bool:
        """Return True only if the date entity contains a year or a named calendar term."""
        if DATE_YEAR_PATTERN.search(ent.text):
            return True
        words = {w.lower() for w in re.findall(r"\b[a-z]+\b", ent.text.lower())}
        return bool(words & SPECIFIC_DATE_WORDS)

    def _placeholder_for_entity(self, ent: "Span") -> Optional[str]:
        label = ent.label_

        if label == "EMAIL":
            return "<email address>"

        if label == "DATE":
            return self._date_placeholder(ent)

        if label == "FAC":
            return self._facility_placeholder(ent)

        return PLACEHOLDER_BY_LABEL.get(label)

    def _date_placeholder(self, ent: "Span") -> str:
        match = DATE_YEAR_PATTERN.search(ent.text)
        if match:
            return f"<date-{match.group(0)}>"
        return "<date>"

    def _facility_placeholder(self, ent: "Span") -> Optional[str]:
        text = ent.text.strip()
        lowered = text.lower()
        without_article = re.sub(r"^(?:the|a|an)\s+", "", lowered)

        if without_article in GENERIC_FACILITY_TERMS:
            return None

        facility_type = self._infer_facility_type(without_article)
        return f"<{facility_type}>"

    def _infer_facility_type(self, facility_text: str) -> str:
        for placeholder, keywords in FACILITY_TYPE_KEYWORDS.items():
            for keyword in keywords:
                if keyword in facility_text:
                    return placeholder
        return "facility"

    def _normalised_key(self, ent: "Span") -> str:
        return f"{ent.label_}:{ent.text.strip().lower()}"

    def _normalise_email_entity(self, doc: "Doc", ent: "Span") -> "Span":
        text = ent.text.strip()
        if EMAIL_PATTERN.fullmatch(text):
            label_id = self._ensure_email_label(doc)
            return Span(doc, ent.start, ent.end, label=label_id)
        return ent

    @staticmethod
    def _ensure_email_label(doc: "Doc") -> int:
        return doc.vocab.strings.add("EMAIL")

    @staticmethod
    def _apply_replacements(text: str, replacements: List[Tuple[int, int, str]]) -> str:
        if not replacements:
            return text

        replacements.sort(key=lambda item: item[0])
        result_parts: List[str] = []
        last_index = 0

        for start, end, replacement in replacements:
            if start < last_index:
                # Skip overlapping spans to keep deterministic output.
                continue
            result_parts.append(text[last_index:start])
            result_parts.append(replacement)
            last_index = end

        result_parts.append(text[last_index:])
        return "".join(result_parts)


def pseudonymize_messages(
    pseudonymizer: InterviewPseudonymizer, messages: List[Dict[str, str]]
) -> Tuple[List[Dict[str, str]], List[EntityMapping]]:
    """Pseudonymise user messages only and expose mapping metadata.

    Assistant and system messages are stored verbatim — only respondent
    (role == "user") content is passed through NER and entity replacement.
    """

    pseudonymizer.reset()
    transformed: List[Dict[str, str]] = []

    for message in messages:
        if message["role"] == "user":
            transformed.append(
                {
                    "role": message["role"],
                    "content": pseudonymizer.pseudonymize(message["content"]),
                }
            )
        else:
            transformed.append(dict(message))

    mappings = pseudonymizer.export_mappings()
    retrofitted = _apply_mappings_to_messages(transformed, mappings)
    return retrofitted, mappings


def _capitalise_candidate_words(text: str) -> str:
    """Uppercase the first letter of likely proper nouns while preserving spacing."""

    stopwords = _get_spacy_stopwords()

    def _replacement(match: re.Match[str]) -> str:
        word = match.group(0)
        if len(word) <= 2:
            return word
        if word in stopwords:
            return word
        return word[0].upper() + word[1:]

    transformed = WORD_START_PATTERN.sub(_replacement, text)
    return re.sub(r"^[a-z]", lambda match: match.group().upper(), transformed, count=1)


def _build_mapping_pattern(original: str) -> Optional[re.Pattern[str]]:
    if not original:
        return None

    escaped = re.escape(original)
    prefix = r"\b" if original[0].isalnum() else ""
    suffix = r"\b" if original[-1].isalnum() else ""
    return re.compile(f"{prefix}{escaped}{suffix}", flags=re.IGNORECASE)


def _apply_mappings_to_messages(
    messages: List[Dict[str, str]], mappings: Sequence[EntityMapping]
) -> List[Dict[str, str]]:
    if not mappings:
        return messages

    patterns: List[Tuple[re.Pattern[str], str]] = []
    for mapping in mappings:
        pattern = _build_mapping_pattern(mapping.original)
        if pattern:
            patterns.append((pattern, mapping.placeholder))

    if not patterns:
        return messages

    transformed: List[Dict[str, str]] = []
    for message in messages:
        content = message["content"]
        for pattern, replacement in patterns:
            content = pattern.sub(replacement, content)
        transformed.append({"role": message["role"], "content": content})

    return transformed
