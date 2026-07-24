from __future__ import annotations

import json
import sys
from dataclasses import replace
from types import SimpleNamespace

import pytest

from news_agent.compress import (
    COMPRESS_SYSTEM_PROMPT,
    compress_paragraphs,
    compress_paragraphs_result,
    fidelity_guard,
    protected_fact_tokens,
    semantic_anchor_tokens,
)
from news_agent.models import BriefingParagraph, CompressionConfig, OpenAICostConfig
from news_agent.openai_budget import OpenAIBudget


def paragraph(
    text: str,
    *,
    story_id: str = "story-1",
    draft_status: str = "llm",
) -> BriefingParagraph:
    return BriefingParagraph(
        story_id=story_id,
        category="finance",
        paragraph=text,
        sources=("Reuters",),
        draft_status=draft_status,  # type: ignore[arg-type]
    )


def enabled_config(**changes: object) -> CompressionConfig:
    config = CompressionConfig(
        enabled=True,
        min_words_to_compress=1,
        min_words_floor=3,
    )
    return replace(config, **changes)


class FakeResponse:
    def __init__(self, output_text: str, input_tokens: int = 100, output_tokens: int = 30) -> None:
        self.output_text = output_text
        self.usage = SimpleNamespace(input_tokens=input_tokens, output_tokens=output_tokens)


class FakeResponses:
    def __init__(self, response: FakeResponse | None = None, error: Exception | None = None) -> None:
        self.response = response
        self.error = error
        self.calls: list[dict] = []

    def create(self, **kwargs: object) -> FakeResponse:
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        assert self.response is not None
        return self.response


class FakeOpenAI:
    responses: FakeResponses

    def __init__(self) -> None:
        self.responses = type(self).responses


class FakeOpenAIModule:
    OpenAI = FakeOpenAI


def install_fake_openai(monkeypatch: pytest.MonkeyPatch, responses: FakeResponses) -> None:
    FakeOpenAI.responses = responses
    monkeypatch.setitem(sys.modules, "openai", FakeOpenAIModule())


def compression_payload(story_id: str, compressed: str) -> str:
    return json.dumps({"compressions": [{"story_id": story_id, "compressed": compressed}]})


def test_compression_prompt_has_no_hard_word_target() -> None:
    assert "as short as possible" in COMPRESS_SYSTEM_PROMPT
    assert "without losing or changing any meaning" in COMPRESS_SYSTEM_PROMPT
    assert "35 words" not in COMPRESS_SYSTEM_PROMPT
    assert "target words" not in COMPRESS_SYSTEM_PROMPT.casefold()


def test_protected_facts_normalize_equivalent_percent_and_currency_forms() -> None:
    left = protected_fact_tokens("Revenue rose 12 percent to 1.2 billion dollars in Q2 2026.")
    right = protected_fact_tokens("Revenue rose 12% to $1.2 billion in Q2 2026.")

    assert left == right


@pytest.mark.parametrize(
    ("original", "compressed"),
    [
        (
            "Reuters said revenue rose 12% to $1.2 billion on Wednesday in Q2 2026.",
            "Reuters said revenue rose 12 percent to 1.2 billion dollars Wednesday in Q2 2026.",
        ),
        (
            "The company cut 2,500 jobs and saved €20 million in 2025.",
            "The company cut 2500 jobs, saving 20 million euros in 2025.",
        ),
    ],
)
def test_fidelity_guard_accepts_normalized_equivalents(original: str, compressed: str) -> None:
    assert fidelity_guard(original, compressed).passed


def test_fidelity_guard_rejects_dropped_added_and_altered_figures() -> None:
    dropped = fidelity_guard("Revenue rose 12% to $1.2 billion.", "Revenue rose to $1.2 billion.")
    added = fidelity_guard("Revenue rose 12%.", "Revenue rose 12% to $1.2 billion.")
    altered = fidelity_guard("Revenue reached $1.23 billion.", "Revenue reached $1.2 billion.")

    assert dropped.missing_tokens == ("num:12:%",)
    assert "usd:1.2:billion" in added.added_tokens
    assert not altered.passed


def test_fidelity_guard_rejects_changed_numeric_sign() -> None:
    dropped = fidelity_guard("Shares fell -5.2%.", "Shares fell 5.2%.")
    reversed_sign = fidelity_guard("Shares fell -5.2%.", "Shares rose +5.2%.")

    assert not dropped.passed
    assert "num:-5.2:%" in dropped.missing_tokens
    assert not reversed_sign.passed


def test_entity_guard_is_warning_only_unless_enabled() -> None:
    original = "Reuters said Microsoft acquired Contoso."
    compressed = "Reuters said the acquisition occurred."

    soft = fidelity_guard(original, compressed, guard_entities=False)
    hard = fidelity_guard(original, compressed, guard_entities=True)

    assert soft.passed
    assert soft.dropped_entities == ("Contoso", "Microsoft")
    assert not hard.passed


@pytest.mark.parametrize(
    ("original", "compressed", "missing", "added"),
    [
        (
            "Reuters said Microsoft may release the product.",
            "Reuters said Microsoft will release the product.",
            "modality:may",
            "modality:will",
        ),
        (
            "Reuters said Microsoft did not release the product.",
            "Reuters said Microsoft released the product.",
            "negation:not",
            "",
        ),
        (
            "Reuters said Microsoft delayed the product because of safety concerns.",
            "Microsoft delayed the product.",
            "attribution:said",
            "",
        ),
    ],
)
def test_semantic_anchor_guard_rejects_changed_meaning(
    original: str,
    compressed: str,
    missing: str,
    added: str,
) -> None:
    guard = fidelity_guard(original, compressed)

    assert not guard.passed
    assert missing in guard.missing_anchors
    if added:
        assert added in guard.added_anchors


def test_semantic_anchor_extraction_preserves_causal_phrase() -> None:
    anchors = semantic_anchor_tokens("Sales fell because of weak demand and may remain low.")

    assert anchors["causal:because of"] == 1
    assert anchors["modality:may"] == 1


def test_dropped_entity_forces_original_to_be_retained(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = "Reuters said Microsoft acquired Contoso after a lengthy competitive review by regulators."
    compressed = "Reuters said Microsoft completed the acquisition after regulators' review."
    install_fake_openai(
        monkeypatch,
        FakeResponses(FakeResponse(compression_payload("story-1", compressed))),
    )

    result = compress_paragraphs_result(
        [paragraph(original)],
        enabled_config(),
    )

    assert result.paragraphs[0].paragraph == original
    assert result.paragraphs[0].compression_status == "kept_original_guard_failed"
    assert result.entity_warnings == 1
    assert result.guard_deltas == {
        "story-1": {
            "missing_tokens": [],
            "added_tokens": [],
            "dropped_entities": ["Contoso"],
            "missing_anchors": [],
            "added_anchors": [],
        }
    }


def test_happy_path_accepts_shortest_faithful_compression(monkeypatch: pytest.MonkeyPatch) -> None:
    original = (
        "Reuters said the Federal Reserve kept rates at 5.25% on Wednesday after officials "
        "reviewed inflation data, and the central bank repeated that future decisions would "
        "depend on incoming evidence. The decision matters because borrowing costs remain high "
        "for households and businesses across the United States."
    )
    compressed = (
        "Reuters said the Federal Reserve kept rates at 5.25% Wednesday; future decisions would "
        "depend on data. The decision matters because borrowing costs remain high for United States "
        "households and businesses."
    )
    responses = FakeResponses(FakeResponse(compression_payload("story-1", compressed)))
    install_fake_openai(monkeypatch, responses)

    result = compress_paragraphs_result([paragraph(original)], enabled_config())

    assert result.paragraphs[0].paragraph == compressed
    assert result.paragraphs[0].full_paragraph == original
    assert result.paragraphs[0].compression_status == "compressed"
    assert result.paragraphs[0].compression_ratio >= 0.20
    assert result.input_tokens == 100
    assert result.output_tokens == 30
    assert result.cost_usd > 0
    assert responses.calls[0]["model"] == "gpt-5.6-terra"
    assert responses.calls[0]["max_output_tokens"] == 1200


def test_guard_failure_keeps_original_and_records_delta(monkeypatch: pytest.MonkeyPatch) -> None:
    original = "Reuters said revenue rose 12% in 2026 after a sustained expansion across all major markets."
    compressed = "Reuters said revenue rose after expansion."
    install_fake_openai(
        monkeypatch,
        FakeResponses(FakeResponse(compression_payload("story-1", compressed))),
    )

    result = compress_paragraphs_result([paragraph(original)], enabled_config())

    assert result.paragraphs[0].paragraph == original
    assert result.paragraphs[0].compression_status == "kept_original_guard_failed"
    assert result.guard_deltas
    assert "num:12:%" in result.guard_deltas["story-1"]["missing_tokens"]


def test_no_gain_keeps_original(monkeypatch: pytest.MonkeyPatch) -> None:
    original = "Reuters reported a policy decision that retained every existing condition without any material change."
    install_fake_openai(
        monkeypatch,
        FakeResponses(FakeResponse(compression_payload("story-1", original))),
    )

    result = compress_paragraphs([paragraph(original)], enabled_config())

    assert result[0].paragraph == original
    assert result[0].compression_status == "kept_original_no_gain"


def test_any_faithful_word_count_reduction_is_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = "Reuters reported the company retained every existing policy condition without any material changes."
    compressed = "Reuters reported the company retained every policy condition without material changes."
    install_fake_openai(
        monkeypatch,
        FakeResponses(FakeResponse(compression_payload("story-1", compressed))),
    )

    result = compress_paragraphs(
        [paragraph(original)],
        enabled_config(min_words_floor=3),
    )

    assert result[0].paragraph == compressed
    assert result[0].compression_status == "compressed"
    assert result[0].compression_ratio == pytest.approx(2 / 13)


def test_over_shrink_below_safety_floor_keeps_original(monkeypatch: pytest.MonkeyPatch) -> None:
    original = "Reuters reported a policy decision after officials completed a lengthy review of the available evidence."
    install_fake_openai(
        monkeypatch,
        FakeResponses(FakeResponse(compression_payload("story-1", "Reuters reported."))),
    )

    result = compress_paragraphs(
        [paragraph(original)],
        enabled_config(min_words_floor=5, min_words_to_compress=5),
    )

    assert result[0].compression_status == "kept_original_guard_failed"


def test_api_error_keeps_original(monkeypatch: pytest.MonkeyPatch) -> None:
    original = "Reuters reported a policy decision after officials completed a lengthy review of the available evidence."
    install_fake_openai(monkeypatch, FakeResponses(error=RuntimeError("down")))

    result = compress_paragraphs([paragraph(original)], enabled_config())

    assert result[0].paragraph == original
    assert result[0].compression_status == "kept_original_error"


def test_disabled_short_and_fallback_paragraphs_are_never_sent(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = FakeResponses(error=AssertionError("OpenAI should not be called"))
    install_fake_openai(monkeypatch, responses)
    short = paragraph("A short original paragraph.")
    fallback = paragraph(
        "A fallback paragraph with enough repeated context to exceed the normal compression threshold. " * 3,
        story_id="fallback",
        draft_status="fallback_error",
    )

    disabled = compress_paragraphs([short], CompressionConfig(), use_openai=True)
    enabled = compress_paragraphs(
        [short, fallback],
        enabled_config(min_words_to_compress=40),
        use_openai=True,
    )

    assert disabled[0].compression_status == "disabled"
    assert disabled[0].full_paragraph == short.paragraph
    assert enabled[0].compression_status == "skipped_short"
    assert enabled[1].compression_status == "skipped_fallback_draft"
    assert enabled[1].paragraph == fallback.paragraph
    assert responses.calls == []


def test_openai_off_disables_stage_even_when_config_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = FakeResponses(error=AssertionError("OpenAI should not be called"))
    install_fake_openai(monkeypatch, responses)
    original = paragraph("A sufficiently long paragraph for compression. " * 12)

    result = compress_paragraphs([original], enabled_config(), use_openai=False)

    assert result[0].compression_status == "disabled"
    assert result[0].paragraph == original.paragraph
    assert responses.calls == []


def test_budget_exhaustion_keeps_original_without_api_call(monkeypatch: pytest.MonkeyPatch) -> None:
    responses = FakeResponses(error=AssertionError("OpenAI should not be called"))
    install_fake_openai(monkeypatch, responses)
    original = paragraph("A sufficiently long paragraph for compression. " * 12)

    result = compress_paragraphs_result(
        [original],
        enabled_config(),
        budget=OpenAIBudget(
            replace(OpenAICostConfig(), max_cost_usd_per_run=0.000001)
        ),
    )

    assert result.paragraphs[0].compression_status == "kept_original_budget_exhausted"
    assert result.budget_exhausted is True
    assert result.cost_usd == 0
    assert responses.calls == []
