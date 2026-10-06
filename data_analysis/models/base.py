"""Pluggable model-adapter interface.

Every model the pipeline can use sits behind ``ModelAdapter``. The pipeline
never talks to a vendor SDK directly — it iterates over whatever adapters are
*available* (have credentials). Today only Azure OpenAI is live; the others are
stubs that report themselves unavailable until their keys exist. Majority voting
therefore works with N=1 today and N>1 the moment a stub is filled in.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


class TruncatedResponseError(RuntimeError):
    """The model ran out of output tokens before finishing its answer.

    Raised rather than swallowed: a truncated reply carries an arbitrary prefix
    of the occurrences, so treating it as "no occurrences" (or as a complete
    list) silently understates how often a code appears and corrupts its kappa.
    The caller is expected to retry with fewer documents.
    """


class MalformedResponseError(ValueError):
    """The model answered, but not with a reply the coding contract accepts.

    Raised rather than read as "no occurrences". A reply with no usable
    ``occurrences`` list says nothing about whether the code applies -- one
    model was seen answering a question nobody had asked, in valid JSON under a
    different key -- and scoring it as absent silently marks every document in
    the batch as uncoded-but-negative. The caller re-asks; if the model never
    produces a valid reply, the code is recorded as FAILED, not as absent.

    ``content`` carries the raw reply so it can be stored for audit.
    """

    def __init__(self, message: str, content: str = "") -> None:
        super().__init__(message)
        self.content = content


@dataclass(frozen=True)
class RawReply:
    """One model reply exactly as received, before any parsing.

    Kept separate from the parsed hits so every reply can be stored verbatim:
    the stored count matrix must be re-derivable from stored replies alone, or a
    published result cannot be audited.
    """

    content: str
    finish_reason: str | None = None
    #: The request parameters actually sent (after any the model rejected were
    #: dropped). Recorded because a dropped ``temperature`` changes sampling.
    params: dict = field(default_factory=dict)
    prompt_tokens: int | None = None
    completion_tokens: int | None = None


# Speaker scope for a code: whose turns the model may apply it to.
SCOPE_INTERVIEWEE = "interviewee"
SCOPE_INTERVIEWER = "interviewer"


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
    # Which speaker's turns this code may be applied to. Defaults to the
    # participant, which is every substantive code; a procedural code that
    # describes the interviewer's own behaviour sets SCOPE_INTERVIEWER.
    scope: str = SCOPE_INTERVIEWEE


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

    def complete(self, request: CodingRequest) -> RawReply:
        """Send one coding request and return the reply unparsed.

        Raises :class:`TruncatedResponseError` when the reply was cut off by the
        output limit. Live adapters implement this; parsing is done once,
        centrally, by :func:`models.prompting.parse_occurrences`, so every
        provider is read by the same rules.
        """
        raise NotImplementedError(
            f"{type(self).__name__} does not implement complete()")

    def code_one(self, request: CodingRequest) -> list[CodeHit]:
        """Apply one code to the merged document; return every occurrence.

        Returns ``[]`` only when the model validly answered that the code does
        not apply anywhere. Raises :class:`MalformedResponseError` when the
        reply cannot be read as an answer at all.
        """
        from .prompting import parse_occurrences
        return parse_occurrences(self.complete(request).content, request)

    @classmethod
    def has_raw_replies(cls) -> bool:
        """True if this adapter implements :meth:`complete`."""
        return cls.complete is not ModelAdapter.complete

    def __str__(self) -> str:  # pragma: no cover - cosmetic
        return f"{self.__class__.__name__}(name={self.name!r})"
