"""Application-owned input, fetch and output contracts; no SDK or backend imports."""

from pathlib import PurePosixPath
from typing import Annotated, Literal, Self

from pydantic import Field, JsonValue, PrivateAttr, field_validator, model_validator

from .config import CompetitorInput, NonNegativeInt, PositiveInt, ScraperConfig
from .decision_models import (
    CandidateObservation,
    DecisionTraceRecord,
    DecisionUsageSummary,
    PageAssessment,
    SelectionDiagnostics,
)
from .decision_tracking import DecisionTrackingReference
from .types import (
    ContractModel,
    CrawlStatus,
    DiscoverySource,
    HomepageUrl,
    NonEmptyText,
    PageCategory,
    UrlDisposition,
    UtcTimestamp,
    WebUrl,
    utc_now,
)

__all__ = [
    "CompetitorInput",
    "CompetitorCrawlResult",
    "CrawlSummary",
    "DiscoveredLink",
    "DiscoveredUrl",
    "DiscoveryEvidence",
    "FetchedPage",
    "ScrapedPage",
    "CrawlExportSummary",
    "ExportedPage",
    "UncrawledUrl",
]


class DiscoveredLink(ContractModel):
    url: WebUrl
    text: str = ""
    title: str | None = None


class DiscoveryEvidence(ContractModel):
    observed_url: str
    discovery_source: DiscoverySource
    parent_url: WebUrl | None = None
    depth: NonNegativeInt | None = None
    title: str = ""
    observation: CandidateObservation | None = None


class DiscoveredUrl(ContractModel):
    url: WebUrl
    normalized_url: WebUrl
    discovery_source: DiscoverySource
    discovered_at: UtcTimestamp = Field(default_factory=utc_now)
    parent_url: WebUrl | None = None
    depth: NonNegativeInt | None = None
    depth_basis: Literal["homepage", "link", "sitemap_seed"] = "link"
    relevance_score: Annotated[float, Field(ge=0, le=1)] | None = None
    selection_method: Literal["homepage", "ranked", "exploration", "structural", "bfs"] | None = (
        None
    )
    disposition: UrlDisposition = UrlDisposition.PENDING
    reason: NonEmptyText | None = None
    discovery_count: PositiveInt = 1
    evidence: list[DiscoveryEvidence] = Field(default_factory=list)

    @model_validator(mode="after")
    def explain_uncrawled_urls(self) -> Self:
        if (
            self.disposition
            in {
                UrlDisposition.SKIPPED,
                UrlDisposition.DEFERRED,
                UrlDisposition.EXTERNAL,
            }
            and self.reason is None
        ):
            raise ValueError("Skipped, deferred and external URLs require a reason")
        return self


class PageContent(ContractModel):
    source_url: WebUrl
    final_url: WebUrl | None = None
    page_title: str | None = None
    crawl_timestamp: UtcTimestamp
    crawl_status: CrawlStatus
    http_status: Annotated[int, Field(strict=True, ge=100, le=599)] | None = None
    attempt_count: PositiveInt = 1
    raw_markdown: str = ""
    filtered_markdown: str | None = None
    content_assessment: PageAssessment | None = None
    selection: SelectionDiagnostics | None = None
    metadata: dict[str, JsonValue] = Field(default_factory=dict)
    error_message: NonEmptyText | None = None

    @model_validator(mode="after")
    def consistent_outcome(self) -> Self:
        if self.crawl_status == CrawlStatus.FAILED and self.error_message is None:
            raise ValueError("Failed pages require an error message")
        if self.crawl_status == CrawlStatus.SUCCESS:
            if self.error_message is not None:
                raise ValueError("Successful pages cannot have an error message")
            if self.http_status is not None and self.http_status >= 400:
                raise ValueError("HTTP error responses cannot be successful pages")
        return self


class FetchedPage(PageContent):
    """Transient boundary object; HTML is not included in exported ScrapedPage."""

    raw_html: str = ""
    links: list[DiscoveredLink] = Field(default_factory=list)


class ScrapedPage(PageContent):
    competitor_name: NonEmptyText
    homepage_url: HomepageUrl
    page_category: PageCategory = PageCategory.OTHER
    cleaned_markdown: str = ""

    @model_validator(mode="after")
    def successful_pages_have_content(self) -> Self:
        if self.crawl_status == CrawlStatus.SUCCESS and not self.raw_markdown.strip():
            raise ValueError("Successful extracted pages require source Markdown")
        return self


class CrawlSummary(ContractModel):
    successful: NonNegativeInt = 0
    failed: NonNegativeInt = 0
    skipped: NonNegativeInt = 0
    discovered: NonNegativeInt = 0
    deferred: NonNegativeInt = 0
    external: NonNegativeInt = 0
    pending: NonNegativeInt = 0


class CompetitorSummary(ContractModel):
    """Shared in-memory crawl accounting and provenance."""

    schema_version: Literal["1.1"] = "1.1"
    competitor_name: NonEmptyText
    homepage_url: HomepageUrl
    started_at: UtcTimestamp
    finished_at: UtcTimestamp
    run_status: Literal["success", "partial", "failed", "cancelled"]
    completion_reason: Literal["completed", "page_limit", "discovery_limit", "cancelled", "failed"]
    effective_config: ScraperConfig = Field(default_factory=ScraperConfig)
    discovered_urls: list[DiscoveredUrl] = Field(default_factory=list)
    discovery_truncated: bool = False
    warnings: list[NonEmptyText] = Field(default_factory=list)
    decision_usage: DecisionUsageSummary | None = None
    summary: CrawlSummary = Field(default_factory=CrawlSummary)

    @model_validator(mode="after")
    def ordered_timestamps(self) -> Self:
        if self.finished_at < self.started_at:
            raise ValueError("finished_at must not precede started_at")
        return self


class CompetitorCrawlResult(CompetitorSummary):
    pages: list[ScrapedPage] = Field(default_factory=list)
    decision_trace: list[DecisionTraceRecord] = Field(default_factory=list)
    _decision_tracking: DecisionTrackingReference | None = PrivateAttr(default=None)

    @property
    def decision_tracking(self) -> DecisionTrackingReference | None:
        """Runtime reference, exported separately from the existing crawl contracts."""
        return self._decision_tracking

    @model_validator(mode="after")
    def validate_result_and_derive_summary(self) -> Self:
        if self.finished_at < self.started_at:
            raise ValueError("finished_at must not precede started_at")
        if any(page.homepage_url != self.homepage_url for page in self.pages):
            raise ValueError("All pages must belong to this competitor homepage")
        urls = [str(record.normalized_url) for record in self.discovered_urls]
        if len(urls) != len(set(urls)):
            raise ValueError("Discovered URLs must have unique normalized URLs")
        summary = CrawlSummary(
            successful=sum(page.crawl_status == CrawlStatus.SUCCESS for page in self.pages),
            failed=sum(page.crawl_status == CrawlStatus.FAILED for page in self.pages),
            skipped=sum(url.disposition == UrlDisposition.SKIPPED for url in self.discovered_urls),
            discovered=len(self.discovered_urls),
            deferred=sum(
                url.disposition == UrlDisposition.DEFERRED for url in self.discovered_urls
            ),
            external=sum(
                url.disposition == UrlDisposition.EXTERNAL for url in self.discovered_urls
            ),
            pending=sum(url.disposition == UrlDisposition.PENDING for url in self.discovered_urls),
        )
        if "summary" in self.model_fields_set and self.summary != summary:
            raise ValueError("Summary counts do not match the page and URL records")
        object.__setattr__(self, "summary", summary)
        return self


class ExportDiscovery(ContractModel):
    depth: NonNegativeInt | None = None
    depth_basis: Literal["homepage", "link", "sitemap_seed"] | None = None
    discovery_sources: list[DiscoverySource] = Field(default_factory=list)
    discovered_from: list[WebUrl] = Field(default_factory=list)


class ExportedPage(ExportDiscovery):
    """Content reference and essential metadata; never embeds page bodies."""

    url: WebUrl
    url_path: str
    markdown_file: str | None = None
    title: str | None = None
    category: PageCategory
    word_count: NonNegativeInt
    crawled_at: UtcTimestamp
    duration_seconds: Annotated[float, Field(ge=0)] | None = None
    status: CrawlStatus
    final_url: WebUrl | None = None
    http_status: Annotated[int, Field(ge=100, le=599)] | None = None
    error: NonEmptyText | None = None
    attempt_count: PositiveInt | None = None
    retry_stop_reason: str | None = None
    selection: SelectionDiagnostics | None = None

    @field_validator("markdown_file")
    @classmethod
    def safe_content_reference(cls, value: str | None) -> str | None:
        if value is None:
            return None
        path = PurePosixPath(value)
        if (
            path.is_absolute()
            or path.as_posix() != value
            or not value.startswith("pages/")
            or path.suffix != ".md"
            or any(part in {".", ".."} for part in path.parts)
            or any(char in value for char in "\\:\x00")
        ):
            raise ValueError("Markdown files must be safe relative paths under pages/")
        return value

    @model_validator(mode="after")
    def consistent_reference(self) -> Self:
        if self.status == CrawlStatus.SUCCESS and (not self.markdown_file or self.error):
            raise ValueError("Successful pages require a Markdown file and no error")
        if self.status == CrawlStatus.FAILED and (self.markdown_file or not self.error):
            raise ValueError("Failed pages require an error and no Markdown file")
        return self


class UncrawledUrl(ExportDiscovery):
    url: WebUrl
    status: Literal["skipped", "deferred", "external", "pending"]
    reason: NonEmptyText | None = None

    @model_validator(mode="after")
    def explain_disposition(self) -> Self:
        if self.status != "pending" and not self.reason:
            raise ValueError("Uncrawled URLs require a reason")
        return self


class CrawlExportSummary(ContractModel):
    """Versioned, content-free index for one competitor's Markdown export."""

    schema_version: Literal["2.1"] = "2.1"
    competitor_name: NonEmptyText
    homepage_url: HomepageUrl
    started_at: UtcTimestamp
    finished_at: UtcTimestamp
    duration_seconds: Annotated[float, Field(ge=0)]
    status: Literal["success", "partial", "failed", "cancelled"]
    completion_reason: Literal["completed", "page_limit", "discovery_limit", "cancelled", "failed"]
    counts: CrawlSummary
    pages: list[ExportedPage]
    uncrawled_urls: list[UncrawledUrl] = Field(default_factory=list)
    discovery_truncated: bool = False
    warnings: list[NonEmptyText] = Field(default_factory=list)
    decision_usage: DecisionUsageSummary | None = None
    decision_trace_file: Literal["decisions.jsonl"] | None = None

    @model_validator(mode="after")
    def consistent_index(self) -> Self:
        if self.finished_at < self.started_at:
            raise ValueError("finished_at must not precede started_at")
        urls = [str(item.url) for item in [*self.pages, *self.uncrawled_urls]]
        files = [item.markdown_file.casefold() for item in self.pages if item.markdown_file]
        if len(urls) != len(set(urls)) or len(files) != len(set(files)):
            raise ValueError("Page URLs and Markdown references must be unique")
        counts = CrawlSummary(
            successful=sum(item.status == "success" for item in self.pages),
            failed=sum(item.status == "failed" for item in self.pages),
            discovered=len(urls),
            **{
                name: sum(item.status == name for item in self.uncrawled_urls)
                for name in ("skipped", "deferred", "external", "pending")
            },
        )
        if self.counts != counts:
            raise ValueError("Counts do not match exported page and URL entries")
        return self
