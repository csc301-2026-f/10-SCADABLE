import asyncio
import time

import httpx
import pytest

from scraper import CompetitorInput, FetchedPage, ScraperConfig
from scraper.types import utc_now


def http_fixture(request):
    if request.url.path == "/robots.txt":
        return httpx.Response(200, text="User-agent: *\nAllow: /\n")
    return httpx.Response(404)


class FixtureFetcher:
    active = 0
    peak = 0
    closed = 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        type(self).closed += 1

    async def fetch(self, url, settings):
        from scraper.models import DiscoveredLink

        cls = type(self)
        cls.active += 1
        cls.peak = max(cls.peak, cls.active)
        try:
            await asyncio.sleep(0.01)
            return FetchedPage(
                source_url=url,
                crawl_timestamp=utc_now(),
                crawl_status="success",
                raw_markdown="# Plans\n$49",
                links=[DiscoveredLink(url=str(url).rstrip("/") + "/pricing")],
            )
        finally:
            cls.active -= 1


async def test_service_composes_discovery_extraction_and_competitor_overrides():
    from scraper.service import CompetitorScraper

    FixtureFetcher.closed = 0
    service = CompetitorScraper(
        fetcher_factory=lambda *args: FixtureFetcher(),
        http_transport=httpx.MockTransport(http_fixture),
    )
    result = await service.crawl(
        CompetitorInput(homepage_url="https://example.com", overrides={"max_pages": 1}),
        ScraperConfig(
            selection={"mode": "structural"}, max_pages=4, request_delay_seconds=(0.0, 0.0)
        ),
    )
    assert result.summary.successful == 1
    assert result.summary.deferred == 1
    assert result.completion_reason == "page_limit"
    assert result.pages[0].cleaned_markdown == "# Plans\n$49"
    assert result.pages[0].competitor_name == "example.com"
    assert result.effective_config.max_pages == 1
    assert FixtureFetcher.closed == 1


async def test_tracking_context_error_does_not_fail_crawl(caplog):
    from scraper.service import CompetitorScraper

    def context(_):
        raise RuntimeError("sensitive-context-error")

    service = CompetitorScraper(
        fetcher_factory=lambda *args: FixtureFetcher(),
        http_transport=httpx.MockTransport(http_fixture),
        decision_tracking_context_factory=context,
    )
    result = await service.crawl(
        CompetitorInput(homepage_url="https://example.com"),
        ScraperConfig(
            selection={"mode": "structural"}, max_pages=1, request_delay_seconds=(0.0, 0.0)
        ),
    )
    assert result.summary.successful == 1
    assert result.decision_tracking is None
    assert "sensitive-context-error" not in caplog.text


async def test_custom_provider_factory_keeps_original_signature():
    from scraper.decisions import OpenAIDecisionProvider
    from scraper.service import CompetitorScraper

    calls = []

    def provider(competitor, settings, limiter):
        calls.append(competitor)
        return OpenAIDecisionProvider(settings.selection, request_limiter=limiter)

    def unused(_):
        pytest.fail("Custom providers own their tracking context")

    service = CompetitorScraper(
        fetcher_factory=lambda *args: FixtureFetcher(),
        http_transport=httpx.MockTransport(http_fixture),
        decision_provider_factory=provider,
        decision_tracking_context_factory=unused,
    )
    result = await service.crawl(
        CompetitorInput(homepage_url="https://example.com"),
        ScraperConfig(
            selection={"mode": "structural"}, max_pages=1, request_delay_seconds=(0.0, 0.0)
        ),
    )
    assert result.summary.successful == 1
    assert len(calls) == 1


async def test_many_limits_global_page_concurrency_even_if_competitor_requests_more():
    from scraper.service import CompetitorScraper

    FixtureFetcher.peak = FixtureFetcher.active = 0
    service = CompetitorScraper(
        fetcher_factory=lambda *args: FixtureFetcher(),
        http_transport=httpx.MockTransport(http_fixture),
    )
    settings = ScraperConfig(
        selection={"mode": "structural"},
        max_pages=1,
        max_concurrency=1,
        max_concurrent_competitors=2,
        request_delay_seconds=(0.0, 0.0),
    )
    competitors = [
        CompetitorInput(homepage_url=f"https://{host}.example", overrides={"max_concurrency": 8})
        for host in ["one", "two", "three"]
    ]
    results = [result async for result in service.crawl_many(competitors, settings)]
    assert len(results) == 3
    assert FixtureFetcher.peak == 1
    assert all(result.summary.successful == 1 for result in results)


async def test_failed_competitor_does_not_abort_other_competitors():
    from scraper.service import CompetitorScraper

    def factory(competitor, settings, guard):
        if competitor.homepage_url.host == "broken.example":
            raise OSError("browser unavailable")
        return FixtureFetcher()

    service = CompetitorScraper(
        fetcher_factory=factory, http_transport=httpx.MockTransport(http_fixture)
    )
    inputs = [CompetitorInput(homepage_url=f"https://{h}.example") for h in ["broken", "good"]]
    results = [
        r
        async for r in service.crawl_many(
            inputs,
            ScraperConfig(
                selection={"mode": "structural"}, max_pages=1, request_delay_seconds=(0.0, 0.0)
            ),
        )
    ]
    assert {r.run_status for r in results} == {"failed", "success"}
    assert next(r for r in results if r.run_status == "failed").warnings


async def test_missing_credentials_fail_before_starting_workers(monkeypatch):
    from scraper.service import CompetitorScraper

    service = CompetitorScraper()
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    competitor = CompetitorInput(
        homepage_url="https://example.com", overrides={"selection": {"mode": "llm"}}
    )
    settings = ScraperConfig(selection={"mode": "structural"})
    iterator = service.crawl_many([competitor], settings)
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        await asyncio.wait_for(anext(iterator), timeout=0.2)


async def test_shared_hostname_spacing_across_competitors():
    from scraper.service import CompetitorScraper

    starts = []

    class TimedFetcher(FixtureFetcher):
        async def fetch(self, url, settings):
            starts.append(time.monotonic())
            return await super().fetch(url, settings)

    service = CompetitorScraper(
        fetcher_factory=lambda *args: TimedFetcher(),
        http_transport=httpx.MockTransport(http_fixture),
    )
    inputs = [
        CompetitorInput(homepage_url="https://example.com", name=name) for name in ["One", "Two"]
    ]
    settings = ScraperConfig(
        selection={"mode": "structural"}, max_pages=1, request_delay_seconds=(0.08, 0.08)
    )
    results = [r async for r in service.crawl_many(inputs, settings)]
    assert len(results) == 2
    assert starts[1] - starts[0] >= 0.07


async def test_closing_many_iterator_cancels_running_competitor():
    from scraper.service import CompetitorScraper

    slow_started = asyncio.Event()
    slow_closed = asyncio.Event()

    class SlowFetcher(FixtureFetcher):
        async def fetch(self, url, settings):
            slow_started.set()
            await asyncio.Event().wait()

        async def __aexit__(self, *_):
            slow_closed.set()

    service = CompetitorScraper(
        fetcher_factory=lambda c, *args: SlowFetcher() if c.name == "slow" else FixtureFetcher(),
        http_transport=httpx.MockTransport(http_fixture),
    )
    inputs = [
        CompetitorInput(homepage_url="https://example.com", name=name) for name in ["fast", "slow"]
    ]
    iterator = service.crawl_many(
        inputs,
        ScraperConfig(
            selection={"mode": "structural"},
            max_pages=1,
            per_domain_concurrency=2,
            request_delay_seconds=(0.0, 0.0),
        ),
    )
    first = await anext(iterator)
    assert first.competitor_name == "fast"
    await slow_started.wait()
    await iterator.aclose()
    assert slow_closed.is_set()
