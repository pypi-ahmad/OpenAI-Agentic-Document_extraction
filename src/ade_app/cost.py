"""Token usage and official standard-tier GPT-5.6 model pricing."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

ONE_MILLION = Decimal(1_000_000)
MODEL_RATES = {
    "gpt-5.6-luna": (Decimal("0.20"), Decimal("0.02"), Decimal("0.25"), Decimal("1.20")),
    "gpt-5.6-terra": (Decimal("2.00"), Decimal("0.20"), Decimal("2.50"), Decimal("12.00")),
    "gpt-5.6-sol": (Decimal("4.00"), Decimal("0.40"), Decimal("5.00"), Decimal("20.00")),
}
# Backward-compatible aliases used by the evaluation report.
INPUT_RATE, CACHED_INPUT_RATE, CACHE_WRITE_RATE, OUTPUT_RATE = MODEL_RATES["gpt-5.6-terra"]


@dataclass(frozen=True, slots=True)
class TokenUsage:
    """Token counters used for exact, model-specific cost estimation."""

    input_tokens: int = 0
    cached_input_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0

    def __post_init__(self) -> None:
        values = (
            self.input_tokens,
            self.cached_input_tokens,
            self.cache_write_tokens,
            self.output_tokens,
            self.reasoning_tokens,
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
        )


def calculate_cost(usage: TokenUsage, model: str = "gpt-5.6-terra") -> Decimal:
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
    ) / ONE_MILLION
