"""Utilities for pseudonymising interview transcripts before persistence."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

import spacy
from spacy.language import Language
from spacy.lang.en.stop_words import STOP_WORDS
from spacy.tokens import Doc, Span


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


WORD_START_PATTERN = re.compile(r"\b[a-z][a-z'\-]*")


STOPWORDS = {word.lower() for word in STOP_WORDS}


EMAIL_PATTERN = re.compile(
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}",
)


NAME_FALLBACK_PATTERN = re.compile(
    r"\b(?:my\s+name\s+is|name\s*[:=])\s+(?P<name>[A-Za-z][A-Za-z'\-]*(?:\s+[A-Za-z][A-Za-z'\-]*){0,3})",
    re.IGNORECASE,
)


@dataclass
class EntityMapping:
    """Record of how a specific entity was pseudonymised."""

    original: str
    placeholder: str
    label: str


def _load_spacy_model(preferred_names: Sequence[str] | None = None) -> Language:
    """Return a spaCy English model, raising a clear error if none are installed."""

    names: Iterable[str]
    if preferred_names:
        names = preferred_names
    else:
        names = ("en_core_web_sm", "en_core_web_md", "en_core_web_lg")

    for name in names:
        try:
            return spacy.load(name)
        except OSError:
            continue

    raise RuntimeError(
        "No spaCy English model is installed. Run 'python -m spacy download en_core_web_sm' "
        "to enable interview text pseudonymisation."
    )


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
        entities = list(doc.ents)
        replacements: List[Tuple[int, int, str]] = []

        if entities:
            occupied = {(ent.start_char, ent.end_char) for ent in entities}
        else:
            occupied = set()

        entities.extend(self._case_normalised_entities(doc, text, occupied))
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
        self, doc: Doc, seen_ranges: Set[Tuple[int, int]]
    ) -> List[Span]:
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
        self, doc: Doc, seen_ranges: Set[Tuple[int, int]]
    ) -> List[Span]:
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
        self, doc: Doc, text: str, seen_ranges: Set[Tuple[int, int]]
    ) -> List[Span]:
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

    def _placeholder_for_entity(self, ent: Span) -> Optional[str]:
        label = ent.label_

        if label == "EMAIL":
            return "<email address>"

        if label == "DATE":
            return self._date_placeholder(ent)

        if label == "FAC":
            return self._facility_placeholder(ent)

        return PLACEHOLDER_BY_LABEL.get(label)

    def _date_placeholder(self, ent: Span) -> str:
        match = DATE_YEAR_PATTERN.search(ent.text)
        if match:
            return f"<date-{match.group(0)}>"
        return "<date>"

    def _facility_placeholder(self, ent: Span) -> Optional[str]:
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

    def _normalised_key(self, ent: Span) -> str:
        return f"{ent.label_}:{ent.text.strip().lower()}"

    def _normalise_email_entity(self, doc: Doc, ent: Span) -> Span:
        text = ent.text.strip()
        if EMAIL_PATTERN.fullmatch(text):
            label_id = self._ensure_email_label(doc)
            return Span(doc, ent.start, ent.end, label=label_id)
        return ent

    @staticmethod
    def _ensure_email_label(doc: Doc) -> int:
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
    """Pseudonymise message payloads and expose mapping metadata."""

    pseudonymizer.reset()
    transformed: List[Dict[str, str]] = []

    for message in messages:
        transformed.append(
            {
                "role": message["role"],
                "content": pseudonymizer.pseudonymize(message["content"]),
            }
        )

    mappings = pseudonymizer.export_mappings()
    retrofitted = _apply_mappings_to_messages(transformed, mappings)
    return retrofitted, mappings


def _capitalise_candidate_words(text: str) -> str:
    """Uppercase the first letter of likely proper nouns while preserving spacing."""

    def _replacement(match: re.Match[str]) -> str:
        word = match.group(0)
        if len(word) <= 2:
            return word
        if word in STOPWORDS:
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
