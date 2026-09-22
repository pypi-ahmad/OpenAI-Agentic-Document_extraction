"""Token usage accounting and standard-tier GPT-6 Sol model pricing.

Responsible for tracking token counters (input, cached, write, output, reasoning),
validating token invariants, and computing exact USD costs using model rate cards.
Must NOT call billing APIs, make external requests, or modify configuration state.
Next: ade_app.spending for evaluation budget bounds, or ade_app.pipeline for runtime cost recording.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

from ade_app.config import ModelSettings

if TYPE_CHECKING:
    from ade_app.config import PipelineConfig

ONE_MILLION = Decimal(1_000_000)
_DEFAULT_RATES = ModelSettings()
MODEL_RATES = {
    "gpt-6-sol": (
        _DEFAULT_RATES.input_rate,
        _DEFAULT_RATES.cached_input_rate,
        _DEFAULT_RATES.cache_write_rate,
        _DEFAULT_RATES.output_rate,
    )
}


def configure_model_rates(config: PipelineConfig) -> None:
    """Install validated rates for the active process configuration."""

    for model in (config.model,):
        MODEL_RATES[model.name] = (
            model.input_rate,
            model.cached_input_rate,
            model.cache_write_rate,
            model.output_rate,
        )


# Backward-compatible aliases used by the evaluation report.
INPUT_RATE, CACHED_INPUT_RATE, CACHE_WRITE_RATE, OUTPUT_RATE = MODEL_RATES["gpt-6-sol"]


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """Token counters used for exact, model-specific cost estimation."""

    input_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    long_input_tokens: int = 0
    long_cached_tokens: int = 0
    long_write_tokens: int = 0
    long_output_tokens: int = 0

    def __post_init__(self) -> None:
        values = (
            self.input_tokens,
            self.cached_input_tokens,
            self.cache_write_tokens,
            self.output_tokens,
            self.reasoning_tokens,
            self.long_input_tokens,
            self.long_cached_tokens,
            self.long_write_tokens,
            self.long_output_tokens,
        )
        if any(value < 0 for value in values):
            raise ValueError("token counts cannot be negative")
        if self.cached_input_tokens > self.input_tokens:
            raise ValueError("cached input tokens cannot exceed input tokens")
        if self.cached_input_tokens + self.cache_write_tokens > self.input_tokens:
            raise ValueError("cached input and cache-write tokens cannot exceed input tokens")

    def __add__(self, other: TokenUsage) -> TokenUsage:
        return TokenUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            cached_input_tokens=self.cached_input_tokens + other.cached_input_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            reasoning_tokens=self.reasoning_tokens + other.reasoning_tokens,
            long_input_tokens=self.long_input_tokens + other.long_input_tokens,
            long_cached_tokens=self.long_cached_tokens + other.long_cached_tokens,
            long_write_tokens=self.long_write_tokens + other.long_write_tokens,
            long_output_tokens=self.long_output_tokens + other.long_output_tokens,
        )


def calculate_cost(usage: TokenUsage, model: str = "gpt-6-sol") -> Decimal:
    """Calculate USD using the configured model's official rates."""

    try:
        input_rate, cached_rate, cache_write_rate, output_rate = MODEL_RATES[model]
    except KeyError as error:
        raise ValueError(f"unsupported pricing model: {model}") from error
    uncached = usage.input_tokens - usage.cached_input_tokens - usage.cache_write_tokens
    return (
        Decimal(uncached) * input_rate
        + Decimal(usage.cached_input_tokens) * cached_rate
        + Decimal(usage.cache_write_tokens) * cache_write_rate
        + Decimal(usage.output_tokens) * output_rate
        + Decimal(usage.long_input_tokens - usage.long_cached_tokens - usage.long_write_tokens)
        * input_rate
        + Decimal(usage.long_cached_tokens) * cached_rate
        + Decimal(usage.long_write_tokens) * cache_write_rate
        + Decimal(usage.long_output_tokens) * output_rate / 2
    ) / ONE_MILLION
