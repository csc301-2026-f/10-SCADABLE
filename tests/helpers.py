"""Scraper fixtures and analysis-boundary doubles for offline research tests."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

from llm.analyzer import Analyzer
from llm.schemas import (
    ConflictEntry,
    CompetitorNote,
    ExtractedInformationByURL,
    FeatureScore,
    SWOTAnalysisByURL,
    SWOTEntryByURL
)
from scraper import CompetitorCrawlResult, ScrapedPage
from scraper.output import competitor_directory, export_competitor

NOW = datetime(2026, 10, 19, 12, tzinfo=UTC)
# make_result() finishes one second after NOW; extraction formats that time like this.
CRAWL_TIME = (NOW + timedelta(seconds=1)).strftime("%Y-%m-%d %H:%M:%S")
MARKDOWN = "  # Example\n\nEvidence collection.  \n\n    code\n\n"


def make_page(**changes) -> ScrapedPage:
    return ScrapedPage(
        **{
            "competitor_name": "Example",
            "homepage_url": "https://example.com/",
            "source_url": "https://example.com/",
            "page_title": "Example home",
            "page_category": "features",
            "crawl_timestamp": NOW,
            "crawl_status": "success",
            "raw_markdown": MARKDOWN,
            "cleaned_markdown": MARKDOWN,
            **changes,
        }
    )


def make_result(**changes) -> CompetitorCrawlResult:
    return CompetitorCrawlResult(
        **{
            "competitor_name": "Example",
            "homepage_url": "https://example.com/",
            "started_at": NOW,
            "finished_at": NOW + timedelta(seconds=1),
            "run_status": "success",
            "completion_reason": "completed",
            "pages": [make_page()],
            **changes,
        }
    )


def write_export(root: Path, result: CompetitorCrawlResult | None = None) -> Path:
    result = result if result is not None else make_result()
    export_competitor(result, root)
    return root / competitor_directory(str(result.homepage_url)) / "crawl_summary.json"


def company_result(company, *, empty=False):
    pages = (
        []
        if empty
        else [
            make_page(
                competitor_name=company.display_name,
                homepage_url=company.homepage_url,
                source_url=company.homepage_url,
                cleaned_markdown=f"  {company.display_name} page  \n",
            )
        ]
    )
    return make_result(
        competitor_name=company.display_name,
        homepage_url=company.homepage_url,
        pages=pages,
    )


class StubScraper:
    """Yield completed competitors in reverse order to exercise completion-order handoff."""

    def __init__(self, results):
        self.results = {str(result.homepage_url): result for result in results}
        self.calls = []
        self.closed = False

    async def crawl_many(self, competitors, global_settings):
        self.calls.append((list(competitors), global_settings))
        try:
            for competitor in reversed(competitors):
                yield self.results[str(competitor.homepage_url)]
        finally:
            self.closed = True


class StubAnalyzer(Analyzer):
    def __init__(self, fail_for=()):
        self.fail_for = set(fail_for)
        self.calls = []
        self.inputs = []
        self.comparison_input = None

    async def extract_information(self, data):
        self.calls.append(("extract", data.company_name))
        self.inputs.append(data)
        if data.company_name in self.fail_for:
            raise RuntimeError(f"extraction failed for {data.company_name}")
        return ExtractedInformationByURL(
            company_name=data.company_name, summary="facts", summary_page_urls=[], job_postings=[], claims_cited=[],
            conflicting_data=ConflictEntry(company_name=data.company_name, conflicts=[]),
            crawl_time_string=CRAWL_TIME,
        )

    async def compare(self, data):
        self.calls.append(("compare", None))
        self.comparison_input = data
        return SWOTAnalysisByURL(
            SWOT_entries=[SWOTEntryByURL(component="strength", what="s", swot_component_page_urls=[""])],
            competitor_notes=[
                CompetitorNote(
                    company_name=c.company_name,
                    why_it_matters=f"{c.company_name} matters",
                )
                for c in data.competitors
            ],
            scores=[],
            conflicting_data=[],
        )
