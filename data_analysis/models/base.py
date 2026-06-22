"""Pluggable model-adapter interface.

Every model the pipeline can use sits behind ``ModelAdapter``. The pipeline
never talks to a vendor SDK directly — it iterates over whatever adapters are
*available* (have credentials). Today only Azure OpenAI is live; the others are
stubs that report themselves unavailable until their keys exist. Majority voting
therefore works with N=1 today and N>1 the moment a stub is filled in.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class CodeHit:
    """One occurrence of a code the model found in one document.

    Maps directly onto the required results columns (spec 4.3):
    document title, code name, quote, reason.
    """

    document_title: str
    code_name: str
    quote: str
    reason: str


@dataclass(frozen=True)
class CodingRequest:
    """Inputs for a single per-code coding call (spec 4.2)."""

    code_name: str
    code_description: str
    merged_document: str
    # Titles of the documents present in merged_document, so an adapter can
    # validate / constrain the titles it returns.
    document_titles: tuple[str, ...]


class ModelAdapter(ABC):
    """Base class for all model adapters.

    Subclasses implement :meth:`code_one` for a single (code, merged-doc) call
    and declare availability via :meth:`is_available`.
    """

    #: Stable, filesystem-safe identifier used in CSV filenames and the DB
    #: ``model`` column. Set by subclasses.
    name: str = "base"

    @abstractmethod
    def is_available(self) -> bool:
        """True if this adapter has the credentials/config needed to run."""

    @abstractmethod
    def code_one(self, request: CodingRequest) -> list[CodeHit]:
        """Apply one code to the merged document; return every occurrence.

        Must return ``[]`` when the code does not apply anywhere.
        """

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return f"{self.__class__.__name__}(name={self.name!r})"
