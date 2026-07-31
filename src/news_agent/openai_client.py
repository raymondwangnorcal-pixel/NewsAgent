from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import Any

from news_agent.openai_budget import OpenAIBudget, conservative_request_cost_usd


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StructuredResponseOutcome:
    response: Any | None = None
    error_code: str = ""
    budget_exhausted: bool = False


def configured_openai_model(default_model: str) -> str:
    """Use OPENAI_MODEL when supplied, falling back to the checked-in default."""

    return os.getenv("OPENAI_MODEL", "").strip() or default_model


def request_structured_response(
    *,
    stage: str,
    budget_stage: str,
    default_model: str,
    system_prompt: str,
    user_payload: str,
    schema_name: str,
    schema: dict[str, Any],
    max_output_tokens: int,
    budget: OpenAIBudget,
    use_watchlist_reserve: bool = False,
    reasoning_effort: str = "",
) -> StructuredResponseOutcome:
    """Make one budgeted Responses API request with uniform failure reporting."""

    budget.record_attempt(budget_stage)
    estimate = conservative_request_cost_usd(
        system_prompt,
        user_payload,
        max_output_tokens,
        budget.config,
    )
    if not budget.can_start(estimate, use_watchlist_reserve=use_watchlist_reserve):
        error_code = f"{stage}_budget_exhausted"
        budget.record_failure(budget_stage, error_code)
        return StructuredResponseOutcome(error_code=error_code, budget_exhausted=True)

    try:
        from openai import OpenAI
    except ImportError:
        error_code = f"{stage}_openai_not_installed"
        logger.warning("%s: openai package is not installed", stage)
        budget.record_failure(budget_stage, error_code)
        return StructuredResponseOutcome(error_code=error_code)

    try:
        request_options: dict[str, Any] = {
            "model": configured_openai_model(default_model),
            "input": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_payload},
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": schema_name,
                    "strict": True,
                    "schema": schema,
                }
            },
            "max_output_tokens": max_output_tokens,
        }
        if reasoning_effort:
            request_options["reasoning"] = {"effort": reasoning_effort}
        response = OpenAI().responses.create(
            **request_options,
        )
    except Exception:
        error_code = f"{stage}_api_error"
        logger.warning("%s: OpenAI request failed", stage, exc_info=True)
        budget.record_failure(budget_stage, error_code)
        return StructuredResponseOutcome(error_code=error_code)

    usage = getattr(response, "usage", None)
    budget.record(
        budget_stage,
        _usage_value(usage, "input_tokens"),
        _usage_value(usage, "output_tokens"),
    )
    if getattr(response, "status", "") == "incomplete":
        error_code = f"{stage}_incomplete_response"
        budget.record_failure(budget_stage, error_code)
        return StructuredResponseOutcome(error_code=error_code)
    budget.record_success(budget_stage)
    return StructuredResponseOutcome(response=response)


def _usage_value(usage: object, name: str) -> int:
    if isinstance(usage, dict):
        return int(usage.get(name, 0) or 0)
    return int(getattr(usage, name, 0) or 0)
