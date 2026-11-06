"""Optional diagnostics derived from retained crawl evidence; never an export contract."""

from .coverage import coverage_summary
from .models import CompetitorCrawlResult


def crawl_diagnostics(result: CompetitorCrawlResult) -> dict:
    return {
        "schema_version": "1.0",
        "coverage": coverage_summary(
            result.pages if result.effective_config.selection.coverage_enabled else [],
            available=bool(
                result.effective_config.selection.coverage_enabled
                and result.decision_usage
                and result.decision_usage.effective_mode == "llm"
            ),
        ),
        "features": {
            "markdown_remove_boilerplate": result.effective_config.markdown_remove_boilerplate,
            "deduplicate_pages": result.effective_config.deduplicate_pages,
            "coverage_enabled": result.effective_config.selection.coverage_enabled,
        },
        "redirect_aliases": [
            {
                "url": str(record.normalized_url),
                "representative_url": record.reason.removeprefix("duplicate_redirect:"),
            }
            for record in result.discovered_urls
            if record.disposition == "skipped"
            and record.reason
            and record.reason.startswith("duplicate_redirect:")
        ],
        "body_duplicates": [
            {
                "url": str(page.source_url),
                "representative_url": page.metadata["duplicate_of"],
                "fingerprint": page.metadata.get("content_fingerprint"),
            }
            for page in result.pages
            if page.metadata.get("duplicate_of")
        ],
    }
