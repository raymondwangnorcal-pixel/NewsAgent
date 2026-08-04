from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from news_agent.models import OpenAICostConfig
from news_agent.openai_budget import OpenAIBudget
from news_agent.openai_client import request_structured_response


def _request(
    monkeypatch: pytest.MonkeyPatch,
    *,
    response: object,
    reasoning_effort: str = "",
) -> tuple[object, dict[str, object], OpenAIBudget]:
    captured: dict[str, object] = {}

    class FakeResponses:
        def create(self, **kwargs: object) -> object:
            captured.update(kwargs)
            return response

    class FakeOpenAI:
        def __init__(self) -> None:
            self.responses = FakeResponses()

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=FakeOpenAI))
    budget = OpenAIBudget(OpenAICostConfig())
    outcome = request_structured_response(
        stage="test_stage",
        budget_stage="test_stage",
        default_model="gpt-5.6-terra",
        system_prompt="System",
        user_payload="Payload",
        schema_name="test_schema",
        schema={
            "type": "object",
            "additionalProperties": False,
            "properties": {},
            "required": [],
        },
        max_output_tokens=100,
        budget=budget,
        reasoning_effort=reasoning_effort,
    )
    return outcome, captured, budget


@pytest.mark.parametrize(
    ("reasoning_effort", "expected"),
    [
        ("", None),
        ("medium", {"effort": "medium"}),
    ],
)
def test_structured_response_includes_reasoning_only_when_requested(
    monkeypatch: pytest.MonkeyPatch,
    reasoning_effort: str,
    expected: object,
) -> None:
    response = SimpleNamespace(
        status="completed",
        output_text="{}",
        usage=SimpleNamespace(input_tokens=10, output_tokens=5),
    )

    outcome, captured, _budget = _request(
        monkeypatch,
        response=response,
        reasoning_effort=reasoning_effort,
    )

    assert outcome.response is response
    assert captured.get("reasoning") == expected


def test_incomplete_response_records_usage_and_specific_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = SimpleNamespace(
        status="incomplete",
        output_text="",
        usage=SimpleNamespace(input_tokens=100, output_tokens=20),
    )

    outcome, _captured, budget = _request(monkeypatch, response=response)

    assert outcome.response is None
    assert outcome.error_code == "test_stage_incomplete_response"
    assert budget.input_tokens == 100
    assert budget.output_tokens == 20
    assert budget.failures_by_stage == {
        "test_stage": {"test_stage_incomplete_response": 1}
    }
