"""Real service, Decisions, exporter, loader and Responses parsing; network boundaries mocked."""

import json
from decimal import Decimal

import httpx
import httpx2
import pytest
from openai import AsyncOpenAI
from scraper.decisions import OpenAIDecisionProvider
from scraper.models import DiscoveredLink
from scraper.service import CompetitorScraper

import llm.client as client_module
from llm.analyzer import Analyzer
from llm.config import Settings
from llm.pipeline import analyze_export, run_pipeline
from llm.schemas import ClaimCited, CompanyAnalysis, ExtractedInformation, FeatureScore
from scraper import CompetitorInput, FetchedPage, ScraperConfig
from tests.helpers import MARKDOWN, NOW

pytestmark = pytest.mark.asyncio


async def test_scraper_markdown_flows_through_decisions_and_llm_and_can_be_replayed(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("OPENAI_API_KEY", "offline-test-key")
    index = tmp_path / "example.com/crawl_summary.json"
    rendered = []
    sdk_clients = []
    api_requests = []
    pricing = "# Pricing\n\nPro: $49/month.  \n"
    facts = ExtractedInformation(
        company_name="Example",
        summary="Evidence collection",
        claims_cited=[
            ClaimCited(
                category="features",
                claim="Evidence collection",
                source_url="https://example.com/",
            ),
            ClaimCited(
                category="pricing",
                claim="Pro: $49/month",
                source_url="https://example.com/pricing",
            ),
        ],
    )
    analysis = CompanyAnalysis(
        company_name="Example",
        summary="Evidence collection",
        claims_cited=[
            ClaimCited(
                category="strength",
                claim="Evidence collection",
                source_url="https://example.com/",
            )
        ],
        scores=[FeatureScore(feature="Evidence collection", score=8)],
    )

    class FixtureFetcher:
        closed = False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            self.closed = True

        async def fetch(self, url, settings):
            rendered.append(str(url))
            home = str(url) == "https://example.com/"
            return FetchedPage(
                source_url=url,
                final_url=url if home else "https://example.com/plans/",
                page_title="Example" if home else "Pricing",
                crawl_timestamp=NOW,
                crawl_status="success",
                http_status=200,
                raw_markdown=MARKDOWN if home else pricing,
                raw_html='<main><a href="/pricing">Pricing</a></main>',
                links=[
                    DiscoveredLink(url="https://example.com/pricing", text="Pricing")
                ]
                if home
                else [],
            )

    fetcher = FixtureFetcher()

    def documents(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        if request.url.path == "/sitemap.xml":
            return httpx.Response(200, text="<urlset/>")
        return httpx.Response(404)

    def model_response(request):
        payload = json.loads(request.content)
        api_requests.append((request.url.path, payload))
        if request.url.path == "/v1/decisions":
            return httpx2.Response(
                200,
                json={
                    "model": "gpt-6-luna",
                    "answers": [
                        {
                            "name": question["name"],
                            "type": question["type"],
                            "confidence": 0.9,
                            **(
                                {"score": 3}
                                if question["type"] == "score"
                                else {"choice": "features"}
                            ),
                        }
                        for question in payload["questions"]
                    ],
                    "usage": {"input_tokens": 100},
                },
            )
        assert request.url.path == "/v1/responses"
        # Analysis must read the completed, exact Markdown export, not a second crawl.
        assert index.is_file()
        assert (index.parent / "pages/index.md").read_text(encoding="utf-8") == MARKDOWN
        if payload["text"]["format"]["name"] == "ExtractedInformation":
            assert MARKDOWN in payload["input"] and pricing in payload["input"]
            assert "https://example.com/plans/" in payload["input"]
            response = facts
        else:
            assert payload["text"]["format"]["name"] == "CompanyAnalysis"
            response = analysis
        return httpx2.Response(
            200,
            json={
                "id": "resp_offline",
                "object": "response",
                "created_at": 0,
                "status": "completed",
                "model": "gpt-6-luna",
                "output": [
                    {
                        "id": "msg_offline",
                        "type": "message",
                        "role": "assistant",
                        "status": "completed",
                        "content": [
                            {
                                "type": "output_text",
                                "text": response.model_dump_json(),
                                "annotations": [],
                            }
                        ],
                    }
                ],
                "parallel_tool_calls": False,
                "tool_choice": "auto",
                "tools": [],
                "usage": {
                    "input_tokens": 1000,
                    "input_tokens_details": {
                        "cached_tokens": 200,
                        "cache_write_tokens": 300,
                    },
                    "output_tokens": 400,
                    "output_tokens_details": {"reasoning_tokens": 100},
                    "total_tokens": 1400,
                },
            },
        )

    def sdk_factory(**kwargs):
        client = AsyncOpenAI(
            **kwargs,
            http_client=httpx2.AsyncClient(
                transport=httpx2.MockTransport(model_response)
            ),
        )
        sdk_clients.append(client)
        return client

    monkeypatch.setattr(client_module, "AsyncOpenAI", sdk_factory)
    service = CompetitorScraper(
        fetcher_factory=lambda *args: fetcher,
        http_transport=httpx.MockTransport(documents),
        decision_provider_factory=lambda company, settings, limiter: (
            OpenAIDecisionProvider(
                settings.selection, request_limiter=limiter, client_factory=sdk_factory
            )
        ),
    )
    client = client_module.OpenAIClient(
        Settings(
            openai_api_key="offline-test-key",
            openai_model="gpt-6-luna",
            _env_file=None,
        )
    )
    analyzer = Analyzer(client)
    try:
        results = await run_pipeline(
            [CompetitorInput(homepage_url="https://example.com/", name="Example")],
            scraper=service,
            scraper_settings=ScraperConfig(
                max_pages=2, max_sitemap_documents=1, request_delay_seconds=(0.0, 0.0)
            ),
            output_directory=tmp_path,
            analyzer=analyzer,
        )
        result = results[0]
        assert result.analysis == analysis
        assert result.extracted_information == facts
        assert result.crawl_summary.counts.successful == 2
        assert result.crawl_summary.decision_usage.effective_mode == "llm"
        assert (
            str(result.crawl_summary.pages[1].final_url) == "https://example.com/plans/"
        )
        assert result.crawl_summary.pages[1].markdown_file == "pages/pricing.md"
        assert "/v1/decisions" in [path for path, _ in api_requests]
        decision_count = sum(path == "/v1/decisions" for path, _ in api_requests)
        assert result.crawl_summary.decision_usage.estimated_cost_usd == pytest.approx(
            decision_count * 0.00001
        )
        assert client.usage.request_count == 2
        assert client.usage.estimated_cost_usd() == Decimal("0.000579")
        replay = await analyze_export(index, analyzer=analyzer)
        assert client.usage.request_count == 4
        assert client.usage.estimated_cost_usd() == Decimal("0.001158")
        assert replay == result
        assert rendered == ["https://example.com/", "https://example.com/pricing"]
        assert (
            sum(path == "/v1/decisions" for path, _ in api_requests) == decision_count
        )
        assert (
            json.loads(result.model_dump_json())["analysis"]["scores"][0]["score"] == 8
        )
    finally:
        await client.close()
    assert fetcher.closed
    assert len(sdk_clients) == 2 and all(sdk.is_closed() for sdk in sdk_clients)
