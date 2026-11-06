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
