"""The model response parser must survive shapes JSON mode does not constrain.

`response_format={"type": "json_object"}` guarantees syntactically valid JSON,
not a particular structure. Over a few hundred calls the model does occasionally
deviate — a bare string in place of an occurrence object crashed a full run at
code 45 of 50, losing 44 codes' worth of work. One malformed element must cost
that element, not the run.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from models.azure_openai import AzureOpenAIAdapter
from models.base import CodingRequest, TruncatedResponseError

REQUEST = CodingRequest(
    code_name="worry",
    code_description="feeling worried",
    merged_document="...",
    document_titles=("a.txt", "b.txt"),
)


def _adapter_returning(payload, finish_reason="stop") -> AzureOpenAIAdapter:
    adapter = AzureOpenAIAdapter()
    content = payload if isinstance(payload, str) else json.dumps(payload)
    adapter._create = lambda messages: SimpleNamespace(  # type: ignore[method-assign]
        choices=[SimpleNamespace(
            finish_reason=finish_reason,
            message=SimpleNamespace(content=content),
        )]
    )
    return adapter


def test_well_formed_occurrences_are_parsed():
    hits = _adapter_returning({"occurrences": [
        {"document_title": "a.txt", "quote": "q1", "reason": "r1"},
        {"document_title": "b.txt", "quote": "q2", "reason": "r2"},
    ]}).code_one(REQUEST)
    assert [(h.document_title, h.quote) for h in hits] == [("a.txt", "q1"), ("b.txt", "q2")]


def test_a_bare_string_occurrence_is_skipped_not_fatal():
    """The exact shape that killed a 50-code run."""
    hits = _adapter_returning({"occurrences": [
        "some stray text",
        {"document_title": "a.txt", "quote": "q1", "reason": "r1"},
    ]}).code_one(REQUEST)
    assert [h.quote for h in hits] == ["q1"]


def test_a_single_object_instead_of_a_list_is_accepted():
    hits = _adapter_returning({
        "occurrences": {"document_title": "a.txt", "quote": "q1", "reason": "r1"}
    }).code_one(REQUEST)
    assert [h.quote for h in hits] == ["q1"]


def test_occurrences_of_an_unexpected_type_yield_nothing():
    assert _adapter_returning({"occurrences": "none found"}).code_one(REQUEST) == []


def test_a_top_level_list_yields_nothing_rather_than_crashing():
    assert _adapter_returning([{"quote": "q"}]).code_one(REQUEST) == []


def test_missing_occurrences_key_is_no_occurrences():
    assert _adapter_returning({"result": "nothing"}).code_one(REQUEST) == []


def test_unparseable_content_is_no_occurrences():
    assert _adapter_returning("not json at all").code_one(REQUEST) == []


def test_truncation_raises_so_the_caller_can_split_the_batch():
    with pytest.raises(TruncatedResponseError):
        _adapter_returning({"occurrences": []}, finish_reason="length").code_one(REQUEST)


def test_an_abbreviated_title_is_snapped_to_a_real_one():
    hits = _adapter_returning({"occurrences": [
        {"document_title": "a", "quote": "q", "reason": "r"},
    ]}).code_one(REQUEST)
    assert hits[0].document_title == "a.txt"
