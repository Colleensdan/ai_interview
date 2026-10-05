"""The cacheable prompt order, and the guarantee that the old one is unchanged.

Prompt caching is a prefix match: everything before the first byte that differs
between two calls can be served from cache at about a tenth of the price. Our
prompt led with the code name, so no two calls shared anything beyond the system
prompt and caching never engaged. Putting the transcripts first makes ~85% of
input cacheable -- but it also changes what the model reads last before
answering, so the old order has to stay available until an A/B says kappa holds.
"""

from __future__ import annotations

import config
from models.base import SCOPE_INTERVIEWER, CodingRequest
from models.prompting import build_user_prompt

CORPUS = "===== BEGIN DOCUMENT | title: a.txt =====\nhello\n===== END DOCUMENT ====="


def _req(**kw):
    base = dict(code_name="anger/irritation", code_description="A definition.",
                merged_document=CORPUS, document_titles=("a.txt", "b.txt"))
    base.update(kw)
    return CodingRequest(**base)


def test_default_order_is_unchanged(monkeypatch):
    """Every kappa reported so far was produced with this exact text."""
    monkeypatch.setattr(config, "PROMPT_ORDER", "code_first")
    r = _req()
    assert build_user_prompt(r) == (
        f"CODE NAME: {r.code_name}\n"
        f"CODE DEFINITION: {r.code_description}\n\n"
        f"Valid document titles: {list(r.document_titles)}\n\n"
        "INTERVIEW DOCUMENTS:\n"
        f"{r.merged_document}"
    )


def test_default_is_code_first():
    """The reported order stays the default until an A/B clears the new one."""
    assert config.PROMPT_ORDER == "code_first"


def test_documents_first_puts_the_corpus_at_the_front(monkeypatch):
    monkeypatch.setattr(config, "PROMPT_ORDER", "documents_first")
    p = build_user_prompt(_req())
    assert p.startswith("INTERVIEW DOCUMENTS:\n"), "the corpus must lead, or nothing caches"
    assert p.index(CORPUS) < p.index("CODE NAME:"), "the code must trail the corpus"


def test_the_cacheable_prefix_is_identical_across_codes(monkeypatch):
    """The property the whole saving rests on: same batch, any code, same prefix."""
    monkeypatch.setattr(config, "PROMPT_ORDER", "documents_first")
    a = build_user_prompt(_req(code_name="anger/irritation", code_description="One."))
    b = build_user_prompt(_req(code_name="frustration", code_description="Another."))
    prefix_a = a[:a.index("CODE NAME:")]
    prefix_b = b[:b.index("CODE NAME:")]
    assert prefix_a == prefix_b, "prefix differs between codes; caching would not engage"
    assert len(prefix_a) > len(CORPUS), "the prefix should carry the corpus, not just a header"


def test_a_different_batch_gets_a_different_prefix(monkeypatch):
    """Each batch caches separately; they must not be conflated."""
    monkeypatch.setattr(config, "PROMPT_ORDER", "documents_first")
    a = build_user_prompt(_req())
    b = build_user_prompt(_req(merged_document=CORPUS.replace("hello", "goodbye")))
    assert a[:a.index("CODE NAME:")] != b[:b.index("CODE NAME:")]


def test_interviewer_scope_still_applies_in_the_new_order(monkeypatch):
    monkeypatch.setattr(config, "PROMPT_ORDER", "documents_first")
    p = build_user_prompt(_req(code_name="finished", scope=SCOPE_INTERVIEWER))
    assert "ONLY interviewer turns" in p
    assert p.index("CODE NAME:") < p.index("ONLY interviewer turns"), \
        "the scope note belongs with the code, in the uncached tail"
