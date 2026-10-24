"""Validated settings and explicit override resolution, with no browser side effects."""

import re
import tomllib
from copy import deepcopy
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import (
    ConfigDict,
    Field,
    StrictBool,
    field_serializer,
    field_validator,
    model_validator,
)

from .types import ContractModel, HomepageUrl, NonEmptyText

PositiveInt = Annotated[int, Field(strict=True, gt=0)]
NonNegativeInt = Annotated[int, Field(strict=True, ge=0)]
PositiveSeconds = Annotated[float, Field(strict=True, gt=0)]
NonNegativeSeconds = Annotated[float, Field(strict=True, ge=0)]


class SelectionConfig(ContractModel):
    """Decision policy, without credentials or browser/network side effects."""

    mode: Literal["llm", "structural"] = "llm"
    model: Literal["gpt-6-luna"] = "gpt-6-luna"
    objective: Annotated[NonEmptyText, Field(max_length=4000)] = (
        "Find company-specific evidence about products and services, target customers, pricing, "
        "integrations, differentiators, commercial activity, and careers or hiring. Favor "
        "concrete capabilities, stated limitations, customer evidence, and company developments "
        "that support factual competitor analysis. Include articles when they reveal the "
        "company's capabilities or strategy."
    )
    budget_usd: Annotated[float, Field(strict=True, ge=0)] = 0.03
    exploration_fraction: Annotated[float, Field(strict=True, ge=0, le=1)] = 0.20
    trace_enabled: StrictBool = False
    timeout_seconds: PositiveSeconds = 15.0
    max_retries: NonNegativeInt = 2
    max_concurrency: PositiveInt = 2


class DynamicSettings(ContractModel):
    wait_for_css: NonEmptyText | None = None
    wait_timeout_seconds: PositiveSeconds = 10.0
    scan_full_page: StrictBool = False
    max_scroll_steps: PositiveInt = 5
    scroll_delay_seconds: NonNegativeSeconds = 0.3


class DynamicRule(DynamicSettings):
    """URL glob plus bounded, operator-configured interactions for Stage 3."""

    url_pattern: NonEmptyText
    load_more_selector: NonEmptyText | None = None
    max_clicks: NonNegativeInt = 5


class ScraperConfig(ContractModel):
    """Also represents partial overrides via Pydantic's explicitly set fields.

    Always use resolve_config(), rather than model_copy(update=...), to merge settings.
    """

    strategy: Literal["best_first", "bfs"] = "best_first"
    selection: SelectionConfig = Field(default_factory=SelectionConfig)
    max_pages: PositiveInt = 50
    max_depth: NonNegativeInt = 3
    max_discovered_urls: PositiveInt = 5000
    max_sitemap_documents: PositiveInt = 20
    max_sitemap_requests: PositiveInt = 50
    max_sitemap_depth: NonNegativeInt = 3
    max_sitemap_bytes: PositiveInt = 2_097_152
    max_redirects: NonNegativeInt = 5
    max_concurrency: PositiveInt = 4
    max_concurrent_competitors: PositiveInt = 2
    per_domain_concurrency: PositiveInt = 1
    request_delay_seconds: tuple[NonNegativeSeconds, NonNegativeSeconds] = (1.0, 2.0)
    page_timeout_seconds: PositiveSeconds = 45.0
    capacity_wait_timeout_seconds: PositiveSeconds = 120.0
    discovery_timeout_seconds: PositiveSeconds = 30.0
    max_retries: NonNegativeInt = 2
    max_backoff_seconds: PositiveSeconds = 30.0
    respect_robots_txt: Literal[True] = True
    user_agent: NonEmptyText = "ScraperBot/0.1"
    allowed_domains: list[NonEmptyText] = Field(default_factory=list)
    excluded_url_patterns: list[NonEmptyText] = Field(
        default_factory=lambda: [
            "*/login*",
            "*/logout*",
            "*/signin*",
            "*/sign-in*",
            "*/signup*",
            "*/sign-up*",
            "*/account/*",
            "*/admin/*",
            "*/privacy*",
            "*/terms*",
        ]
    )
    markdown_remove_navigation_and_footer: StrictBool = True
    markdown_excluded_selectors: list[NonEmptyText] = Field(default_factory=list)
    dynamic: DynamicSettings = Field(default_factory=DynamicSettings)
    dynamic_rules: list[DynamicRule] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def reject_keyword_settings(cls, value):
        if isinstance(value, dict):
            obsolete = sorted(
                set(value)
                & {
                    "category_keywords",
                    "minimum_relevance_score",
                    "url_keyword_weight",
                    "title_keyword_weight",
                }
            )
            if obsolete:
                raise ValueError(
                    "Removed keyword settings: " + ", ".join(obsolete) + ". "
                    "Remove these fields. Use selection.objective for research relevance, "
                    "selection.mode='structural' for API-free selection, or strategy='bfs' "
                    "for breadth-first traversal. Categories now use fetched content."
                )
        return value

    @field_validator("request_delay_seconds")
    @classmethod
    def ordered_delays(cls, value: tuple[float, float]) -> tuple[float, float]:
        if value[0] > value[1]:
            raise ValueError("Request delay minimum must not exceed maximum")
        return value

    @field_validator("markdown_excluded_selectors")
    @classmethod
    def valid_markdown_selectors(cls, values: list[str]) -> list[str]:
        from cssselect import SelectorError
        from lxml import etree

        from .markdown_cleaning import compile_markdown_selector

        for selector in values:
            try:
                compile_markdown_selector(selector)
            except (SelectorError, etree.XPathError) as error:
                raise ValueError(
                    f"Invalid Markdown exclusion CSS selector: {selector!r}"
                ) from error
        return values

    @field_validator("allowed_domains")
    @classmethod
    def exact_hostnames(cls, values: list[str]) -> list[str]:
        normalized = []
        for value in values:
            host = value.lower().rstrip(".")
            labels = host.split(".")
            if (
                len(host) > 253
                or len(labels) < 2
                or not all(
                    re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels
                )
            ):
                raise ValueError(
                    "Allowed domains must be exact hostnames, without URLs or wildcards"
                )
            if host not in normalized:
                normalized.append(host)
        return normalized


class CompetitorInput(ContractModel):
    homepage_url: HomepageUrl
    name: NonEmptyText | None = None
    overrides: ScraperConfig = Field(default_factory=ScraperConfig)

    @field_serializer("overrides")
    def serialize_overrides(self, value: ScraperConfig) -> dict[str, Any]:
        return value.model_dump(mode="json", exclude_unset=True)

    @property
    def display_name(self) -> str:
        return self.name or self.homepage_url.host.removeprefix("www.")


class ProjectConfig(ContractModel):
    model_config = ConfigDict(validate_by_name=True)

    global_settings: ScraperConfig = Field(default_factory=ScraperConfig, alias="global")
    competitors: list[CompetitorInput] = Field(default_factory=list)


def _merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    result = deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = deepcopy(value)
    return result


def resolve_config(
    global_settings: ScraperConfig | None = None,
    competitor_overrides: ScraperConfig | None = None,
    cli_overrides: ScraperConfig | None = None,
) -> ScraperConfig:
    """Merge defaults < global < competitor < CLI, then validate the complete result.

    Nested maps merge; lists replace, including explicitly empty lists. Only explicitly
    provided fields participate, so an override's defaults cannot reset global settings.
    """
    data = ScraperConfig().model_dump()
    for layer in (global_settings, competitor_overrides, cli_overrides):
        if layer is not None:
            data = _merge(data, layer.model_dump(exclude_unset=True))
    return ScraperConfig.model_validate(data)


def load_config(path: str | Path) -> ProjectConfig:
    """Read UTF-8 TOML; invalid settings and file errors are reported to the caller."""
    with Path(path).open("rb") as stream:
        return ProjectConfig.model_validate(tomllib.load(stream))
