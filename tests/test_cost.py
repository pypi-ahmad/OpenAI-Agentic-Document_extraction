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
        (TokenUsage(output_tokens=1_000_000), Decimal("12.00")),
        (
            TokenUsage(
                input_tokens=1_000_000,
                cached_input_tokens=250_000,
                cache_write_tokens=500_000,
                output_tokens=100_000,
            ),
            Decimal("3.00"),
        ),
    ],
)
def test_calculate_cost(usage: TokenUsage, expected: Decimal) -> None:
    assert calculate_cost(usage) == expected


def test_model_specific_costs_use_terra_and_sol_rates() -> None:
    usage = TokenUsage(input_tokens=1_000_000, output_tokens=1_000_000)

    assert calculate_cost(usage, "gpt-5.6-terra") == Decimal("14")
    assert calculate_cost(usage, "gpt-5.6-sol") == Decimal("24")


def test_usage_rejects_cached_tokens_above_total() -> None:
    with pytest.raises(ValueError, match="cannot exceed"):
        TokenUsage(input_tokens=1, cached_input_tokens=2)


def test_usage_rejects_cached_and_cache_write_tokens_above_total() -> None:
    with pytest.raises(ValueError, match="cache-write"):
        TokenUsage(input_tokens=10, cached_input_tokens=6, cache_write_tokens=5)


def test_calculate_cost_uses_luna_rate() -> None:
    usage = TokenUsage(input_tokens=1_000_000, output_tokens=1_000_000)
    assert calculate_cost(usage, "gpt-5.6-luna") == Decimal("1.40")


def test_calculate_cost_rejects_unknown_model() -> None:
    with pytest.raises(ValueError, match="unsupported pricing model"):
        calculate_cost(TokenUsage(), "other-model")
