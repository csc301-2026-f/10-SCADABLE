"""Edit the settings below, then run this file with the project's Python environment."""

import asyncio
import logging
from contextlib import aclosing
from datetime import UTC, datetime
from pathlib import Path

from scraper import CompetitorInput, ScraperConfig, SelectionConfig
from scraper.environment import load_environment
from scraper.output import competitor_directory, export_competitor
from scraper.runtime import configure_runtime as configure_browser_runtime
from scraper.service import CompetitorScraper

PROJECT_DIRECTORY = Path(__file__).resolve().parent

# EDIT: add one entry per website. Use homepage URLs, without subpage paths.
WEBSITES = [
    CompetitorInput(homepage_url="https://scadable.com/", name="Example competitor"),
    CompetitorInput(homepage_url="https://cluely.com/", name="Example competitor"),
    CompetitorInput(homepage_url="https://www.bluej.com/", name="Example competitor"),
    # CompetitorInput(homepage_url="https://competitor.example/", name="Another competitor"),
]

# EDIT: these are the same settings as the small manual CLI test.
SETTINGS = ScraperConfig(
    # EDIT: "llm" reads OPENAI_API_KEY from .env; "structural" makes no OpenAI calls.
    selection=SelectionConfig(
        mode="llm",
        # Optional override; otherwise use the shared extraction-aligned objective.
        # objective="Research integration setup and plan restrictions.",
        budget_usd=0.03,  # Estimated per website; not a guaranteed billing ceiling.
        trace_enabled=False,  # True saves bounded evidence/answers in decisions.jsonl.
    ),
    max_pages=10,
    max_depth=3,
    max_concurrency=3,
    max_concurrent_competitors=3,
    per_domain_concurrency=1,
    request_delay_seconds=(2.0, 2.0),
    max_sitemap_documents=3,
    max_sitemap_requests=8,
    markdown_remove_navigation_and_footer=True,
    markdown_excluded_selectors=[],  # Optional actual site selectors, e.g. [".site-menu"].
)

# EDIT if needed. Readable UTC timestamps keep runs sorted by date and time.
OUTPUT_DIRECTORY = (
    PROJECT_DIRECTORY / "output" / f"manual-{datetime.now(UTC):%Y-%m-%d_%H-%M-%S}_UTC"
)

logger = logging.getLogger(__name__)


def configure_runtime() -> None:
    """Use the project's writable SDK/browser caches while respecting explicit env settings."""
    load_environment(PROJECT_DIRECTORY.parent / ".env")
    configure_browser_runtime(PROJECT_DIRECTORY)


async def main() -> int:
    if not WEBSITES:
        raise ValueError("Add at least one homepage to WEBSITES")
    folders = [competitor_directory(str(website.homepage_url)).casefold() for website in WEBSITES]
    if len(folders) != len(set(folders)):
        raise ValueError("WEBSITES contains duplicate or conflicting website output folders")

    configure_runtime()
    OUTPUT_DIRECTORY.mkdir(parents=True, exist_ok=False)
    logger.info("Output directory: %s", OUTPUT_DIRECTORY)
    failed = False
    service = CompetitorScraper()
    async with aclosing(service.crawl_many(WEBSITES, SETTINGS)) as results:
        async for result in results:
            summary = export_competitor(result, OUTPUT_DIRECTORY)
            failed |= summary.status != "success"
            index = (
                OUTPUT_DIRECTORY
                / competitor_directory(str(result.homepage_url))
                / "crawl_summary.json"
            )
            logger.info(
                "%s: %d successful, %d failed. Saved %s",
                summary.competitor_name,
                summary.counts.successful,
                summary.counts.failed,
                index,
            )
            if summary.decision_usage:
                usage = summary.decision_usage
                logger.info(
                    "Selection %s -> %s; estimated $%.6f (%d reported, %d uncertain tokens)",
                    usage.requested_mode,
                    usage.effective_mode,
                    usage.estimated_cost_usd,
                    usage.reported_input_tokens,
                    usage.uncertain_input_tokens,
                )
            if summary.decision_trace_file:
                logger.info("Decision trace: %s", index.parent / summary.decision_trace_file)
    return 1 if failed else 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    try:
        raise SystemExit(asyncio.run(main()))
    except KeyboardInterrupt:
        logger.info("Interrupted. Completed exports are retained in %s", OUTPUT_DIRECTORY)
        raise SystemExit(130) from None
    except (OSError, ValueError) as error:
        logger.error("%s", error)
        raise SystemExit(2) from None
