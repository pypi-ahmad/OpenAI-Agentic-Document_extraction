from decimal import Decimal

import pytest

from ade_app.cost import TokenUsage, calculate_cost


@pytest.mark.parametrize(
    ("usage", "expected"),
    [
        (TokenUsage(), Decimal("0")),
        (TokenUsage(input_tokens=1_000_000), Decimal("2.00")),
        (
            TokenUsage(input_tokens=1_000_000, cached_input_tokens=1_000_000),
            Decimal("0.20"),
        ),
        (TokenUsage(output_tokens=1_000_000), Decimal("10.00")),
        (
            TokenUsage(
                input_tokens=1_000_000,
                cached_input_tokens=250_000,
                cache_write_tokens=500_000,
                output_tokens=100_000,
            ),
            Decimal("2.80"),
        ),
    ],
)
def test_calculate_cost(usage: TokenUsage, expected: Decimal) -> None:
    assert calculate_cost(usage) == expected


def test_single_model_costs() -> None:
    usage = TokenUsage(input_tokens=1_000_000, output_tokens=1_000_000)

    assert calculate_cost(usage, "gpt-6-sol") == Decimal("12")


def test_usage_rejects_cached_tokens_above_total() -> None:
    with pytest.raises(ValueError, match="cannot exceed"):
        TokenUsage(input_tokens=1, cached_input_tokens=2)


def test_usage_rejects_cached_and_cache_write_tokens_above_total() -> None:
    with pytest.raises(ValueError, match="cache-write"):
        TokenUsage(input_tokens=10, cached_input_tokens=6, cache_write_tokens=5)


def test_calculate_cost_rejects_legacy_models() -> None:
    for model in ("gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol"):
        with pytest.raises(ValueError, match="unsupported pricing model"):
            calculate_cost(TokenUsage(), model)


def test_calculate_cost_rejects_unknown_model() -> None:
    with pytest.raises(ValueError, match="unsupported pricing model"):
        calculate_cost(TokenUsage(), "other-model")
