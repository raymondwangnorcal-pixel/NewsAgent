from __future__ import annotations

import pytest

from news_agent.models import OpenAICostConfig
from news_agent.openai_budget import (
    OpenAIBudget,
    calculate_openai_cost_usd,
    conservative_request_cost_usd,
)


def test_shared_budget_accumulates_every_openai_stage() -> None:
    budget = OpenAIBudget(OpenAICostConfig())

    budget.record("quality_judging", 1_000, 100)
    budget.record("classification", 2_000, 200)
    budget.record("drafting", 3_000, 300)
    budget.record("compression", 4_000, 400)

    assert budget.input_tokens == 10_000
    assert budget.output_tokens == 1_000
    assert budget.cost_usd == pytest.approx(0.04)
    assert budget.cost_usd == pytest.approx(sum(budget.cost_by_stage.values()))
    assert set(budget.cost_by_stage) == {
        "quality_judging",
        "classification",
        "drafting",
        "compression",
    }


def test_shared_budget_refuses_request_that_would_exceed_one_dollar_cap() -> None:
    budget = OpenAIBudget(OpenAICostConfig())
    budget.record("classification", 100_000, 40_000)

    assert budget.cost_usd == pytest.approx(0.85)
    assert budget.can_start(0.15) is True
    assert budget.can_start(0.150001) is False
    assert budget.exhausted is True


def test_stage_outcomes_record_requests_successes_and_fallback_reasons() -> None:
    budget = OpenAIBudget(OpenAICostConfig())

    budget.record_attempt("classification")
    budget.record_success("classification")
    budget.record_attempt("classification")
    budget.record_failure("classification", "classification_api_error")

    assert budget.stage_outcomes() == {
        "classification": {
            "requested": 2,
            "successful": 1,
            "fallbacks": 1,
            "reasons": {"classification_api_error": 1},
        }
    }


def test_conservative_estimate_prices_input_and_maximum_output() -> None:
    config = OpenAICostConfig()
    estimate = conservative_request_cost_usd(
        "system",
        "payload",
        max_output_tokens=1_200,
        config=config,
    )
    expected_input_tokens = len(b"system") + len(b"payload") + 512

    assert estimate == pytest.approx(
        calculate_openai_cost_usd(expected_input_tokens, 1_200, config)
    )
