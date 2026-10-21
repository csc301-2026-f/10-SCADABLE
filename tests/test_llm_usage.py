"""Cost estimates use observed billing categories, without double-counting input."""

from decimal import Decimal

import pytest

from llm.usage import LLMUsage, TokenRates, UsageRecord


def test_cached_reads_writes_and_output_are_priced_once_across_requests():
    usage = LLMUsage(
        request_count=2,
        responses=[
            UsageRecord("gpt-6-luna", 1000, 200, 300, 400),
            UsageRecord("gpt-6-luna-2026-09-22", 500, 100, 0, 200),
        ],
    )
    # 900 ordinary input + 300 cache reads + 300 writes + 600 output.
    assert usage.estimated_cost_usd() == Decimal("0.0004305")


@pytest.mark.parametrize(
    "record",
    [
        UsageRecord("unknown-model", 1000, 0, 0, 100),
        UsageRecord("gpt-6-luna", 1000, 0, 0, 100, service_tier="flex"),
        UsageRecord("gpt-6-luna", 300000, 0, 0, 100),
    ],
)
def test_unsupported_pricing_is_explicit_and_can_use_custom_rates(record):
    usage = LLMUsage(request_count=1, responses=[record])
    assert usage.estimated_cost_usd() is None
    rates = TokenRates(*(Decimal(value) for value in ("1", "0.5", "2", "1.25")))
    expected = (Decimal(record.input_tokens) + record.output_tokens * 2) / 1000000
    assert usage.estimated_cost_usd(rates) == expected


def test_unreported_request_prevents_misleading_total():
    usage = LLMUsage(
        request_count=2,
        responses=[
            UsageRecord("gpt-6-luna", 1000, 0, 0, 100),
        ],
    )
    assert usage.estimated_cost_usd() is None
    assert LLMUsage().estimated_cost_usd() == Decimal(0)


@pytest.mark.parametrize("value", ["-1", "NaN", "Infinity"])
def test_invalid_prices_are_rejected(value):
    with pytest.raises(ValueError, match="finite.*nonnegative"):
        TokenRates(Decimal(value), Decimal(0), Decimal(0), Decimal(0))
