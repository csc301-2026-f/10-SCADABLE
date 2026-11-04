"""Crawler interface, adapters, and realistic mock fixtures.

Enables the research orchestrator to execute against real Crawl4AI instances
via the scraper package. A failed real crawl raises ``CrawlFailedError`` so the
run fails visibly; mock fixtures are used only in tests, or when
``ALLOW_MOCK_CRAWLER=true`` is set for offline development.
"""

import asyncio
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlsplit

logger = logging.getLogger(__name__)

CRAWL_FAILED_MESSAGE = "Could not crawl the competitor's website."


class CrawlFailedError(RuntimeError):
    """The real crawl failed; the run must not continue with made-up pages."""


def mock_crawler_allowed() -> bool:
    return os.environ.get("ALLOW_MOCK_CRAWLER", "").strip().lower() in {"1", "true", "yes"}


class CrawlerProtocol(Protocol):
    """Protocol defining the scraper interface expected by the orchestrator."""

    async def crawl(
        self,
        competitor_name: str,
        website_url: str,
    ) -> dict[str, Any]: ...


class MockCrawl4AICrawler:
    """Mock crawler returning realistic compliance SaaS crawl data.

    Simulates the subpage extraction (/features, /pricing, /docs) of Crawl4AI
    without requiring headless browser binaries or external network requests.
    """

    def __init__(self, should_fail: bool = False, delay_seconds: float = 0.0) -> None:
        self.should_fail = should_fail
        self.delay_seconds = delay_seconds

    async def crawl(
        self,
        competitor_name: str,
        website_url: str,
    ) -> dict[str, Any]:
        if self.delay_seconds > 0:
            await asyncio.sleep(self.delay_seconds)

        if self.should_fail:
            raise RuntimeError(
                f"Mock crawler simulated failure while crawling '{website_url}'."
            )

        base_url = website_url.rstrip("/")
        scraped_at = datetime.now(timezone.utc).isoformat()

        return {
            "competitor_name": competitor_name,
            "base_url": base_url,
            "scraped_at": scraped_at,
            "pages": [
                {
                    "url": f"{base_url}/features",
                    "title": f"{competitor_name} - Platform Features",
                    "text": (
                        f"{competitor_name} provides automated compliance for SOC 2, "
                        "ISO 27001, and HIPAA. Features include continuous evidence "
                        "collection, vendor risk management, access reviews, and "
                        "over 75 cloud integrations including AWS, Azure, and Google Cloud."
                    ),
                },
                {
                    "url": f"{base_url}/pricing",
                    "title": f"{competitor_name} - Pricing Plans",
                    "text": (
                        f"{competitor_name} plans start at $7,500/year for single-framework "
                        "startups, scaling to $25,000+/year for enterprise multi-framework "
                        "packages with dedicated auditor support."
                    ),
                },
                {
                    "url": f"{base_url}/docs",
                    "title": f"{competitor_name} - Developer Documentation",
                    "text": (
                        f"Integration documentation for {competitor_name} REST API and "
                        "agent-based evidence collectors. Does not offer native "
                        "automated code remediation or EU Cyber Resilience Act (CRA) workflows."
                    ),
                },
            ],
        }


class Crawl4AIAdapter:
    """Adapter wrapping Yash & Jaynik's Crawl4AI implementation.

    Delegates to CompetitorScraper from the scraper package. If the scraper is
    missing or the crawl fails, raises CrawlFailedError, unless
    ALLOW_MOCK_CRAWLER is set, in which case it returns mock pages marked
    ``"mode": "mock"``.
    """

    def __init__(self) -> None:
        self._underlying: Any = None

    async def crawl(
        self,
        competitor_name: str,
        website_url: str,
    ) -> dict[str, Any]:
        try:
            from scraper import CompetitorInput, ScraperConfig, SelectionConfig
            from scraper.environment import load_environment
            from scraper.output import competitor_directory, export_competitor
            from scraper.service import CompetitorScraper

            load_environment()

            parts = urlsplit(website_url)
            homepage_url = (
                f"{parts.scheme}://{parts.netloc}"
                if parts.scheme and parts.netloc
                else website_url
            )

            competitor = CompetitorInput(
                name=competitor_name,
                homepage_url=homepage_url,
            )
            # Use LLM selection if OPENAI_API_KEY is available, otherwise structural
            selection_mode = (
                "llm" if os.environ.get("OPENAI_API_KEY", "").strip() else "structural"
            )
            # Cap crawl to 3 pages for D2 demo efficiency
            config = ScraperConfig(
                max_pages=3,
                selection=SelectionConfig(mode=selection_mode),
            )
            scraper = CompetitorScraper()
            result = await scraper.crawl(competitor, config)

            pages = [
                {
                    "url": str(page.source_url),
                    "final_url": str(page.final_url) if page.final_url else None,
                    "title": page.page_title,
                    "category": getattr(
                        page.page_category, "value", str(page.page_category)
                    ),
                    "text": page.cleaned_markdown,
                    "markdown": page.cleaned_markdown,
                    "status": page.crawl_status,
                }
                for page in result.pages
                if page.crawl_status == "success" and page.cleaned_markdown.strip()
            ]

            if not pages:
                raise RuntimeError(
                    f"Crawl4AI returned no usable pages (status={result.run_status}, "
                    f"warnings={result.warnings})."
                )

            # Export immutable Markdown bundle to scraper/output/backend/
            summary_path = None
            try:
                output_root = Path("scraper/output/backend")
                output_root.mkdir(parents=True, exist_ok=True)
                timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
                target_dir = output_root / f"run_{timestamp}"
                await asyncio.to_thread(export_competitor, result, target_dir)
                summary_path = str(
                    target_dir
                    / competitor_directory(str(result.homepage_url))
                    / "crawl_summary.json"
                )
            except Exception as export_err:
                logger.warning("Crawl bundle export skipped: %s", export_err)

            return {
                "schema_version": "backend-scrape-v1",
                "mode": "real",
                "competitor_name": competitor_name,
                "base_url": homepage_url,
                "scraped_at": datetime.now(timezone.utc).isoformat(),
                "status": result.run_status,
                "pages": pages,
                "summary_path": summary_path,
            }
        except Exception as exc:
            if not mock_crawler_allowed():
                logger.error("Real Crawl4AI scrape failed for %s: %s", website_url, exc)
                raise CrawlFailedError(f"{CRAWL_FAILED_MESSAGE} ({exc})") from exc
            logger.warning(
                "Real Crawl4AI scraper unavailable or failed (%s); "
                "ALLOW_MOCK_CRAWLER is set, so using mock pages.",
                exc,
            )
            mock_data = await MockCrawl4AICrawler().crawl(competitor_name, website_url)
            return {**mock_data, "mode": "mock"}


def get_default_crawler() -> CrawlerProtocol:
    """Return the default crawler adapter."""
    return Crawl4AIAdapter()
