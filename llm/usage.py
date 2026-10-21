"""Observed Responses usage and standard USD estimates, separate from analysis schemas."""

import re
from dataclasses import dataclass, field
from decimal import Decimal


@dataclass(frozen=True)
class TokenRates:
    """USD per million tokens; cache-write rates are total rates, not surcharges."""

    input: Decimal
    cached_input: Decimal
    output: Decimal
    cache_write: Decimal

    def __post_init__(self) -> None:
        if any(
            not rate.is_finite() or rate < 0
            for rate in (self.input, self.cached_input, self.output, self.cache_write)
        ):
            raise ValueError("Token rates must be finite and nonnegative")


# Verified 2026-10-20: https://developers.openai.com/api/docs/pricing
# https://developers.openai.com/api/docs/models/gpt-4o-mini
# Standard short-context pricing, default 30-minute cache writes (GPT-6).
STANDARD_RATES = {
    model: TokenRates(*(Decimal(rate) for rate in rates))
    for model, rates in {
        "gpt-6-luna": ("0.10", "0.01", "0.50", "0.125"),
        "gpt-6.1-sol": ("2.00", "0.10", "10.00", "2.50"),
        "gpt-6-astra": ("10.00", "1.00", "50.00", "12.50"),
        "gpt-4o-mini": ("0.15", "0.075", "0.60", "0.15"),
    }.items()
}


@dataclass(frozen=True)
class UsageRecord:
    model: str
    input_tokens: int
    cached_input_tokens: int
    cache_write_tokens: int
    output_tokens: int
    service_tier: str | None = None


@dataclass
class LLMUsage:
    """One client's requests, including requests whose usage could not be observed."""

    request_count: int = 0
    responses: list[UsageRecord] = field(default_factory=list)

    def estimated_cost_usd(self, override: TokenRates | None = None) -> Decimal | None:
        """Return None when usage or applicable pricing is unavailable, never a fake zero.

        Overrides must describe the effective rates for the entire run. Output tokens
        already include reasoning tokens. SDK retries without returned usage are not
        observable here, so this is an estimate rather than an invoice total.
        """
        if self.request_count != len(self.responses):
            return None
        total = Decimal(0)
        for response in self.responses:
            model = re.sub(r"-\d{4}-\d{2}-\d{2}$", "", response.model)
            rates = override or STANDARD_RATES.get(model)
            if rates is None:
                return None
            if override is None and (
                response.service_tier not in (None, "default", "standard")
                or (model.startswith("gpt-6") and response.input_tokens > 272000)
            ):
                return None
            ordinary_input = (
                response.input_tokens
                - response.cached_input_tokens
                - response.cache_write_tokens
            )
            if (
                min(
                    ordinary_input,
                    response.cached_input_tokens,
                    response.cache_write_tokens,
                    response.output_tokens,
                )
                < 0
            ):
                return None
            total += (
                ordinary_input * rates.input
                + response.cached_input_tokens * rates.cached_input
                + response.cache_write_tokens * rates.cache_write
                + response.output_tokens * rates.output
            ) / 1000000
        return total
