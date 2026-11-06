"""One Markdown bundle per competitor, with a compact validated JSON completion index."""

import hashlib
import json
import logging
import math
import os
import re
import unicodedata
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from urllib.parse import unquote, urlsplit

from .diagnostics import crawl_diagnostics
from .discovery.urls import normalize_url
from .models import (
    CompetitorCrawlResult,
    CrawlExportSummary,
    CrawlSummary,
    DiscoveredUrl,
    ExportedPage,
    ScrapedPage,
    UncrawledUrl,
)

logger = logging.getLogger(__name__)

_RESERVED = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
    *(f"{prefix}{digit}" for prefix in ("COM", "LPT") for digit in "¹²³"),
}
logger = logging.getLogger(__name__)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]


def _component(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value)
    safe = re.sub(r"[^\w.-]", "-", normalized).strip(" .") or "page"
    if safe.split(".", 1)[0].upper() in _RESERVED:
        safe = "_" + safe
    if safe != value or len(safe) > 64:
        safe = safe[:48].rstrip(" .") + "--" + _digest(value)
    return safe


def competitor_directory(homepage: str) -> str:
    """HTTPS uses the hostname; other schemes/ports get a stable origin suffix."""
    normalized = normalize_url(homepage)
    if normalized is None:
        raise ValueError("A valid homepage URL is required")
    parts = urlsplit(normalized.normalized)
    name = _component(parts.hostname)
    if parts.scheme != "https" or parts.port:
        name += "--" + _digest(normalized.normalized)
    return name


def _directory_component(value: str) -> str:
    safe = _component(value)
    # All content files end in .md; no directory may use that suffix.
    if safe.casefold().endswith(".md"):
        safe += "--dir-" + _digest(value)
    return safe


def _identity(url: str) -> str:
    normalized = normalize_url(url)
    if normalized is None:
        raise ValueError(f"Invalid page URL: {url}")
    return normalized.normalized


def _candidate(url: str, homepage: str) -> PurePosixPath:
    parts = urlsplit(url)
    segments = parts.path.split("/")[1:]
    if parts.path.endswith("/"):
        segments = segments[:-1]
        filename = "index"
    else:
        filename = _component(unquote(segments.pop()))
        if filename.casefold() == "index":
            filename += "--" + _digest(url)
    directories = []
    for segment in segments:
        directories.append(_directory_component(unquote(segment)))
    prefix = ["pages"]
    if parts.netloc != urlsplit(homepage).netloc or parts.scheme != urlsplit(homepage).scheme:
        prefix += [
            "_hosts",
            _directory_component(competitor_directory(f"{parts.scheme}://{parts.netloc}/")),
        ]
    if parts.query:
        filename += "--q-" + _digest(url)
    path = PurePosixPath(*prefix, *directories, filename + ".md")
    if len(str(path)) > 140:
        path = PurePosixPath("pages", "_long", filename[:48] + "--" + _digest(url) + ".md")
    return path


def _page_paths(result: CompetitorCrawlResult) -> dict[str, PurePosixPath]:
    sources = {}
    for page in result.pages:
        key = _identity(str(page.source_url))
        if key in sources:
            raise ValueError(f"Duplicate logical page URL: {page.source_url}")
        sources[key] = page
    used = set()
    paths = {}
    for url, page in sorted(sources.items()):
        if page.crawl_status != "success":
            continue
        if not page.cleaned_markdown.strip():
            raise ValueError(f"Successful page has no cleaned Markdown: {url}")
        candidate = _candidate(url, str(result.homepage_url))
        path = candidate
        number = 0
        while str(path).casefold() in used:
            number += 1
            suffix = _digest(url) + (f"-{number}" if number > 1 else "")
            path = candidate.with_name(candidate.stem[:64] + "--" + suffix + ".md")
        used.add(str(path).casefold())
        paths[str(page.source_url)] = path
    return paths


def _discovery(record: DiscoveredUrl | None) -> dict:
    if record is None:
        return {}
    sources = list(
        dict.fromkeys(
            [record.discovery_source, *(item.discovery_source for item in record.evidence)]
        )
    )
    parents = list(
        dict.fromkeys(
            [
                url
                for url in [record.parent_url, *(item.parent_url for item in record.evidence)]
                if url
            ]
        )
    )
    return {
        "depth": record.depth,
        "depth_basis": record.depth_basis,
        "discovery_sources": sources,
        "discovered_from": parents,
    }


def _compact_error(message: str) -> str:
    lines = [
        line.strip() for line in message.split("Code context:", 1)[0].splitlines() if line.strip()
    ]
    return (" ".join(lines) or message.strip())[:500]


def _duration(page: ScrapedPage) -> float | None:
    diagnostics = page.metadata.get("crawl_diagnostics", {})
    value = diagnostics.get("duration_seconds") if isinstance(diagnostics, dict) else None
    if (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(value)
        and value >= 0
    ):
        return round(value, 6)
    return None


def _summary(result: CompetitorCrawlResult, paths: dict[str, PurePosixPath]) -> CrawlExportSummary:
    registry = {_identity(str(item.normalized_url)): item for item in result.discovered_urls}
    pages = []
    fetched = set()
    for page in result.pages:
        url = str(page.source_url)
        key = _identity(url)
        fetched.add(key)
        diagnostics = page.metadata.get("crawl_diagnostics", {})
        failed = page.crawl_status == "failed"
        pages.append(
            ExportedPage(
                url=page.source_url,
                url_path=urlsplit(url).path,
                markdown_file=str(paths[url]) if not failed else None,
                title=page.page_title,
                category=page.page_category,
                selection=page.selection,
                word_count=len(re.findall(r"\b\w+(?:['’-]\w+)*\b", page.cleaned_markdown))
                if not failed
                else 0,
                crawled_at=page.crawl_timestamp,
                duration_seconds=_duration(page),
                status=page.crawl_status,
                final_url=page.final_url if page.final_url and str(page.final_url) != url else None,
                http_status=page.http_status if failed else None,
                error=_compact_error(page.error_message) if failed else None,
                attempt_count=page.attempt_count if page.attempt_count > 1 else None,
                retry_stop_reason=diagnostics.get("stop_reason")
                if failed and isinstance(diagnostics, dict)
                else None,
                **_discovery(registry.get(key)),
            )
        )
    uncrawled = []
    for key, record in registry.items():
        if key not in fetched:
            if record.disposition in {"crawled", "failed"}:
                raise ValueError(f"Discovered URL has no corresponding page outcome: {record.url}")
            uncrawled.append(
                UncrawledUrl(
                    url=record.url,
                    status=record.disposition,
                    reason=record.reason,
                    **_discovery(record),
                )
            )
    counts = CrawlSummary(
        successful=sum(page.status == "success" for page in pages),
        failed=sum(page.status == "failed" for page in pages),
        discovered=len(pages) + len(uncrawled),
        **{
            name: sum(item.status == name for item in uncrawled)
            for name in ("skipped", "deferred", "external", "pending")
        },
    )
    return CrawlExportSummary(
        competitor_name=result.competitor_name,
        homepage_url=result.homepage_url,
        started_at=result.started_at,
        finished_at=result.finished_at,
        duration_seconds=round((result.finished_at - result.started_at).total_seconds(), 6),
        status=result.run_status,
        completion_reason=result.completion_reason,
        counts=counts,
        pages=pages,
        uncrawled_urls=uncrawled,
        discovery_truncated=result.discovery_truncated,
        warnings=[_compact_error(value) for value in result.warnings],
        decision_usage=result.decision_usage,
        decision_trace_file="decisions.jsonl"
        if result.effective_config.selection.trace_enabled
        else None,
    )


def export_competitor(
    result: CompetitorCrawlResult, output_directory: str | Path
) -> CrawlExportSummary:
    """Stage a complete competitor, refuse existing targets, publish its index last.

    A crawl_summary.json marks a completed export. Publication interruption can leave
    a folder without that marker; use a fresh output directory rather than replacing it.
    """
    paths = _page_paths(result)
    summary = _summary(result, paths)
    root = Path(output_directory).resolve()
    target = root / competitor_directory(str(result.homepage_url))
    if target.exists() or target.is_symlink():
        raise FileExistsError(
            f"Competitor output exists: {target}; choose a fresh --output directory"
        )
    root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".scraper-export-", dir=root) as temporary:
        stage = Path(temporary)
        (stage / "pages").mkdir()
        for page in result.pages:
            if page.crawl_status == "success":
                path = stage / paths[str(page.source_url)]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(page.cleaned_markdown, encoding="utf-8", newline="\n")
        if summary.decision_trace_file:
            with (stage / summary.decision_trace_file).open(
                "w", encoding="utf-8", newline="\n"
            ) as trace:
                for record in result.decision_trace:
                    trace.write(record.model_dump_json(exclude_none=True) + "\n")
        tracking_file = None
        reference = result.decision_tracking
        if reference is not None and reference.finished_at is not None:
            try:
                candidate = stage / "decision_tracking.json"
                candidate.write_text(
                    reference.model_dump_json(indent=2, exclude_none=True) + "\n",
                    encoding="utf-8", newline="\n",
                )
                tracking_file = candidate
            except (OSError, ValueError):
                logger.warning(
                    "Decisions tracking companion could not be staged; export continues."
                )
        payload = summary.model_dump(mode="json", exclude_none=True)
        diagnostics_staged = False
        try:
            (stage / "crawl_diagnostics.json").write_text(
                json.dumps(crawl_diagnostics(result), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            diagnostics_staged = True
        except Exception as error:
            logger.warning(
                "Crawl diagnostics staging failed (%s); export continues", type(error).__name__
            )
        for key in ("warnings", "discovery_truncated"):
            if not payload[key]:
                payload.pop(key)
        if not payload["counts"]["pending"]:
            payload["counts"].pop("pending")
        (stage / "crawl_summary.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8", newline="\n"
        )
        # mkdir is the no-overwrite reservation on both Windows and POSIX. Even a
        # directory created during staging is never replaced by a rename.
        target.mkdir()
        os.rename(stage / "pages", target / "pages")
        if summary.decision_trace_file:
            os.rename(stage / summary.decision_trace_file, target / summary.decision_trace_file)
        if diagnostics_staged:
            try:
                os.rename(stage / "crawl_diagnostics.json", target / "crawl_diagnostics.json")
            except Exception as error:
                logger.warning(
                    "Crawl diagnostics publication failed (%s); export continues",
                    type(error).__name__,
        if tracking_file is not None:
            try:
                os.rename(tracking_file, target / tracking_file.name)
            except OSError:
                logger.warning(
                    "Decisions tracking companion could not be published; export continues."
                )
        os.rename(stage / "crawl_summary.json", target / "crawl_summary.json")
    return summary
