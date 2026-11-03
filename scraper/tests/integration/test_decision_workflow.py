"""Real service/adapter/frontier/exporter; all website and SDK HTTP is mocked.

The fixture's answer labels test application behavior, not live model quality.
"""

import json
from html import escape
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import httpx2
import pytest
from openai import AsyncOpenAI

from scraper import CompetitorInput, CrawlExportSummary, FetchedPage, ScraperConfig
from scraper.decision_budget import MAX_REQUEST_BYTES, request_size
from scraper.decision_models import DecisionTraceRecord
from scraper.decisions import OpenAIDecisionProvider
from scraper.models import DiscoveredLink
from scraper.output import export_competitor
from scraper.service import CompetitorScraper
from scraper.types import utc_now

FIXTURE = json.loads(
    (Path(__file__).parents[1] / "fixtures/company_selection.json").read_text(encoding="utf-8")
)


class Scenario:
    def __init__(self, base="https://example.com"):
        self.base = base
        self.pages = {item["path"]: item for item in FIXTURE["pages"]}
        self.requested = []
        self.api_calls = []
        self.ledgers = []
        self.browser_closed = False
        self.clients = []

    def document(self, path):
        return FIXTURE["homepage"] if path == "/" else self.pages[path]

    def rendered(self, path):
        item = self.document(path)
        html = (
            "<main>"
            + "".join(
                f"<section><h2>{escape(page['heading'])}</h2><p>{escape(page['context'])} "
                f'<a href="{page["path"]}">{escape(page["anchor"])}</a></p></section>'
                for page in FIXTURE["pages"]
            )
            + "</main>"
        )
        links = [self.base + page["path"] for page in FIXTURE["pages"]]
        links += [self.base + "/private", "https://unapproved.example/secrets"]
        return FetchedPage(
            source_url=self.base + path,
            crawl_timestamp=utc_now(),
            crawl_status="success",
            http_status=200,
            page_title=item["title"],
            raw_markdown=item["markdown"],
            raw_html=html if path == "/" else "<main>" + escape(item["markdown"]) + "</main>",
            links=[DiscoveredLink(url=url) for url in links] if path == "/" else [],
        )

    def fetcher_factory(self, competitor, settings, guard):
        scenario = self

        class Fetcher:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_):
                scenario.browser_closed = True

            async def fetch(self, url, settings):
                scenario.requested.append(str(url))
                return scenario.rendered(urlsplit(str(url)).path)

        return Fetcher()

    def documents(self, request):
        assert str(request.url).startswith(self.base + "/")
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private\n")
        if request.url.path == "/sitemap.xml":
            # The sitemap and homepage share the same URL identity and evidence registry.
            return httpx.Response(
                200, text=f"<urlset><url><loc>{self.base}/a17</loc></url></urlset>"
            )
        return httpx.Response(404)

    def answers(self, payload):
        evidence = json.loads(payload["input"])
        if "candidates" in evidence:
            return [
                {
                    "name": f"candidate_{i}",
                    "type": "score",
                    "confidence": 0.9,
                    "score": self.pages.get(urlsplit(item["url"]).path, {}).get(
                        "candidate_score", 3
                    ),
                }
                for i, item in enumerate(evidence["candidates"])
            ]
        self.ledgers.append(evidence["collected_excerpts"])
        item = self.document(urlsplit(evidence["page"]["source_url"]).path)
        return [
            {"name": name, "type": "score", "confidence": 0.9, "score": item[name]}
            for name in ("usefulness", "novelty")
        ] + [{"name": "category", "type": "choice", "confidence": 0.9, "choice": item["category"]}]

    def provider_factory(self, competitor, settings, limiter):
        def handle(request):
            assert str(request.url) == "https://api.openai.com/v1/decisions"
            payload = json.loads(request.content)
            self.api_calls.append(payload)
            assert request_size(payload) <= MAX_REQUEST_BYTES
            assert all("untrusted" in q["instructions"] for q in payload["questions"])
            return httpx2.Response(
                200,
                json={
                    "model": "gpt-6-luna",
                    "answers": self.answers(payload),
                    "usage": {"input_tokens": 100},
                },
            )

        def client_factory(**kwargs):
            client = AsyncOpenAI(
                **kwargs, http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handle))
            )
            self.clients.append(client)
            return client

        return OpenAIDecisionProvider(
            settings.selection, request_limiter=limiter, client_factory=client_factory
        )

    def service(self, provider_factory=None):
        return CompetitorScraper(
            fetcher_factory=self.fetcher_factory,
            http_transport=httpx.MockTransport(self.documents),
            decision_provider_factory=provider_factory or self.provider_factory,
        )


@pytest.fixture
def scenario(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-not-a-real-key")
    return Scenario()


async def test_fixture_company_evidence_is_ranked_and_exported_at_small_page_budget(
    scenario, tmp_path
):
    result = await scenario.service().crawl(
        CompetitorInput(homepage_url=scenario.base),
        ScraperConfig(
            max_pages=4,
            max_concurrency=1,
            request_delay_seconds=(0.0, 0.0),
            selection={"trace_enabled": True},
        ),
    )
    assert scenario.requested == [
        scenario.base + path for path in ("/", "/a17", "/proof/k2", "/updates/r7")
    ]
    assert all(page.content_assessment.usefulness >= 2 / 3 for page in result.pages)
    candidates = [
        json.loads(call["input"])["candidates"]
        for call in scenario.api_calls
        if "candidates" in json.loads(call["input"])
    ]
    atlas = next(item for item in candidates[0] if item["url"].endswith("/a17"))
    assert atlas["observations"][0]["heading"] == "Our platform"
    assert "integrates with SAP" in atlas["observations"][0]["surrounding_text"]
    assert result.completion_reason == "page_limit"
    summary = export_competitor(result, tmp_path)
    folder = tmp_path / "example.com"
    restored = CrawlExportSummary.model_validate_json((folder / "crawl_summary.json").read_text())
    assert restored == summary
    assert restored.schema_version == "2.1" and result.schema_version == "1.1"
    assert summary.pages[1].selection.method == "ranked"
    assert summary.pages[1].selection.relevance_score == 1.0
    assert summary.decision_usage.reported_input_tokens == len(scenario.api_calls) * 100
    assert scenario.browser_closed and all(client.is_closed() for client in scenario.clients)
    assert all(
        urlsplit(item["url"]).hostname == "example.com"
        for call in scenario.api_calls
        for item in json.loads(call["input"]).get("candidates", [])
    )
    assert "test-not-a-real-key" not in (folder / "decisions.jsonl").read_text()


async def test_all_successes_survive_low_scores_misleading_links_and_duplicate_content(
    scenario, tmp_path
):
    result = await scenario.service().crawl(
        CompetitorInput(homepage_url=scenario.base),
        ScraperConfig(
            max_pages=8,
            max_concurrency=1,
            request_delay_seconds=(0.0, 0.0),
            selection={"trace_enabled": True},
        ),
    )
    pages = {urlsplit(str(page.source_url)).path: page for page in result.pages}
    assert set(pages) == {"/", *scenario.pages}
    assert pages["/pricing"].page_category == "other"
    assert pages["/pricing"].content_assessment.usefulness == 0.0
    assert pages["/mirror"].content_assessment.novelty == 0.0
    assert pages["/a17"].cleaned_markdown == pages["/mirror"].cleaned_markdown
    assert pages["/instructions"].cleaned_markdown and pages["/journal/industry"].cleaned_markdown
    assert sum(page.selection.method == "exploration" for page in result.pages) == 1
    registry = {urlsplit(str(item.url)).path: item for item in result.discovered_urls}
    assert registry["/private"].reason == "robots_disallowed"
    assert registry["/secrets"].disposition == "external"
    assert not any(
        "unapproved.example" in url or url.endswith("/private") for url in scenario.requested
    )
    for ledger in scenario.ledgers:
        assert len(ledger) <= 8
        assert len({item["text"] for item in ledger}) == len(ledger)
        assert all(len(item["text"]) <= 500 for item in ledger)
    summary = export_competitor(result, tmp_path)
    assert summary.counts.successful == 8 and summary.counts.failed == 0
    assert len(list((tmp_path / "example.com/pages").rglob("*.md"))) == 8


@pytest.mark.parametrize("failure", ["api_unavailable", "budget_exhausted"])
async def test_fallback_exports_successes_usage_warning_and_optional_trace(
    scenario, tmp_path, failure
):
    attempts = []

    def failing_factory(competitor, settings, limiter):
        def handle(request):
            attempts.append(request)
            return httpx2.Response(503, json={"error": {"message": "sensitive upstream detail"}})

        return OpenAIDecisionProvider(
            settings.selection,
            request_limiter=limiter,
            client_factory=lambda **kwargs: AsyncOpenAI(
                **kwargs, http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handle))
            ),
        )

    result = await scenario.service(failing_factory).crawl(
        CompetitorInput(homepage_url=scenario.base),
        ScraperConfig(
            max_pages=3,
            max_concurrency=1,
            request_delay_seconds=(0.0, 0.0),
            selection={
                "trace_enabled": True,
                "max_retries": 0,
                "budget_usd": 0.0 if failure == "budget_exhausted" else 0.03,
            },
        ),
    )
    summary = export_competitor(result, tmp_path)
    usage = summary.decision_usage
    assert summary.status == "success" and summary.counts.successful == 3
    assert usage.requested_mode == "llm" and usage.effective_mode == "structural"
    assert usage.fallback_events[0].reason == failure
    assert summary.warnings
    assert all(page.selection.method == "structural" for page in summary.pages[1:])
    trace = (tmp_path / "example.com/decisions.jsonl").read_text()
    if failure == "api_unavailable":
        assert len(attempts) == usage.request_count == 1
        assert usage.uncertain_input_tokens > 0 and usage.reported_input_tokens == 0
        assert DecisionTraceRecord.model_validate_json(trace).error_code == "http_503"
    else:
        assert len(attempts) == usage.request_count == 0 and trace == ""
    assert "sensitive upstream detail" not in trace


async def test_cleanup_duplicate_reuse_and_coverage_share_the_exported_body(monkeypatch, tmp_path):
    from scraper.crawl4ai_adapter import build_run_config

    monkeypatch.setenv("OPENAI_API_KEY", "test-not-a-real-key")

    class CleanedScenario(Scenario):
        def __init__(self):
            super().__init__()
            self.original_html = {}
            self.pages = {path: {"candidate_score": 3} for path in ("/a", "/b")}

        def document(self, path):
            return {"usefulness": 2, "novelty": 3, "category": "pricing"}

        def rendered(self, path):
            links = ("/a", "/b") if path == "/" else ("/extra",)
            source = (
                f'<div id="onetrust-banner-sdk">Consent variant {path}</div>'
                + "<nav>"
                + "".join(f'<a href="{link}">Page</a>' for link in links)
                + "</nav>"
                + "<h1>Plans</h1><p>Pro $49 monthly, annual billing only.</p>"
            )
            self.original_html[path] = source
            markdown = (
                build_run_config(ScraperConfig())
                .markdown_generator.generate_markdown(source, base_url=self.base)
                .raw_markdown
            )
            return FetchedPage(
                source_url=self.base + path,
                crawl_timestamp=utc_now(),
                crawl_status="success",
                raw_html=source,
                raw_markdown=markdown,
                links=[DiscoveredLink(url=self.base + link) for link in links],
            )

        def documents(self, request):
            return (
                httpx.Response(200, text="User-agent: *")
                if request.url.path == "/robots.txt"
                else httpx.Response(404)
            )

    scenario = CleanedScenario()
    result = await scenario.service().crawl(
        CompetitorInput(homepage_url=scenario.base),
        ScraperConfig(
            max_pages=3,
            max_concurrency=2,
            per_domain_concurrency=2,
            request_delay_seconds=(0.0, 0.0),
        ),
    )
    assert len(result.pages) == 3
    assert len({p.cleaned_markdown for p in result.pages}) == 1
    assert len(scenario.original_html) == 3
    assert all("Consent variant" in source for source in scenario.original_html.values())
    assert len(scenario.ledgers) == 1
    assert result.decision_usage.request_count == len(scenario.api_calls) == 2
    assert result.decision_usage.reported_input_tokens == 200
    candidate_call = next(
        call for call in scenario.api_calls if "candidates" in json.loads(call["input"])
    )
    assert json.loads(candidate_call["input"])["coverage_hints"]["indicated"] == ["pricing"]
    summary = export_competitor(result, tmp_path)
    folder = tmp_path / "example.com"
    assert len(list((folder / "pages").rglob("*.md"))) == len(summary.pages) == 3
    diagnostics = json.loads((folder / "crawl_diagnostics.json").read_text())
    assert len(diagnostics["body_duplicates"]) == 2
    assert len(diagnostics["coverage"]["topics"]["pricing"]["sources"]) == 1
    assert any(str(record.url).endswith("/extra") for record in result.discovered_urls)
