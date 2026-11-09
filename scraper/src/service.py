"""Async, backend-independent composition of discovery, rendering and extraction."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import AsyncExitStack

import httpx

from .config import CompetitorInput, ScraperConfig, resolve_config
from .decision_tracking import DecisionTrackingContext, DecisionTrackingReference
from .discovery.engine import DiscoveryCrawler
from .discovery.http import GuardedHttpClient, RequestBlocked
from .discovery.sitemaps import BoundedSitemapDiscoverer
from .discovery.urls import normalize_url
from .extraction import MarkdownExtractor
from .interfaces import ContentExtractor, DecisionProvider, PageFetcher, RobotsGuard
from .limits import HostLimiter
from .models import CompetitorCrawlResult, ScrapedPage
from .reliability import (
    CapacityTimeout,
    failed_page,
    is_retryable,
    parse_retry_after,
    retry_delay,
    wait_before_retry,
)
from .types import UrlDisposition, utc_now

logger = logging.getLogger(__name__)
FetcherFactory = Callable[[CompetitorInput, ScraperConfig, RobotsGuard], PageFetcher]
DecisionProviderFactory = Callable[
    [CompetitorInput, ScraperConfig, asyncio.Semaphore], DecisionProvider
]


def _decision_factory(competitor, settings, limiter, *, tracking_context=None):
    from .decisions import OpenAIDecisionProvider

    return OpenAIDecisionProvider(
        settings.selection, request_limiter=limiter, tracking_context=tracking_context
    )


def _browser_factory(competitor, settings, guard, host_limiter):
    from .browser import Crawl4AIFetcher

    return Crawl4AIFetcher(str(competitor.homepage_url), settings, guard, host_limiter=host_limiter)


class _Capacity:
    def __init__(self, settings: ScraperConfig):
        self.pages = asyncio.Semaphore(settings.max_concurrency)
        self.hosts = HostLimiter(settings.per_domain_concurrency)
        self.decisions = asyncio.Semaphore(settings.selection.max_concurrency)


class _LimitedFetcher:
    def __init__(
        self,
        fetcher: PageFetcher,
        capacity: _Capacity,
        guard: RobotsGuard,
        *,
        limit_source_host: bool,
    ):
        self.fetcher = fetcher
        self.capacity = capacity
        self.guard = guard
        self.limit_source_host = limit_source_host

    async def __aenter__(self):
        self.active = await self.fetcher.__aenter__()
        return self

    async def __aexit__(self, *exc):
        await self.fetcher.__aexit__(*exc)

    async def fetch(self, url, settings):
        fetch_started = time.monotonic()
        history = []
        stop = "non_retryable"
        for attempt in range(1, settings.max_retries + 2):
            started = time.monotonic()
            logger.info("Fetching %s (attempt %d/%d)", url, attempt, settings.max_retries + 1)
            try:
                page = await self._attempt(url, settings)
            except RequestBlocked as error:
                page = failed_page(str(url), error, kind="policy")
            except Exception as error:
                page = failed_page(str(url), error)
            diagnostics = page.metadata.get("crawl_diagnostics", {})
            kind = diagnostics.get("failure_kind") if isinstance(diagnostics, dict) else None
            if page.crawl_status == "failed" and kind is None:
                kind = "http" if page.http_status and page.http_status >= 400 else "internal"
            headers = page.metadata.get("response_headers", {})
            after = (
                next(
                    (
                        str(value)
                        for name, value in headers.items()
                        if name.lower() == "retry-after"
                    ),
                    None,
                )
                if isinstance(headers, dict)
                else None
            )
            retry_after = parse_retry_after(after)
            delay = retry_delay(attempt, settings, retry_after=retry_after)
            if (
                page.crawl_status == "failed"
                and is_retryable(page.http_status, kind)
                and retry_after is not None
                and not page.metadata.get("retry_after_applied")
            ):
                self.capacity.hosts.defer(str(page.final_url or url), retry_after, settings)
            history.append(
                {
                    "attempt": attempt,
                    "http_status": page.http_status,
                    "failure_kind": kind,
                    "error_message": page.error_message,
                    "duration_seconds": round(time.monotonic() - started, 6),
                }
            )
            if page.crawl_status == "success":
                stop = "succeeded"
                break
            if not is_retryable(page.http_status, kind):
                break
            if delay is None:
                stop = "retry_after_exceeds_budget"
                break
            if attempt > settings.max_retries:
                stop = "retries_exhausted"
                break
            history[-1]["retry_delay_seconds"] = delay
            logger.warning("Retrying %s after %.2fs (%s)", url, delay, kind)
            # _attempt has released all shared slots before the retry sleep.
            await wait_before_retry(delay)
        metadata = dict(page.metadata)
        metadata["crawl_diagnostics"] = {
            **(diagnostics if isinstance(diagnostics, dict) else {}),
            "attempts": history,
            "stop_reason": stop,
            "duration_seconds": round(time.monotonic() - fetch_started, 6),
        }
        logger.info("Fetched %s: %s after %d attempt(s)", url, page.crawl_status, len(history))
        return page.model_copy(update={"attempt_count": len(history), "metadata": metadata})

    async def _attempt(self, url, settings):
        async with AsyncExitStack() as stack:
            try:
                async with asyncio.timeout(settings.capacity_wait_timeout_seconds):
                    await stack.enter_async_context(self.capacity.pages)
                    if self.limit_source_host:
                        await stack.enter_async_context(self.capacity.hosts.slot(str(url)))
                        minimum = await self.guard.check(str(url))
                        await self.capacity.hosts.pace(str(url), settings, minimum)
            except TimeoutError as error:
                raise CapacityTimeout("capacity_wait_timeout") from error
            if self.limit_source_host:
                async with asyncio.timeout(settings.page_timeout_seconds):
                    return await self.active.fetch(url, settings)
            return await self.active.fetch(url, settings)


class CompetitorScraper:
    def __init__(
        self,
        *,
        fetcher_factory: FetcherFactory | None = None,
        extractor: ContentExtractor | None = None,
        http_transport: httpx.AsyncBaseTransport | None = None,
        decision_provider_factory: DecisionProviderFactory | None = None,
        decision_tracking_context_factory: Callable[[CompetitorInput], DecisionTrackingContext]
        | None = None,
    ):
        self._fetcher_factory = fetcher_factory
        self._extractor = extractor or MarkdownExtractor()
        self._transport = http_transport
        self._decision_provider_factory = decision_provider_factory
        self._decision_tracking_context_factory = decision_tracking_context_factory

    def _decision_provider(self, competitor, settings, limiter):
        if self._decision_provider_factory is not None:
            return self._decision_provider_factory(competitor, settings, limiter)
        context = None
        if self._decision_tracking_context_factory is not None:
            try:
                context = self._decision_tracking_context_factory(competitor)
            except Exception:
                logger.warning("Decisions tracking context failed; using a separate crawl trace.")
        return _decision_factory(competitor, settings, limiter, tracking_context=context)

    def _preflight_selection(self, settings):
        if self._decision_provider_factory is None:
            from .decisions import validate_decision_credentials

            validate_decision_credentials(settings.selection)

    async def crawl(
        self,
        competitor: CompetitorInput,
        settings: ScraperConfig | None = None,
        *,
        cli_overrides: ScraperConfig | None = None,
    ) -> CompetitorCrawlResult:
        effective = resolve_config(settings, competitor.overrides, cli_overrides)
        self._preflight_selection(effective)
        return await self._crawl(competitor, settings, cli_overrides, _Capacity(effective))

    async def _crawl(self, competitor, global_settings, cli_overrides, capacity):
        settings = resolve_config(global_settings, competitor.overrides, cli_overrides)
        started = utc_now()
        decision_provider = None
        logger.info("Starting %s", competitor.homepage_url)
        try:
            async with GuardedHttpClient(
                str(competitor.homepage_url),
                settings,
                transport=self._transport,
                host_limiter=capacity.hosts,
            ) as guard:
                custom = self._fetcher_factory is not None
                owned = (
                    self._fetcher_factory(competitor, settings, guard)
                    if custom
                    else _browser_factory(competitor, settings, guard, capacity.hosts)
                )
                fetcher = _LimitedFetcher(owned, capacity, guard, limit_source_host=custom)
                decision_provider = self._decision_provider(
                    competitor, settings, capacity.decisions
                )
                discovery = await DiscoveryCrawler(
                    fetcher,
                    BoundedSitemapDiscoverer(
                        transport=self._transport, host_limiter=capacity.hosts
                    ),
                    robots_guard=guard,
                    managed_attempts=True,
                    decision_provider=decision_provider,
                ).run(competitor, global_settings, cli_overrides=cli_overrides)
            pages = []
            for page in discovery.fetched_pages:
                try:
                    pages.append(self._extractor.extract(page, competitor, settings))
                except Exception as error:
                    logger.warning("Extraction failed for %s: %s", page.source_url, error)
                    content = page.model_dump(exclude={"raw_html", "links"})
                    metadata = dict(page.metadata)
                    prior = metadata.get("crawl_diagnostics", {})
                    metadata["crawl_diagnostics"] = {
                        **(prior if isinstance(prior, dict) else {}),
                        "failure_kind": "extraction",
                    }
                    content.update(
                        crawl_status="failed",
                        error_message=f"extraction_error: {error}",
                        metadata=metadata,
                    )
                    pages.append(
                        ScrapedPage(
                            **content,
                            competitor_name=competitor.display_name,
                            homepage_url=competitor.homepage_url,
                        )
                    )
            failures = {
                normalize_url(str(page.source_url)).normalized: page.error_message
                for page in pages
                if page.crawl_status == "failed"
            }
            records = [
                record.model_copy(
                    update={
                        "disposition": UrlDisposition.FAILED,
                        "reason": failures[str(record.normalized_url)],
                    }
                )
                if str(record.normalized_url) in failures
                else record
                for record in discovery.discovered_urls
            ]
            successes = sum(page.crawl_status == "success" for page in pages)
            policy_failures = any(
                record.reason
                and record.reason.startswith("robots_")
                and record.disposition == UrlDisposition.DEFERRED
                for record in records
            )
            status = (
                "failed"
                if not successes
                else (
                    "partial"
                    if failures or policy_failures or discovery.completion_reason == "failed"
                    else "success"
                )
            )
            result = CompetitorCrawlResult(
                competitor_name=competitor.display_name,
                homepage_url=competitor.homepage_url,
                started_at=discovery.started_at,
                finished_at=utc_now(),
                run_status=status,
                completion_reason=discovery.completion_reason,
                effective_config=settings,
                pages=pages,
                discovered_urls=records,
                discovery_truncated=discovery.discovery_truncated,
                warnings=discovery.warnings,
                decision_usage=discovery.decision_usage,
                decision_trace=discovery.decision_trace,
            )
        except Exception as error:
            # Isolate competitor infrastructure failures, while cancellation propagates.
            logger.exception("Competitor failed: %s", competitor.homepage_url)
            result = CompetitorCrawlResult(
                competitor_name=competitor.display_name,
                homepage_url=competitor.homepage_url,
                started_at=started,
                finished_at=utc_now(),
                run_status="failed",
                completion_reason="failed",
                effective_config=settings,
                warnings=[f"{type(error).__name__}: {error}"],
            )
        try:
            reference = getattr(decision_provider, "decision_tracking", None)
            if isinstance(reference, DecisionTrackingReference):
                result._decision_tracking = reference
        except Exception:
            logger.warning("Decisions tracking reference unavailable; crawling continues.")
        logger.info(
            "Finished %s: %s (%d successful, %d failed)",
            competitor.homepage_url,
            result.run_status,
            result.summary.successful,
            result.summary.failed,
        )
        return result

    async def crawl_many(
        self,
        competitors: Sequence[CompetitorInput],
        global_settings: ScraperConfig | None = None,
        *,
        cli_overrides: ScraperConfig | None = None,
    ) -> AsyncIterator[CompetitorCrawlResult]:
        """Yield completed results; closing this iterator cancels and cleans up workers.

        Global/CLI concurrency settings are ceilings across the run. A competitor may
        lower its own discovery parallelism, but cannot increase those shared ceilings.
        """
        limits = resolve_config(global_settings, cli_overrides=cli_overrides)
        # Invalid combinations must fail before workers are created, rather than leave
        # a consumer waiting on a queue after a worker dies during config resolution.
        for competitor in competitors:
            self._preflight_selection(
                resolve_config(global_settings, competitor.overrides, cli_overrides)
            )
        capacity = _Capacity(limits)
        count = min(len(competitors), limits.max_concurrent_competitors)
        pending = iter(competitors)
        results: asyncio.Queue[CompetitorCrawlResult] = asyncio.Queue(maxsize=max(1, count))

        async def worker():
            for competitor in pending:
                result = await self._crawl(competitor, global_settings, cli_overrides, capacity)
                await results.put(result)

        workers = [asyncio.create_task(worker()) for _ in range(count)]
        try:
            for _ in competitors:
                yield await results.get()
        finally:
            for task in workers:
                task.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
