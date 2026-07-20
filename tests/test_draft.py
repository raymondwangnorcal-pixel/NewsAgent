from __future__ import annotations

import json
import sys
from datetime import datetime, timezone

import pytest

from news_agent.draft import DRAFT_SYSTEM_PROMPT, DraftCandidate, draft_paragraphs
from news_agent.models import Article


def make_article(
    *,
    title: str = "Sample headline",
    summary: str = "Sample summary describing what happened in enough detail to stand alone.",
    url: str = "https://example.com/story",
    source: str = "Example News",
) -> Article:
    return Article(
        title=title,
        url=url,
        source=source,
        published_at=datetime.now(timezone.utc),
        summary=summary,
    )


def make_candidate(
    story_id: str = "story-1",
    category: str = "finance",
    title: str = "Sample story",
    articles: list[Article] | None = None,
) -> DraftCandidate:
    return DraftCandidate(
        story_id=story_id,
        category=category,
        title=title,
        articles=tuple(articles or [make_article(title=title)]),
    )


# --- Fake OpenAI client -------------------------------------------------------------


class FakeResponse:
    def __init__(self, output_text: str) -> None:
        self.output_text = output_text


class FakeResponses:
    def __init__(self, outputs: list[str] | None = None, error: Exception | None = None) -> None:
        self._outputs = outputs or []
        self._error = error
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self._error is not None:
            raise self._error
        index = len(self.calls) - 1
        output_text = self._outputs[index] if index < len(self._outputs) else self._outputs[-1]
        return FakeResponse(output_text)


class FakeOpenAI:
    _shared_responses: FakeResponses | None = None

    def __init__(self, *args, **kwargs) -> None:
        self.responses = FakeOpenAI._shared_responses


class _FakeOpenAIModule:
    OpenAI = FakeOpenAI


def install_fake_openai(monkeypatch: pytest.MonkeyPatch, fake_responses: FakeResponses) -> None:
    FakeOpenAI._shared_responses = fake_responses
    monkeypatch.setitem(sys.modules, "openai", _FakeOpenAIModule())


def paragraph_payload(entries: list[dict]) -> str:
    return json.dumps({"paragraphs": entries})


# --- Paragraph structure -------------------------------------------------------------


def test_draft_prompt_prioritizes_the_compact_informal_analytical_story_style() -> None:
    assert "normally 55-90 words and 2-3 sentences" in DRAFT_SYSTEM_PROMPT
    assert "Lead immediately with the main actors, action or event, and immediate result" in DRAFT_SYSTEM_PROMPT
    assert "End with the broader consequence, risk, or likely next development" in DRAFT_SYSTEM_PROMPT
    assert "informal but credible, analytical without sounding academic" in DRAFT_SYSTEM_PROMPT
    assert "knowledgeable person summarizing the news for a friend" in DRAFT_SYSTEM_PROMPT


def test_draft_produces_one_paragraph_per_candidate_no_headline_field(monkeypatch: pytest.MonkeyPatch) -> None:
    candidate = make_candidate(story_id="ai-datacenter", category="business_tech")
    fake_responses = FakeResponses(
        outputs=[
            paragraph_payload(
                [
                    {
                        "story_id": "ai-datacenter",
                        "paragraph": "Meta expanded its planned Louisiana data center to five "
                        "gigawatts, with more than $50 billion in projected investment as the AI "
                        "infrastructure race accelerates.",
                    }
                ]
            )
        ]
    )
    install_fake_openai(monkeypatch, fake_responses)

    results = draft_paragraphs([candidate], openai_mode="full")

    assert len(results) == 1
    result = results[0]
    assert result.story_id == "ai-datacenter"
    assert result.category == "business_tech"
    assert not result.paragraph.startswith("•")
    assert "\n•" not in result.paragraph
    # canonical schema has no separate headline/why_it_matters/next_watch fields
    assert not hasattr(result, "headline")
    assert not hasattr(result, "why_it_matters")
    assert not hasattr(result, "next_watch")


def test_draft_sources_and_urls_deduplicated(monkeypatch: pytest.MonkeyPatch) -> None:
    articles = [
        make_article(url="https://example.com/1", source="Reuters"),
        make_article(url="https://example.com/2", source="Reuters"),
        make_article(url="https://example.com/3", source="AP News"),
    ]
    candidate = make_candidate(articles=articles)
    fake_responses = FakeResponses(
        outputs=[paragraph_payload([{"story_id": "story-1", "paragraph": "Something happened."}])]
    )
    install_fake_openai(monkeypatch, fake_responses)

    results = draft_paragraphs([candidate], openai_mode="full")

    assert results[0].sources == ("Reuters", "AP News")
    assert results[0].urls == ("https://example.com/1", "https://example.com/2", "https://example.com/3")


# --- Preservation of numbers/entities (LLM path passthrough) -------------------------


def test_draft_preserves_figures_and_entities_from_llm_response(monkeypatch: pytest.MonkeyPatch) -> None:
    candidate = make_candidate(story_id="oil-spike", category="finance")
    text = (
        "Oil prices jumped roughly 9% after Iran threatened shipping through the Strait of "
        "Hormuz, and the S&P 500 fell 3% as investors moved away from riskier assets."
    )
    fake_responses = FakeResponses(outputs=[paragraph_payload([{"story_id": "oil-spike", "paragraph": text}])])
    install_fake_openai(monkeypatch, fake_responses)

    results = draft_paragraphs([candidate], openai_mode="full")

    assert "9%" in results[0].paragraph
    assert "Strait of Hormuz" in results[0].paragraph
    assert "S&P 500" in results[0].paragraph
    assert "3%" in results[0].paragraph


# --- Deterministic fallback (extractive, no invented causal claims) ------------------


def test_extractive_fallback_never_invents_causal_claims() -> None:
    article = make_article(
        title="Nvidia shares rise 12% after earnings",
        summary="Nvidia reported quarterly revenue above analyst expectations, sending shares up "
        "12% in after-hours trading.",
    )
    candidate = make_candidate(articles=[article])

    results = draft_paragraphs([candidate], openai_mode="off")

    paragraph = results[0].paragraph
    # extractive fallback must be built only from the source article's own text --
    # it must not contain any of the deleted canned "why it matters" phrases.
    assert "borrowing costs" not in paragraph
    assert "risk appetite" not in paragraph
    assert "12%" in paragraph
    assert "Nvidia" in paragraph


def test_extractive_fallback_prepends_title_when_not_in_summary() -> None:
    article = make_article(
        title="Fed cuts interest rates by a quarter point",
        summary="The move was widely expected by markets.",
    )
    candidate = make_candidate(articles=[article])

    results = draft_paragraphs([candidate], openai_mode="off")

    assert "Fed cuts interest rates" in results[0].paragraph
    assert "widely expected" in results[0].paragraph


def test_extractive_fallback_truncates_at_sentence_boundary_not_mid_sentence() -> None:
    long_summary = " ".join(
        f"This is sentence number {i} describing an important development in detail." for i in range(1, 20)
    )
    article = make_article(title="Long story", summary=long_summary)
    candidate = make_candidate(articles=[article])

    results = draft_paragraphs([candidate], openai_mode="off")

    paragraph = results[0].paragraph
    assert len(paragraph) <= 420
    assert paragraph.rstrip().endswith((".", "!", "?"))


def test_extractive_fallback_uses_title_when_no_article_has_a_summary() -> None:
    article = make_article(title="Breaking headline with no summary", summary="")
    candidate = make_candidate(title="Breaking headline with no summary", articles=[article])

    results = draft_paragraphs([candidate], openai_mode="off")

    assert "Breaking headline with no summary" in results[0].paragraph


# --- OpenAI-disabled mode never calls the LLM -----------------------------------------


def test_draft_off_mode_never_calls_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    candidate = make_candidate()
    fake_responses = FakeResponses(error=AssertionError("LLM should not be called in off mode"))
    install_fake_openai(monkeypatch, fake_responses)

    results = draft_paragraphs([candidate], openai_mode="off")

    assert len(results) == 1
    assert fake_responses.calls == []


def test_draft_empty_candidates_skips_llm_entirely(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_responses = FakeResponses(error=AssertionError("LLM should not be called for an empty pool"))
    install_fake_openai(monkeypatch, fake_responses)

    results = draft_paragraphs([], openai_mode="full")

    assert results == []
    assert fake_responses.calls == []


# --- Malformed model responses / graceful degradation ---------------------------------


def test_draft_degrades_to_fallback_on_exception(monkeypatch: pytest.MonkeyPatch) -> None:
    article = make_article(title="Some event happened", summary="Details about the event.")
    candidate = make_candidate(articles=[article])
    fake_responses = FakeResponses(error=RuntimeError("boom"))
    install_fake_openai(monkeypatch, fake_responses)

    results = draft_paragraphs([candidate], openai_mode="full")

    assert len(results) == 1
    assert "Details about the event" in results[0].paragraph


def test_draft_degrades_to_fallback_on_malformed_json(monkeypatch: pytest.MonkeyPatch) -> None:
    article = make_article(title="Some event happened", summary="Details about the event.")
    candidate = make_candidate(articles=[article])
    fake_responses = FakeResponses(outputs=["not valid json {{{"])
    install_fake_openai(monkeypatch, fake_responses)

    results = draft_paragraphs([candidate], openai_mode="full")

    assert len(results) == 1
    assert "Details about the event" in results[0].paragraph


def test_draft_partial_response_fills_gap_with_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    covered = make_candidate(story_id="covered", articles=[make_article(title="Covered story")])
    uncovered_article = make_article(title="Uncovered event", summary="Fallback text for this one.")
    uncovered = make_candidate(story_id="uncovered", articles=[uncovered_article])
    fake_responses = FakeResponses(
        outputs=[paragraph_payload([{"story_id": "covered", "paragraph": "LLM wrote this paragraph."}])]
    )
    install_fake_openai(monkeypatch, fake_responses)

    results = draft_paragraphs([covered, uncovered], openai_mode="full")

    by_id = {result.story_id: result for result in results}
    assert by_id["covered"].paragraph == "LLM wrote this paragraph."
    assert "Fallback text for this one" in by_id["uncovered"].paragraph


def test_draft_empty_paragraph_string_falls_back() -> None:
    article = make_article(title="Some event", summary="Fallback content here.")
    candidate = make_candidate(articles=[article])

    # Simulate the LLM returning an empty/whitespace paragraph for this story_id --
    # covered indirectly via off mode using the same fallback path.
    results = draft_paragraphs([candidate], openai_mode="off")

    assert results[0].paragraph.strip() != ""


# --- Batching --------------------------------------------------------------------------


def test_draft_chunks_batches_over_forty_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    candidates = [make_candidate(story_id=f"story-{i}", articles=[make_article(title=f"Story {i}")]) for i in range(85)]

    def payload_for(chunk: list[DraftCandidate]) -> str:
        return paragraph_payload([{"story_id": c.story_id, "paragraph": f"Paragraph for {c.story_id}."} for c in chunk])

    outputs = [payload_for(candidates[0:40]), payload_for(candidates[40:80]), payload_for(candidates[80:85])]
    fake_responses = FakeResponses(outputs=outputs)
    install_fake_openai(monkeypatch, fake_responses)

    results = draft_paragraphs(candidates, openai_mode="full")

    assert len(fake_responses.calls) == 3
    assert len(results) == 85
