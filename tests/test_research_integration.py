"""Real service, Decisions, exporter, loader and Responses parsing; network boundaries mocked."""

import json

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
from llm.schemas import (
    ClaimCitedByIndex,
    ClaimCitedByURL,
    ConflictEntry,
    ExtractedInformationByURL,
    PartialExtractedInformationByIndex,
)
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
        # Page numbers follow the export order: 0 is the homepage, 1 is /pricing.
    # What the model returns for extraction: claims cite pages by number.
    partial_facts = PartialExtractedInformationByIndex(
        summary="Evidence collection",
        summary_page_indexes=[0],
        job_postings=[],
        conflicting_data=[],
        claims_cited=[
            ClaimCitedByIndex(
                category="feature",
                claim="Evidence collection",
                claim_page_index=0,
            ),
            ClaimCitedByIndex(
                category="pricing",
                claim="Pro: $49/month",
                claim_page_index=1,
            ),
        ],
    )

    # What extract_information returns once the numbers are turned back into URLs.
    facts = ExtractedInformationByURL(
        company_name="Example",
        summary="Evidence collection",
        summary_page_urls=["https://example.com/"],
        job_postings=[],
        claims_cited=[
            ClaimCitedByURL(
                category="feature",
                claim="Evidence collection",
                claim_page_url="https://example.com/",
            ),
            ClaimCitedByURL(
                category="pricing",
                claim="Pro: $49/month",
                claim_page_url="https://example.com/pricing",
            ),
        ],
        conflicting_data=ConflictEntry(company_name="Example", conflicts=[]),
        # The real crawl's finish time is only known after the run; filled in below.
        crawl_time_string="",
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
        assert payload["text"]["format"]["name"] == "PartialExtractedInformationByIndex"
        user_text = payload["input"][-1]["content"]
        assert MARKDOWN in user_text and pricing in user_text
        assert "page_index: 0" in user_text and "page_index: 1" in user_text
        response = partial_facts
        return httpx2.Response(
            200,
            json={
                "id": "resp_offline",
                "object": "response",
                "created_at": 0,
                "status": "completed",
                "model": "offline-model",
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
            openai_model="offline-model",
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
        crawl_time = result.crawl_summary.finished_at.strftime("%Y-%m-%d %H:%M:%S")
        assert result.extracted_information == facts.model_copy(
            update={"crawl_time_string": crawl_time}
        )
        assert result.crawl_summary.counts.successful == 2
        assert result.crawl_summary.decision_usage.effective_mode == "llm"
        assert (
            str(result.crawl_summary.pages[1].final_url) == "https://example.com/plans/"
        )
        assert result.crawl_summary.pages[1].markdown_file == "pages/pricing.md"
        assert "/v1/decisions" in [path for path, _ in api_requests]
        decision_count = sum(path == "/v1/decisions" for path, _ in api_requests)
        replay = await analyze_export(index, analyzer=analyzer)
        assert replay == result
        assert rendered == ["https://example.com/", "https://example.com/pricing"]
        assert (
            sum(path == "/v1/decisions" for path, _ in api_requests) == decision_count
        )
        dumped = json.loads(result.model_dump_json())["extracted_information"]
        assert dumped["claims_cited"][0]["claim"] == "Evidence collection"
        assert "scores" not in dumped
    finally:
        await client.close()
    assert fetcher.closed
    assert len(sdk_clients) == 2 and all(sdk.is_closed() for sdk in sdk_clients)
