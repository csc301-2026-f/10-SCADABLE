import asyncio

import httpx
import pytest

from scraper import CompetitorInput, FetchedPage, ScraperConfig
from scraper.decision_models import (
    CandidateScore,
    DecisionFallback,
    DecisionUsageSummary,
    PageAssessment,
)
from scraper.decisions import DecisionAccessError
from scraper.discovery.engine import DiscoveryCrawler
from scraper.discovery.http import RequestBlocked
from scraper.interfaces import DiscoveryBatch
from scraper.models import DiscoveredLink
from scraper.service import CompetitorScraper
from scraper.types import utc_now


class Fetcher:
    def __init__(self, pages):
        self.pages, self.requested = pages, []
        self.active = 0
        self.closed = False
        self.opened = False

    async def __aenter__(self):
        self.opened = True
        return self

    async def __aexit__(self, *_):
        self.closed = True

    async def fetch(self, url, settings):
        self.active += 1
        try:
            self.requested.append(str(url))
            await asyncio.sleep(0)
            return self.pages[str(url)]
        finally:
            self.active -= 1


def page(path="/", links=(), **updates):
    return FetchedPage(
        source_url="https://example.com" + path,
        crawl_timestamp=utc_now(),
        crawl_status="success",
        raw_markdown="# Company\nEvidence: " + path,
        links=[DiscoveredLink(url="https://example.com" + link) for link in links],
        **updates,
    )


class Sitemaps:
    async def discover(self, *args):
        return DiscoveryBatch()


class Guard:
    def __init__(self, blocked=()):
        self.blocked = blocked

    async def check(self, url):
        if url in self.blocked:
            raise RequestBlocked("robots_disallowed", url)
        return 0


class Decisions:
    def __init__(self, fetcher, settings=None, *, fallback=False, fatal=False):
        self.fetcher = fetcher
        self.settings = settings or ScraperConfig().selection
        self.scored, self.assessed, self.ledgers = [], [], []
        self.closed = False
        self.fallback, self.fatal = fallback, fatal
        self.mode = self.settings.mode

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        self.closed = True

    @property
    def summary(self):
        return DecisionUsageSummary(
            requested_mode=self.settings.mode,
            effective_mode=self.mode,
            budget_usd=self.settings.budget_usd,
            fallback_events=[DecisionFallback(reason="budget_exhausted")]
            if self.mode != self.settings.mode
            else [],
        )

    @property
    def trace_records(self):
        return []

    async def score_candidates(self, candidates):
        assert self.fetcher.active == 0
        self.scored.append(candidates)
        return [
            CandidateScore(
                candidate_id=item.candidate_id,
                score=0.95 if str(item.url).endswith("atlas") else 0.1,
                status="scored",
            )
            for item in candidates
        ]

    async def assess_content(self, source, ledger=()):
        assert self.fetcher.active == 0
        self.assessed.append(str(source.source_url))
        self.ledgers.append(list(ledger))
        if self.fatal:
            raise DecisionAccessError("OpenAI Decisions access failed (HTTP 401)")
        if self.fallback:
            self.mode = "structural"
            return PageAssessment(
                issues={key: "unavailable" for key in ("usefulness", "novelty", "category")}
            )
        return PageAssessment(
            usefulness=0.9, novelty=0.8, category="products", category_confidence=0.9
        )


async def test_engine_scores_context_selects_opaque_url_and_preserves_all_source_content():
    fetcher = Fetcher(
        {
            "https://example.com/": page(
                links=("/pricing", "/atlas"),
                raw_html="<main><h1>Contract review</h1><p>Review agreements "
                '<a href="/atlas">Meet Atlas</a></p></main>',
            ),
            "https://example.com/atlas": page("/atlas"),
            "https://example.com/pricing": page("/pricing"),
        }
    )
    decisions = Decisions(fetcher)
    run = await DiscoveryCrawler(
        fetcher, Sitemaps(), robots_guard=Guard(), decision_provider=decisions
    ).run(
        CompetitorInput(homepage_url="https://example.com"),
        ScraperConfig(max_pages=3, request_delay_seconds=(0.0, 0.0)),
    )
    assert fetcher.requested == [
        "https://example.com/",
        "https://example.com/atlas",
        "https://example.com/pricing",
    ]
    atlas = next(item for item in decisions.scored[0] if str(item.url).endswith("atlas"))
    assert "Review agreements" in atlas.observations[0].surrounding_text
    assert len(decisions.assessed) == 3 and decisions.ledgers[1]
    assert all(item.raw_markdown.startswith("# Company") for item in run.fetched_pages)
    assert run.fetched_pages[1].content_assessment.category == "products"
    assert run.fetched_pages[1].selection.method == "ranked"
    assert fetcher.closed and decisions.closed
    assert run.decision_usage.effective_mode == "llm"


async def test_budget_fallback_continues_without_further_decision_calls_or_page_failures():
    fetcher = Fetcher(
        {
            "https://example.com/": page(links=("/atlas",)),
            "https://example.com/atlas": page("/atlas"),
        }
    )
    decisions = Decisions(fetcher, fallback=True)
    run = await DiscoveryCrawler(
        fetcher, Sitemaps(), robots_guard=Guard(), decision_provider=decisions
    ).run(
        CompetitorInput(homepage_url="https://example.com"),
        ScraperConfig(request_delay_seconds=(0.0, 0.0)),
    )
    assert len(run.fetched_pages) == 2
    assert decisions.scored == [] and len(decisions.assessed) == 1
    assert run.fetched_pages[1].selection.method == "structural"
    assert all(item.crawl_status == "success" for item in run.fetched_pages)
    assert any("budget_exhausted" in item for item in run.warnings)


async def test_bfs_skips_candidate_scoring_but_assesses_content():
    fetcher = Fetcher(
        {
            "https://example.com/": page(links=("/atlas",)),
            "https://example.com/atlas": page("/atlas"),
        }
    )
    decisions = Decisions(fetcher)
    await DiscoveryCrawler(
        fetcher, Sitemaps(), robots_guard=Guard(), decision_provider=decisions
    ).run(
        CompetitorInput(homepage_url="https://example.com"),
        ScraperConfig(strategy="bfs", request_delay_seconds=(0.0, 0.0)),
    )
    assert decisions.scored == [] and len(decisions.assessed) == 2


@pytest.mark.parametrize("mode", ["llm", "structural"])
async def test_homepage_page_limit_defers_links_without_candidate_scoring(mode):
    fetcher = Fetcher({"https://example.com/": page(links=("/atlas",))})
    settings = ScraperConfig(
        max_pages=1, selection={"mode": mode}, request_delay_seconds=(0.0, 0.0)
    )
    decisions = Decisions(fetcher, settings.selection)
    result = await CompetitorScraper(
        fetcher_factory=lambda *args: fetcher,
        decision_provider_factory=lambda *args: decisions,
        http_transport=httpx.MockTransport(lambda request: httpx.Response(404)),
    ).crawl(CompetitorInput(homepage_url="https://example.com"), settings)

    assert result.run_status == "success"
    assert result.completion_reason == "page_limit"
    assert result.summary.successful == 1
    assert result.pages[0].cleaned_markdown == "# Company\nEvidence: /"
    atlas = next(item for item in result.discovered_urls if str(item.url).endswith("/atlas"))
    assert atlas.disposition == "deferred" and atlas.reason == "page_limit"
    assert decisions.scored == []


async def test_authentication_failure_retains_fetched_pages_and_closes_resources():
    fetcher = Fetcher({"https://example.com/": page(links=("/atlas",))})
    decisions = Decisions(fetcher, fatal=True)
    run = await DiscoveryCrawler(
        fetcher, Sitemaps(), robots_guard=Guard(), decision_provider=decisions
    ).run(
        CompetitorInput(homepage_url="https://example.com"),
        ScraperConfig(request_delay_seconds=(0.0, 0.0)),
    )
    assert len(run.fetched_pages) == 1
    assert run.completion_reason == "failed"
    assert fetcher.closed and decisions.closed
    assert any("401" in item for item in run.warnings)


async def test_missing_credentials_fail_before_fetcher_factory_or_http(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    def forbidden(*args):
        pytest.fail("Browser/HTTP must not start before credential validation")

    service = CompetitorScraper(
        fetcher_factory=forbidden, http_transport=httpx.MockTransport(forbidden)
    )
    with pytest.raises(DecisionAccessError, match="OPENAI_API_KEY"):
        await service.crawl(CompetitorInput(homepage_url="https://example.com"))
    with pytest.raises(DecisionAccessError, match="OPENAI_API_KEY"):
        await anext(service.crawl_many([CompetitorInput(homepage_url="https://example.com")]))


async def test_service_structural_mode_never_constructs_openai_client(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    fetcher = Fetcher({"https://example.com/": page()})
    result = await CompetitorScraper(
        fetcher_factory=lambda *args: fetcher,
        http_transport=httpx.MockTransport(lambda request: httpx.Response(404)),
    ).crawl(
        CompetitorInput(homepage_url="https://example.com"),
        ScraperConfig(
            max_pages=1, selection={"mode": "structural"}, request_delay_seconds=(0.0, 0.0)
        ),
    )
    assert result.run_status == "success"
    assert result.pages[0].page_category == "other"
    assert result.decision_usage.request_count == 0


async def test_service_injected_decisions_use_content_categories_without_credentials(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    fetcher = Fetcher({"https://example.com/": page()})
    decisions = Decisions(fetcher)
    result = await CompetitorScraper(
        fetcher_factory=lambda *args: fetcher,
        decision_provider_factory=lambda *args: decisions,
        http_transport=httpx.MockTransport(lambda request: httpx.Response(404)),
    ).crawl(
        CompetitorInput(homepage_url="https://example.com"),
        ScraperConfig(max_pages=1, request_delay_seconds=(0.0, 0.0)),
    )
    assert result.pages[0].page_category == "products"
    assert result.pages[0].raw_markdown == "# Company\nEvidence: /"
    assert result.pages[0].selection.assessment_available
    assert result.schema_version == "1.1"


async def test_ranking_cycles_use_new_candidates_after_five_attempts_without_rescoring():
    paths = [f"/items/{index}" for index in range(60)]
    fetcher = Fetcher(
        {
            "https://example.com/": page(links=paths),
            **{"https://example.com" + path: page(path) for path in paths},
        }
    )

    class TimedDecisions(Decisions):
        def __init__(self):
            super().__init__(fetcher)
            self.times = []

        async def score_candidates(self, candidates):
            self.times.append(len(fetcher.requested))
            return await super().score_candidates(candidates)

    decisions = TimedDecisions()
    run = await DiscoveryCrawler(
        fetcher, Sitemaps(), robots_guard=Guard(), decision_provider=decisions
    ).run(
        CompetitorInput(homepage_url="https://example.com"),
        ScraperConfig(max_pages=8, request_delay_seconds=(0.0, 0.0)),
    )
    assert decisions.times == [1, 6]
    evaluated = [item.candidate_id for group in decisions.scored for item in group]
    # One unscored candidate was already fetched in the fifth (exploration) slot.
    assert len(evaluated) == len(set(evaluated)) == 59
    assert run.attempted_pages == 8
    assert len(run.discovered_urls) == 61


async def test_large_parallel_batches_still_stop_at_five_attempt_checkpoints():
    paths = [f"/items/{index}" for index in range(60)]
    fetcher = Fetcher(
        {
            "https://example.com/": page(links=paths),
            **{"https://example.com" + path: page(path) for path in paths},
        }
    )

    class TimedDecisions(Decisions):
        def __init__(self):
            super().__init__(fetcher)
            self.times = []

        async def score_candidates(self, candidates):
            self.times.append(len(fetcher.requested))
            return await super().score_candidates(candidates)

    decisions = TimedDecisions()
    await DiscoveryCrawler(
        fetcher, Sitemaps(), robots_guard=Guard(), decision_provider=decisions
    ).run(
        CompetitorInput(homepage_url="https://example.com"),
        ScraperConfig(
            max_pages=12,
            max_concurrency=10,
            per_domain_concurrency=10,
            request_delay_seconds=(0.0, 0.0),
        ),
    )
    assert decisions.times == [1, 6]


async def test_robots_denial_during_exploration_reuses_the_same_schedule_slot():
    paths = [f"/known/{index}" for index in range(6)]
    fetcher = Fetcher(
        {
            "https://example.com/": page(links=[*paths, "/unknown"]),
            **{"https://example.com" + path: page(path) for path in paths},
        }
    )

    class RankedDecisions(Decisions):
        async def score_candidates(self, candidates):
            return [
                CandidateScore(
                    candidate_id=item.candidate_id,
                    score=0.0 if str(item.url).endswith("unknown") else 0.95,
                    status="scored",
                )
                for item in candidates
            ]

    run = await DiscoveryCrawler(
        fetcher,
        Sitemaps(),
        robots_guard=Guard(["https://example.com/unknown"]),
        decision_provider=RankedDecisions(fetcher),
    ).run(
        CompetitorInput(homepage_url="https://example.com"),
        ScraperConfig(max_pages=6, request_delay_seconds=(0.0, 0.0)),
    )
    assert run.attempted_pages == 6
    assert [item.selection.method for item in run.fetched_pages] == [
        "homepage",
        "ranked",
        "ranked",
        "ranked",
        "ranked",
        "exploration",
    ]
    assert "https://example.com/unknown" not in fetcher.requested


async def test_fatal_assessment_keeps_every_already_fetched_parallel_outcome():
    home = page().model_copy(
        update={
            "links": [
                DiscoveredLink(url="https://example.com/atlas"),
                DiscoveredLink(url="https://extra.example/one"),
            ]
        }
    )
    fetcher = Fetcher(
        {
            "https://example.com/": home,
            "https://example.com/atlas": page("/atlas"),
            "https://extra.example/one": page("/one").model_copy(
                update={"source_url": "https://extra.example/one"}
            ),
        }
    )

    class LaterFailure(Decisions):
        async def assess_content(self, source, ledger=()):
            if self.assessed:
                raise DecisionAccessError("OpenAI Decisions access failed (HTTP 403)")
            return await super().assess_content(source, ledger)

    run = await DiscoveryCrawler(
        fetcher, Sitemaps(), robots_guard=Guard(), decision_provider=LaterFailure(fetcher)
    ).run(
        CompetitorInput(homepage_url="https://example.com"),
        ScraperConfig(
            max_pages=3, allowed_domains=["extra.example"], request_delay_seconds=(0.0, 0.0)
        ),
    )
    assert run.completion_reason == "failed"
    assert len(run.fetched_pages) == len(fetcher.requested) == 3
    assert all(record.disposition == "crawled" for record in run.discovered_urls)


async def test_shared_service_api_capacity_and_lower_per_competitor_override():
    limiters = []
    active = peak = 0

    class ConcurrentDecisions(Decisions):
        def __init__(self, fetcher, settings, limiter):
            super().__init__(fetcher, settings.selection)
            self.limiter = limiter

        async def assess_content(self, source, ledger=()):
            nonlocal active, peak
            async with self.limiter:
                active += 1
                peak = max(active, peak)
                try:
                    await asyncio.sleep(0.01)
                    return await super().assess_content(source, ledger)
                finally:
                    active -= 1

    fetchers = {}

    def fetch_factory(competitor, *args):
        return fetchers[str(competitor.homepage_url)]

    def decision_factory(competitor, settings, limiter):
        limiters.append(limiter)
        return ConcurrentDecisions(fetchers[str(competitor.homepage_url)], settings, limiter)

    competitors = [
        CompetitorInput(
            homepage_url=f"https://{name}.example", overrides={"selection": {"max_concurrency": 8}}
        )
        for name in ("one", "two", "three")
    ]
    for competitor in competitors:
        url = str(competitor.homepage_url)
        fetchers[url] = Fetcher(
            {url: page().model_copy(update={"source_url": competitor.homepage_url})}
        )
    service = CompetitorScraper(
        fetcher_factory=fetch_factory,
        decision_provider_factory=decision_factory,
        http_transport=httpx.MockTransport(lambda request: httpx.Response(404)),
    )
    results = [
        item
        async for item in service.crawl_many(
            competitors,
            ScraperConfig(
                max_pages=1,
                max_concurrent_competitors=3,
                selection={"max_concurrency": 1},
                request_delay_seconds=(0.0, 0.0),
            ),
        )
    ]
    assert len(results) == 3 and all(item.run_status == "success" for item in results)
    assert peak == 1 and len({id(item) for item in limiters}) == 1


async def test_real_adapter_budget_exhaustion_is_warning_only_and_does_not_dispatch(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from scraper.decisions import OpenAIDecisionProvider

    monkeypatch.setenv("OPENAI_API_KEY", "test-not-a-real-key")
    sdk = SimpleNamespace(
        decisions=SimpleNamespace(
            create=AsyncMock(side_effect=AssertionError("Zero budget must not dispatch"))
        ),
        close=AsyncMock(),
    )
    fetcher = Fetcher(
        {
            "https://example.com/": page(links=("/atlas",)),
            "https://example.com/atlas": page("/atlas"),
        }
    )
    service = CompetitorScraper(
        fetcher_factory=lambda *args: fetcher,
        decision_provider_factory=lambda competitor, settings, limiter: OpenAIDecisionProvider(
            settings.selection, request_limiter=limiter, client_factory=lambda **kwargs: sdk
        ),
        http_transport=httpx.MockTransport(lambda request: httpx.Response(404)),
    )
    result = await service.crawl(
        CompetitorInput(homepage_url="https://example.com"),
        ScraperConfig(max_pages=2, selection={"budget_usd": 0.0}, request_delay_seconds=(0.0, 0.0)),
    )
    assert result.run_status == "success" and result.summary.successful == 2
    assert result.decision_usage.effective_mode == "structural"
    assert result.decision_usage.request_count == 0
    sdk.decisions.create.assert_not_awaited()
    sdk.close.assert_awaited_once()


async def test_cancelling_a_decision_closes_provider_and_fetcher():
    entered = asyncio.Event()
    fetcher = Fetcher({"https://example.com/": page()})

    class BlockedDecisions(Decisions):
        async def assess_content(self, *args):
            entered.set()
            await asyncio.Event().wait()

    decisions = BlockedDecisions(fetcher)
    task = asyncio.create_task(
        DiscoveryCrawler(
            fetcher, Sitemaps(), robots_guard=Guard(), decision_provider=decisions
        ).run(CompetitorInput(homepage_url="https://example.com"))
    )
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert fetcher.closed and decisions.closed


@pytest.mark.parametrize("enabled, expected_calls", [(True, 1), (False, 3)])
async def test_exact_duplicate_assessments_preserve_pages_links_and_attempt_cap(
    enabled, expected_calls
):
    home = page(links=("/a", "/b"))
    duplicate = page("/a", links=("/extra",)).model_copy(
        update={"raw_markdown": "\n" + home.raw_markdown.replace("\n", "\r\n") + "\n"}
    )
    fetcher = Fetcher(
        {
            "https://example.com/": home,
            "https://example.com/a": duplicate,
            "https://example.com/b": page("/b").model_copy(
                update={"raw_markdown": home.raw_markdown}
            ),
        }
    )
    decisions = Decisions(fetcher)
    run = await DiscoveryCrawler(
        fetcher, Sitemaps(), robots_guard=Guard(), decision_provider=decisions
    ).run(
        CompetitorInput(homepage_url="https://example.com"),
        ScraperConfig(
            max_pages=3,
            deduplicate_pages=enabled,
            max_concurrency=3,
            per_domain_concurrency=3,
            request_delay_seconds=(0.0, 0.0),
        ),
    )
    assert len(decisions.assessed) == expected_calls
    assert len(run.fetched_pages) == run.attempted_pages == 3
    assert any(str(item.url).endswith("/extra") for item in run.discovered_urls)
    if enabled:
        assert all(
            p.metadata["duplicate_of"] == "https://example.com/" for p in run.fetched_pages[1:]
        )
        assert all(p.content_assessment.novelty == 0 for p in run.fetched_pages[1:])
        assert len(decisions.ledgers) == 1


@pytest.mark.parametrize(
    "status, body, skip",
    [("success", "Evidence", True), ("failed", "Evidence", False), ("success", "   ", False)],
)
async def test_only_successful_nonempty_observed_redirects_suppress_pending_target(
    status, body, skip
):
    home = page(links=("/target",)).model_copy(
        update={
            "final_url": "https://example.com/target",
            "raw_markdown": body,
            "crawl_status": status,
            "error_message": "failed" if status == "failed" else None,
        }
    )
    fetcher = Fetcher({"https://example.com/": home, "https://example.com/target": page("/target")})
    from scraper.models import DiscoveredUrl

    class TargetSitemap:
        async def discover(self, *args):
            return DiscoveryBatch(
                urls=[
                    DiscoveredUrl(
                        url="https://example.com/target",
                        normalized_url="https://example.com/target",
                        discovery_source="sitemap",
                        depth=1,
                    )
                ]
            )

    decisions = Decisions(fetcher)
    run = await DiscoveryCrawler(
        fetcher, TargetSitemap(), robots_guard=Guard(), decision_provider=decisions
    ).run(
        CompetitorInput(homepage_url="https://example.com"),
        ScraperConfig(max_pages=2, request_delay_seconds=(0.0, 0.0)),
    )
    target = next(item for item in run.discovered_urls if str(item.url).endswith("/target"))
    assert (target.reason == "duplicate_redirect:https://example.com/") is skip
    assert len(fetcher.requested) == (1 if skip else 2)
