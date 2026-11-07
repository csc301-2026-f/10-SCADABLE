"""Load the scraper's published Markdown bundles without recrawling or cleaning."""

from collections.abc import Iterable
from pathlib import Path

from scraper.content_identity import markdown_fingerprint

from llm.schemas import AnalysisInput, AnalysisPage
from scraper import CrawlExportSummary


def deduplicate_analysis_pages(pages: Iterable[AnalysisPage]) -> list[AnalysisPage]:
    """Keep the first exact body, unchanged; callers retain their complete source summary."""
    seen = set()
    unique = []
    for page in pages:
        fingerprint = markdown_fingerprint(page.markdown)
        if fingerprint is None:
            continue
        if fingerprint not in seen:
            seen.add(fingerprint)
            unique.append(page)
    return unique


def load_analysis_input(
    summary_path: str | Path, *, deduplicate: bool = True
) -> AnalysisInput:
    """Validate a completion index and follow its successful content references.

    Partial-run warnings and failed-page metadata remain in ``crawl_summary``.
    Missing/invalid files fail explicitly. Whitespace-only pages are not analyzed.
    """
    index = Path(summary_path).resolve(strict=True)
    summary = CrawlExportSummary.model_validate_json(index.read_text(encoding="utf-8"))
    bundle = index.parent
    pages = []
    for page in summary.pages:
        if page.status != "success" or page.markdown_file is None:
            continue
        content = (bundle / page.markdown_file).resolve(strict=True)
        if not content.is_relative_to(bundle):
            raise ValueError(
                f"Markdown reference escapes the export bundle: {page.markdown_file}"
            )
        with content.open(encoding="utf-8", newline="") as stream:
            markdown = stream.read()
        if markdown.strip():
            pages.append(AnalysisPage(source=page, markdown=markdown))
    if not pages:
        details = list(summary.warnings)
        details.extend(page.error for page in summary.pages if page.error)
        explanation = (
            "; ".join(dict.fromkeys(details)) or "No successful nonempty Markdown pages"
        )
        raise ValueError(
            f"No usable Markdown pages for {summary.competitor_name} "
            f"({summary.homepage_url}, status={summary.status}). "
            f"Export: {index}. Crawl details: {explanation}"
        )
    # Read and validate every reference before filtering: duplicates must not hide
    # corrupt exports or missing files.
    return AnalysisInput(
        crawl_summary=summary,
        pages=deduplicate_analysis_pages(pages) if deduplicate else pages,
    )
