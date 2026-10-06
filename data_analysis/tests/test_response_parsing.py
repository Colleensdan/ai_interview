"""The reply contract: a model either answers in the agreed shape, or it fails.

Until 2026-10 the parser never raised. Any reply it could not use was read as
"no occurrences", which marks every document in the batch as coded-and-absent.
That was invisible in the results: gpt-5.2-chat was caught answering questions
nobody had sent ("has meditation helped people heal from trauma?"), in valid
JSON under a ``message`` key, and every such batch went into the count matrix
as 25 confident negatives. A Claude reply with prose after the JSON was lost the
same way.

The rule now: ``{"occurrences": []}`` is the ONLY way to say "absent". Anything
the parser cannot read unambiguously raises ``MalformedResponseError``; the
caller re-asks and, failing that, records the code as FAILED. The old tests
here asserted the lenient behaviour (bare strings skipped, missing key read as
empty); they are replaced deliberately, not weakened.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from models.azure_openai import AzureOpenAIAdapter
from models.base import CodingRequest, MalformedResponseError, TruncatedResponseError
from models.gateway import GatewayAnthropicAdapter
from models.prompting import extract_json, parse_occurrences

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


def _anthropic_returning(text, stop_reason="end_turn"):
    adapter = GatewayAnthropicAdapter("claude-sonnet-4-6")
    adapter._post = lambda body: {  # type: ignore[method-assign]
        "content": [{"type": "text", "text": text}], "stop_reason": stop_reason}
    return adapter


def _occ(title="a.txt", quote="q1", reason="r1"):
    return {"document_title": title, "quote": quote, "reason": reason}


# --- what is accepted --------------------------------------------------------

def test_well_formed_occurrences_are_parsed():
    hits = _adapter_returning({"occurrences": [
        _occ("a.txt", "q1", "r1"), _occ("b.txt", "q2", "r2"),
    ]}).code_one(REQUEST)
    assert [(h.document_title, h.quote) for h in hits] == [("a.txt", "q1"), ("b.txt", "q2")]


def test_an_empty_list_is_the_one_valid_way_to_say_absent():
    assert _adapter_returning({"occurrences": []}).code_one(REQUEST) == []


def test_a_json_fence_is_accepted():
    text = '```json\n{"occurrences": [{"document_title": "b.txt", "quote": "q"}]}\n```'
    assert [h.quote for h in parse_occurrences(text, REQUEST)] == ["q"]


def test_prose_around_the_object_is_accepted():
    text = 'Here is the result:\n{"occurrences": []}\nThat is all.'
    assert parse_occurrences(text, REQUEST) == []


def test_trailing_prose_containing_braces_is_accepted():
    """The shape that lost a claude-sonnet-4-6 batch ("Extra data" error).

    Taking the outermost ``{ ... }`` span swallowed the trailing prose, so
    every parse attempt failed and the batch was scored as absent.
    """
    text = ('{"occurrences": [{"document_title": "a.txt", "quote": "q", '
            '"reason": "r"}]}\n\nNote: no {summary: 2} rating elsewhere.')
    assert [h.quote for h in _anthropic_returning(text).code_one(REQUEST)] == ["q"]


def test_raw_control_characters_inside_a_quote_are_kept_verbatim():
    text = '{"occurrences": [{"document_title": "a.txt", "quote": "line1\nline2"}]}'
    assert parse_occurrences(text, REQUEST)[0].quote == "line1\nline2"


def test_a_missing_reason_is_allowed():
    hits = parse_occurrences(json.dumps({"occurrences": [
        {"document_title": "a.txt", "quote": "q"}]}), REQUEST)
    assert hits[0].reason == ""


def test_an_abbreviated_title_is_snapped_when_it_matches_exactly_one_document():
    hits = _adapter_returning({"occurrences": [_occ("a")]}).code_one(REQUEST)
    assert hits[0].document_title == "a.txt"


# --- what is rejected --------------------------------------------------------

@pytest.mark.parametrize("reply", [
    # Real gpt-5.2-chat replies (2026-10-05), abridged: answers to questions
    # that appear nowhere in the prompt, in valid JSON with no occurrences key.
    {"message": "Yes. Many people report that meditation has helped them "
                "recover from trauma-related symptoms ..."},
    {"/answer": "I can't provide instructions for making methamphetamine ..."},
    {"result": "nothing"},
], ids=["off-task-message", "off-task-answer", "other-key"])
def test_valid_json_without_an_occurrences_key_is_malformed_not_absent(reply):
    with pytest.raises(MalformedResponseError, match="occurrences"):
        _adapter_returning(reply).code_one(REQUEST)


@pytest.mark.parametrize("payload,why", [
    ("not json at all", "no JSON object"),
    ("", "empty reply"),
    ("   \n  ", "empty reply"),
    ([{"quote": "q"}], "no JSON object"),                          # top-level list
    ({"occurrences": "none found"}, "not a list"),
    ({"occurrences": _occ()}, "not a list"),                       # lone object
    ({"occurrences": ["stray text", _occ()]}, "not an object"),
    ({"occurrences": [_occ(quote="")]}, "no quote"),
    ({"occurrences": [_occ(quote="   ")]}, "no quote"),
    ({"occurrences": [{"document_title": "a.txt"}]}, "no quote"),
    ({"occurrences": [{"quote": "q"}]}, "no document_title"),
    ({"occurrences": [_occ(title="z.txt")]}, "not exactly one document"),
    ({"occurrences": [_occ(title="")]}, "not exactly one document"),
])
def test_contract_violations_raise(payload, why):
    with pytest.raises(MalformedResponseError, match=why):
        _adapter_returning(payload).code_one(REQUEST)


def test_one_bad_element_rejects_the_whole_reply():
    """A reply that breaks the contract in one place is not evidence about the
    rest. Keeping the good elements would store a partial answer as complete."""
    with pytest.raises(MalformedResponseError):
        parse_occurrences(json.dumps({"occurrences": [_occ(), "stray"]}), REQUEST)


def test_two_answers_in_one_reply_are_ambiguous():
    text = '{"occurrences": []}\n{"occurrences": [{"document_title": "a.txt", "quote": "q"}]}'
    with pytest.raises(MalformedResponseError, match="2 JSON objects"):
        parse_occurrences(text, REQUEST)


def test_an_ambiguous_abbreviated_title_is_rejected_not_guessed():
    """The old rule took the first match from a set, whose order varies between
    processes -- so the same reply could be attributed differently on a rerun."""
    req = CodingRequest("worry", "d", "...", ("R_1.txt", "R_12.txt"))
    with pytest.raises(MalformedResponseError, match="not exactly one document"):
        parse_occurrences(json.dumps({"occurrences": [_occ(title="R_1")]}), req)


def test_the_raw_reply_travels_with_the_error():
    with pytest.raises(MalformedResponseError) as info:
        parse_occurrences('{"message": "hi"}', REQUEST)
    assert info.value.content == '{"message": "hi"}'


def test_truncation_raises_so_the_caller_can_split_the_batch():
    with pytest.raises(TruncatedResponseError):
        _adapter_returning({"occurrences": []}, finish_reason="length").code_one(REQUEST)


# --- determinism -------------------------------------------------------------

def test_parsing_is_a_pure_function_of_the_reply():
    text = ('preamble {not json} {"occurrences": [{"document_title": "a", '
            '"quote": "q", "reason": "r"}]} trailing {x}')
    first = parse_occurrences(text, REQUEST)
    assert all(parse_occurrences(text, REQUEST) == first for _ in range(20))
    assert extract_json(text) == {"occurrences": [
        {"document_title": "a", "quote": "q", "reason": "r"}]}


def test_a_nested_occurrences_key_is_not_a_second_answer():
    """Only top-level objects are candidates; scanning into an object already
    decoded would count its children and wrongly call the reply ambiguous."""
    text = json.dumps({"occurrences": [
        {"document_title": "a.txt", "quote": "q", "reason": "r",
         "extra": {"occurrences": []}}]})
    assert [h.quote for h in parse_occurrences(text, REQUEST)] == ["q"]
