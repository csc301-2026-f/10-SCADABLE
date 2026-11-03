"""Conservative, network-free extraction from application-owned fetched pages."""

from .config import CompetitorInput, ScraperConfig
from .content_identity import clean_markdown as clean_markdown
from .models import FetchedPage, ScrapedPage
from .types import CrawlStatus, PageCategory


def categorize(page: FetchedPage, settings: ScraperConfig) -> PageCategory:
    """Consume an existing assessment; extraction never makes model/network calls."""
    return page.content_assessment.category if page.content_assessment else PageCategory.OTHER


class MarkdownExtractor:
    def extract(
        self, page: FetchedPage, competitor: CompetitorInput, settings: ScraperConfig
    ) -> ScrapedPage:
        content = page.model_dump(exclude={"raw_html", "links"})
        if page.crawl_status == CrawlStatus.SUCCESS and not page.raw_markdown.strip():
            content.update(crawl_status=CrawlStatus.FAILED, error_message="empty_markdown")
        return ScrapedPage(
            **content,
            competitor_name=competitor.display_name,
            homepage_url=competitor.homepage_url,
            page_category=categorize(page, settings),
            cleaned_markdown=clean_markdown(page.raw_markdown),
        )
