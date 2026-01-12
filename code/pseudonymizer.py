"""Utilities for pseudonymising interview transcripts before persistence."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import spacy
from spacy.language import Language
from spacy.tokens import Span


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
        replacements: List[Tuple[int, int, str]] = []

        for ent in doc.ents:
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

    def _placeholder_for_entity(self, ent: Span) -> Optional[str]:
        label = ent.label_

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

    return transformed, pseudonymizer.export_mappings()
