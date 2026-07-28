from __future__ import annotations

from dataclasses import dataclass, field

from news_agent.models import OpenAICostConfig


REQUEST_OVERHEAD_TOKENS = 512


def calculate_openai_cost_usd(
    input_tokens: int,
    output_tokens: int,
    config: OpenAICostConfig,
) -> float:
    return (
        input_tokens * config.input_cost_usd_per_million_tokens
        + output_tokens * config.output_cost_usd_per_million_tokens
    ) / 1_000_000


def conservative_request_cost_usd(
    system_prompt: str,
    user_payload: str,
    max_output_tokens: int,
    config: OpenAICostConfig,
) -> float:
    conservative_input_tokens = (
        len(system_prompt.encode("utf-8"))
        + len(user_payload.encode("utf-8"))
        + REQUEST_OVERHEAD_TOKENS
    )
    return calculate_openai_cost_usd(
        conservative_input_tokens,
        max_output_tokens,
        config,
    )


@dataclass
class OpenAIBudget:
    config: OpenAICostConfig
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    exhausted: bool = False
    cost_by_stage: dict[str, float] = field(default_factory=dict)
    attempts_by_stage: dict[str, int] = field(default_factory=dict)
    successes_by_stage: dict[str, int] = field(default_factory=dict)
    failures_by_stage: dict[str, dict[str, int]] = field(default_factory=dict)

    @property
    def remaining_usd(self) -> float:
        return max(0.0, self.config.max_cost_usd_per_run - self.cost_usd)

    def can_start(self, estimated_cost_usd: float, *, use_watchlist_reserve: bool = False) -> bool:
        available = self.remaining_usd if use_watchlist_reserve else max(
            0.0,
            self.remaining_usd - self.config.watchlist_reserve_usd,
        )
        if estimated_cost_usd > available:
            self.exhausted = True
            return False
        return True

    def record(self, stage: str, input_tokens: int, output_tokens: int) -> float:
        cost = calculate_openai_cost_usd(input_tokens, output_tokens, self.config)
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        self.cost_usd += cost
        self.cost_by_stage[stage] = self.cost_by_stage.get(stage, 0.0) + cost
        if self.cost_usd >= self.config.max_cost_usd_per_run:
            self.exhausted = True
        return cost

    def record_attempt(self, stage: str) -> None:
        self.attempts_by_stage[stage] = self.attempts_by_stage.get(stage, 0) + 1

    def record_success(self, stage: str) -> None:
        self.successes_by_stage[stage] = self.successes_by_stage.get(stage, 0) + 1

    def record_failure(self, stage: str, reason: str) -> None:
        stage_failures = self.failures_by_stage.setdefault(stage, {})
        stage_failures[reason] = stage_failures.get(reason, 0) + 1

    def stage_outcomes(self) -> dict[str, dict[str, object]]:
        stages = set(self.attempts_by_stage) | set(self.successes_by_stage) | set(self.failures_by_stage)
        return {
            stage: {
                "requested": self.attempts_by_stage.get(stage, 0),
                "successful": self.successes_by_stage.get(stage, 0),
                "fallbacks": sum(self.failures_by_stage.get(stage, {}).values()),
                "reasons": dict(self.failures_by_stage.get(stage, {})),
            }
            for stage in sorted(stages)
        }
