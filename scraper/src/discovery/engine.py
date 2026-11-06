"""Shared discovery budget, policy preflight, and adaptive selection over guarded fetches."""

import asyncio
import random
from collections import Counter
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field

from ..config import CompetitorInput, ScraperConfig, resolve_config
from ..content_identity import markdown_fingerprint
from ..decision_models import DecisionTraceRecord, DecisionUsageSummary
from ..decisions import DecisionAccessError, OpenAIDecisionProvider
from ..interfaces import DecisionProvider, PageFetcher, RobotsGuard, SitemapDiscoverer
from ..models import DiscoveredUrl, FetchedPage
from ..types import ContractModel, UtcTimestamp, utc_now
from .evidence import link_observations
from .http import RequestBlocked
from .selection import AdaptiveSelection
from .traversal import UrlFrontier
from .urls import normalize_url


class DiscoveryRun(ContractModel):
    competitor: CompetitorInput
    effective_config: ScraperConfig
    started_at: UtcTimestamp
    finished_at: UtcTimestamp
    fetched_pages: list[FetchedPage] = Field(default_factory=list)
    discovered_urls: list[DiscoveredUrl] = Field(default_factory=list)
    attempted_pages: int
    discovery_truncated: bool
    completion_reason: Literal["completed", "page_limit", "discovery_limit", "failed"]
    warnings: list[str] = Field(default_factory=list)
    decision_usage: DecisionUsageSummary | None = None
    decision_trace: list[DecisionTraceRecord] = Field(default_factory=list)


class DiscoveryCrawler:
    def __init__(
        self,
        fetcher: PageFetcher,
        sitemaps: SitemapDiscoverer,
        *,
        robots_guard: RobotsGuard,
        managed_attempts: bool = False,
        decision_provider: DecisionProvider | None = None,
    ):
        self._fetcher = fetcher
        self._sitemaps = sitemaps
        self._robots = robots_guard
        self._managed_attempts = managed_attempts
        self._decision_provider = decision_provider

    async def run(
        self,
        competitor: CompetitorInput,
        global_settings: ScraperConfig | None = None,
        *,
        cli_overrides: ScraperConfig | None = None,
    ) -> DiscoveryRun:
        settings = resolve_config(global_settings, competitor.overrides, cli_overrides)
        started = utc_now()
        frontier = UrlFrontier(str(competitor.homepage_url), settings)
        pages: list[FetchedPage] = []
        warnings: list[str] = []
        pace_locks: dict[str, asyncio.Lock] = {}

        async def preflight(record):
            url = str(record.normalized_url)
            try:
                async with asyncio.timeout(settings.discovery_timeout_seconds):
                    robots_delay = await self._robots.check(url)
            except TimeoutError:
                return RequestBlocked("robots_unavailable", url)
            except RequestBlocked as error:
                return error
            return robots_delay

        async def attempt(record, robots_delay):
            url = str(record.normalized_url)
            if self._managed_attempts:
                # The service owns capacity waits, attempt deadlines, pacing and retries.
                return await fetcher.fetch(record.normalized_url, settings)
            host = urlsplit(url).hostname
            async with pace_locks.setdefault(host, asyncio.Lock()):
                interval = max(random.uniform(*settings.request_delay_seconds), robots_delay)
                if interval > 0:
                    await asyncio.sleep(interval)
            try:
                async with asyncio.timeout(settings.page_timeout_seconds):
                    return await fetcher.fetch(record.normalized_url, settings)
            except (TimeoutError, OSError) as error:
                return FetchedPage(
                    source_url=url,
                    crawl_timestamp=utc_now(),
                    crawl_status="failed",
                    error_message=f"{type(error).__name__}: {error}",
                )

        async def seed_sitemaps():
            try:
                async with asyncio.timeout(settings.discovery_timeout_seconds):
                    batch = await self._sitemaps.discover(competitor.homepage_url, settings)
                warnings.extend(batch.warnings)
                frontier.truncated |= batch.truncated
                for record in batch.urls:
                    frontier.discover(
                        str(record.url),
                        parent_url=str(record.parent_url) if record.parent_url else None,
                        depth=1,
                        source="sitemap",
                        depth_basis="sitemap_seed",
                    )
            except (TimeoutError, OSError) as error:
                warnings.append(f"sitemap_discovery_failed: {type(error).__name__}: {error}")

        seeded = False
        selection_failed = False
        provider = self._decision_provider or OpenAIDecisionProvider(settings.selection)
        async with provider as decisions, self._fetcher as fetcher:
            selection = AdaptiveSelection(frontier, settings, decisions)
            try:
                while True:
                    await selection.rank_if_needed()
                    hosts = Counter()
                    batch = []
                    while len(batch) < settings.max_concurrency:
                        record = frontier.pop_next(
                            eligible=lambda item, counts=hosts: (
                                counts[urlsplit(str(item.normalized_url)).hostname]
                                < settings.per_domain_concurrency
                            )
                        )
                        if record is None:
                            break
                        delay = await preflight(record)
                        if isinstance(delay, RequestBlocked):
                            frontier.reject_reserved(
                                str(record.normalized_url),
                                reason=delay.reason,
                                deferred=delay.reason != "robots_disallowed",
                            )
                            warnings.append(str(delay))
                            continue
                        batch.append((record, delay))
                        hosts[urlsplit(str(record.normalized_url)).hostname] += 1
                        if selection.evaluation_due:
                            break
                    if not batch:
                        break
                    async with asyncio.TaskGroup() as group:
                        tasks = [
                            group.create_task(attempt(record, delay)) for record, delay in batch
                        ]
                    # Process in dispatch order so network completion timing cannot reorder
                    # discovery evidence or priorities for the next batch.
                    assessments = []
                    for (record, _), task in zip(batch, tasks, strict=True):
                        outcome = task.result()
                        url = str(record.normalized_url)
                        if outcome.final_url and not frontier.policy.scope.allows(
                            str(outcome.final_url)
                        ):
                            frontier.discover(
                                str(outcome.final_url),
                                parent_url=url,
                                depth=record.depth,
                                title=outcome.page_title or "",
                                source="external_link",
                            )
                            outcome = FetchedPage(
                                source_url=url,
                                final_url=outcome.final_url,
                                crawl_timestamp=outcome.crawl_timestamp,
                                crawl_status="failed",
                                error_message="domain_not_approved_redirect",
                            )
                        success = outcome.crawl_status == "success"
                        frontier.complete(url, success=success, error=outcome.error_message)
                        pages.append(selection.attach(outcome, record))
                        if success:
                            if markdown_fingerprint(outcome.raw_markdown) is not None:
                                frontier.register_final_url(
                                    str(outcome.final_url or outcome.source_url), url
                                )
                            assessments.append((len(pages) - 1, record))
                            depth = frontier.get(url).depth + 1
                            observations = link_observations(outcome)
                            for link in outcome.links:
                                source = (
                                    "internal_link"
                                    if frontier.policy.scope.allows(str(link.url))
                                    else "external_link"
                                )
                                normalized = normalize_url(str(link.url))
                                contexts = (
                                    observations.get(normalized.normalized, [])
                                    if normalized
                                    else []
                                )
                                for observation in contexts or [None]:
                                    frontier.discover(
                                        str(link.url),
                                        parent_url=url,
                                        depth=depth,
                                        source=source,
                                        title=link.text or link.title or "",
                                        observation=observation,
                                    )
                    if not seeded:
                        await seed_sitemaps()
                        seeded = True
                    # All fetches have released browser slots and all outcomes are retained
                    # before a fatal decision error can stop this competitor.
                    for index, record in assessments:
                        pages[index] = await selection.assess(pages[index], record)
            except DecisionAccessError as error:
                selection_failed = True
                warnings.append(f"decision_access_failed: {error}")
        frontier.finalize(reason="decision_access_failed" if selection_failed else None)
        warnings.extend(
            f"decision_fallback: {item.reason}" for item in provider.summary.fallback_events
        )
        if frontier.invalid_links:
            warnings.append(f"invalid_links_ignored: {frontier.invalid_links}")
        records = frontier.records
        reason = (
            "failed"
            if selection_failed
            else (
                "page_limit"
                if any(item.reason == "page_limit" for item in records)
                else ("discovery_limit" if frontier.truncated else "completed")
            )
        )
        return DiscoveryRun(
            competitor=competitor,
            effective_config=settings,
            started_at=started,
            finished_at=utc_now(),
            fetched_pages=pages,
            discovered_urls=records,
            attempted_pages=frontier.attempted_pages,
            discovery_truncated=frontier.truncated,
            completion_reason=reason,
            warnings=list(dict.fromkeys(warnings)),
            decision_usage=provider.summary,
            decision_trace=provider.trace_records,
        )
