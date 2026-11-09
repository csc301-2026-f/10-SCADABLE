"""Tests for the Crawl4AI adapter's failure handling (no browser or network needed)."""

import asyncio
import sys

import pytest

from app.services.crawler import CRAWL_FAILED_MESSAGE, Crawl4AIAdapter, CrawlFailedError


@pytest.fixture
def scraper_unavailable(monkeypatch):
    # A None entry makes "from scraper import ..." raise ImportError
    monkeypatch.setitem(sys.modules, "scraper", None)


def test_failed_real_crawl_raises_instead_of_using_mock(monkeypatch, scraper_unavailable):
    monkeypatch.delenv("ALLOW_MOCK_CRAWLER", raising=False)

    with pytest.raises(CrawlFailedError) as exc_info:
        asyncio.run(Crawl4AIAdapter().crawl("Sprinto", "https://sprinto.com"))

    assert str(exc_info.value).startswith(CRAWL_FAILED_MESSAGE)


@pytest.mark.parametrize("value", ["", "false", "0", "no"])
def test_mock_fallback_stays_off_unless_enabled(monkeypatch, scraper_unavailable, value):
    monkeypatch.setenv("ALLOW_MOCK_CRAWLER", value)

    with pytest.raises(CrawlFailedError):
        asyncio.run(Crawl4AIAdapter().crawl("Drata", "https://drata.com"))


def test_mock_fallback_when_explicitly_allowed(monkeypatch, scraper_unavailable):
    monkeypatch.setenv("ALLOW_MOCK_CRAWLER", "true")

    result = asyncio.run(Crawl4AIAdapter().crawl("Drata", "https://drata.com"))

    assert result["mode"] == "mock"
    assert result["pages"] and result["base_url"] == "https://drata.com"


def test_decisions_tracking_survives_failed_bundle_export(monkeypatch):
    """The in-memory run snapshot retains Decisions correlation when export fails."""
    import scraper.output as output
    import sys
    from types import SimpleNamespace, ModuleType

    contexts = []

    class FakeScraper:
        def __init__(self, *, decision_tracking_context_factory):
            self.factory = decision_tracking_context_factory

        async def crawl(self, competitor, config):
            context = self.factory(competitor)
            contexts.append(context)
            return SimpleNamespace(
                pages=[SimpleNamespace(
                    source_url="https://example.com/features",
                    final_url="https://example.com/features",
                    page_title="Features", page_category="features",
                    cleaned_markdown="Features", crawl_status="success",
                )],
                run_status="success", warnings=[], homepage_url="https://example.com",
                decision_tracking=SimpleNamespace(
                    model_dump=lambda **kwargs: {
                        "research_run_id": context.research_run_id,
                        "crawl_id": context.crawl_id,
                        "trace_id": "a" * 32,
                        "observation_id": "b" * 16,
                    }
                ),
                decision_usage=SimpleNamespace(
                    model_dump=lambda **kwargs: {"request_count": 2, "estimated_cost_usd": 0.001}
                ),
            )

    def broken_export(*args, **kwargs):
        raise OSError("disk unavailable")

    fake_service = ModuleType("scraper.service")
    fake_service.CompetitorScraper = FakeScraper
    monkeypatch.setitem(sys.modules, "scraper.service", fake_service)
    monkeypatch.setattr(output, "export_competitor", broken_export)
    monkeypatch.delenv("ALLOW_MOCK_CRAWLER", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result1 = asyncio.run(Crawl4AIAdapter().crawl(
        "Example", "https://example.com", research_run_id=123
    ))
    result2 = asyncio.run(Crawl4AIAdapter().crawl(
        "Example", "https://example.com", research_run_id=124
    ))
    assert result1["summary_path"] is None
    assert result1["decision_tracking"]["research_run_id"] == "123"
    assert result1["decision_usage"]["request_count"] == 2
    assert result1["pages"][0]["text"] == "Features"
    assert result2["decision_tracking"]["research_run_id"] == "124"
    assert len({context.crawl_id for context in contexts}) == 2
