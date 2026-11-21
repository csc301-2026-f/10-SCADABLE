"""Crawl, export, and extract competitor information.

Extract from a saved bundle:
    python examples/research_pipeline.py --export path/to/crawl_summary.json --report

Crawl and extract fresh content:
    python examples/research_pipeline.py https://competitor.example/ --report

Set OPENAI_API_KEY and OPENAI_MODEL in the root .env or process environment.
Live crawling also requires Chromium; see scraper/docs/quickstart.md for setup.
JSON goes to stdout and extraction.json under examples/output/research-<UTC timestamp>/.
Live runs also save scraper/<competitor>/ bundles there. Reports go to stderr.
"""

import argparse
import asyncio
import json
import logging
import re
import sys
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from urllib.parse import unquote, urlsplit

from scraper.discovery.urls import normalize_url
from scraper.environment import load_environment
from scraper.models import UncrawledUrl

from llm.analyzer import Analyzer
from llm.client import OpenAIClient
from llm.config import Settings
from llm.pipeline import analyze_export, run_pipeline
from llm.schemas import AnalysisInput, ExtractedInformationByURL
from scraper import (
    CompetitorInput,
    CrawlExportSummary,
    ExportedPage,
    ScraperConfig,
    SelectionConfig,
)
from scraper.output import competitor_directory

PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
TREE_MAX_DEPTH = 4
TREE_MAX_LINES = 80
ATS_DOMAINS = (
    "ashbyhq.com",
    "bamboohr.com",
    "greenhouse.io",
    "icims.com",
    "lever.co",
    "myworkdayjobs.com",
    "recruitee.com",
    "smartrecruiters.com",
    "workable.com",
)


def _report(message: str) -> None:
    # Strip terminal control characters from exported labels and local paths.
    print(
        "".join(char if char.isprintable() else " " for char in message),
        file=sys.stderr,
        flush=True,
    )


def _display_url(url: str) -> str:
    """Display a URL without user info, query parameters, or fragments."""
    try:
        parts = urlsplit(url)
        host = parts.netloc.rsplit("@", 1)[-1]
        if parts.scheme in {"http", "https"} and host:
            return f"{parts.scheme}://{host}{parts.path}"
    except ValueError:
        pass
    return "(invalid URL)"


def _candidate(item: ExportedPage | UncrawledUrl) -> str:
    urls = [str(item.url)]
    if isinstance(item, ExportedPage) and item.final_url:
        urls.append(str(item.final_url))
    labels = set()
    for url in urls:
        parts = urlsplit(url)
        host = parts.hostname or ""
        if any(host == domain or host.endswith(f".{domain}") for domain in ATS_DOMAINS):
            labels.add("ATS")
        words = set(re.findall(r"[a-z]+", unquote(f"{host}{parts.path}").lower()))
        if words & {"career", "careers", "hiring"}:
            labels.add("careers")
        if words & {"job", "jobs", "openings", "vacancies"}:
            labels.add("jobs")
    if isinstance(item, ExportedPage) and item.category in {"careers", "hiring"}:
        labels.add("careers")
    return "".join(f"[{label}] " for label in sorted(labels)) + _display_url(
        str(item.url)
    )


def _url_key(url) -> str:
    normalized = normalize_url(str(url))
    return normalized.normalized if normalized else str(url)


def _discovery_tree(
    summary: CrawlExportSummary, items: dict[str, ExportedPage | UncrawledUrl]
) -> None:
    children = defaultdict(list)
    homepage = _url_key(summary.homepage_url)
    sitemap = []
    unparented = []
    for key, item in items.items():
        parents = {_url_key(parent) for parent in item.discovered_from} & items.keys()
        for parent in parents:
            children[parent].append(key)
        if key == homepage:
            continue
        if "sitemap" in item.discovery_sources or item.depth_basis == "sitemap_seed":
            sitemap.append(key)
        elif not parents:
            unparented.append(key)

    home = [homepage] if homepage in items else []
    # Find disconnected components independently of the display limits, so depth
    # truncation never turns descendants into unparented roots.
    reachable = set()
    roots = {*home, *sitemap, *unparented}
    for root in [*home, *sitemap, *unparented, *items]:
        if root in reachable:
            continue
        if root not in roots:
            unparented.append(root)
        pending = [root]
        while pending:
            key = pending.pop()
            if key not in reachable:
                reachable.add(key)
                pending.extend(children[key])

    expanded_at: dict[str, int] = {}
    lines = 0
    truncated = False

    def visit(key, indent, ancestors):
        nonlocal lines, truncated
        item = items[key]
        cycle = key in ancestors
        repeated = key in expanded_at
        at_limit = indent == TREE_MAX_DEPTH and bool(children[key])
        note = ""
        if cycle:
            note = " (cycle)"
        elif repeated:
            note = " (also linked)"
        elif at_limit:
            note = " (depth limit)"
        depth = item.depth if item.depth is not None else "?"
        _report(
            f"{'  ' * (indent + 1)}- depth {depth} [{item.status}] {_candidate(item)}{note}"
        )
        lines += 1
        if cycle or (repeated and expanded_at[key] <= indent):
            return
        if at_limit:
            truncated = True
            return
        expanded_at[key] = indent
        for child in children[key]:
            if lines >= TREE_MAX_LINES:
                truncated = True
                break
            visit(child, indent + 1, ancestors | {key})

    _report("Discovery tree (recorded parents)")
    for label, roots in (
        ("Homepage links", home),
        ("Sitemap entries", sitemap),
        ("Unparented entries", unparented),
    ):
        if roots:
            _report(f"  {label}")
            for key in roots:
                if lines >= TREE_MAX_LINES:
                    truncated = True
                    break
                visit(key, 0, set())
    if truncated:
        _report("  Tree limited. See crawl_summary.json for the full inventory.")


def _crawl_report(summary: CrawlExportSummary, index: Path) -> None:
    _report(f"Crawl bundle: {index.parent}")
    _report(f"Full crawl inventory: {index}")
    counts = ", ".join(
        f"{name}={count}" for name, count in summary.counts.model_dump().items()
    )
    _report(
        f"Crawl {summary.status} ({summary.completion_reason}, "
        f"{summary.duration_seconds:.2f}s, {len(summary.pages)} attempted): {counts}"
    )
    if summary.discovery_truncated:
        _report("Discovery reached its inventory limit.")
    _report("Attempted URLs")
    for page in summary.pages:
        outcome = "fetched" if page.status == "success" else "failed"
        http = f", HTTP {page.http_status}" if page.http_status else ""
        redirect = f" -> {_display_url(str(page.final_url))}" if page.final_url else ""
        _report(f"  {outcome}{http}: {_candidate(page)}{redirect}")
    groups = defaultdict(list)
    for item in summary.uncrawled_urls:
        # Scraper reasons are identifiers; avoid displaying arbitrary diagnostic text.
        reason = item.reason or "no_reason_recorded"
        if reason.startswith("duplicate_redirect:"):
            reason = "duplicate_redirect"
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", reason):
            reason = "see_crawl_summary"
        groups[item.status, reason].append(item)
    _report("Unfetched URLs")
    for (status, reason), group in groups.items():
        blocked = (
            ", blocked"
            if reason in {"domain_not_approved", "robots_disallowed"}
            else ""
        )
        _report(f"  {status} / {reason} ({len(group)}{blocked})")
        for item in group:
            _report(f"    {_candidate(item)}")
    _discovery_tree(
        summary,
        {
            _url_key(item.url): item
            for item in [*summary.pages, *summary.uncrawled_urls]
        },
    )


class DiagnosticAnalyzer(Analyzer):
    def __init__(self, client: OpenAIClient, args, output: Path) -> None:
        super().__init__(client)
        self.args = args
        self.output = output
        self.reported_indexes: set[Path] = set()

    def show_crawl(self, summary: CrawlExportSummary, index: Path) -> None:
        self.reported_indexes.add(index)
        if self.args.report:
            _crawl_report(summary, index)
        if summary.decision_trace_file:
            trace = index.parent / summary.decision_trace_file
            if trace.is_file():
                _report(f"Decisions trace: {trace}")

    async def extract_information(
        self, data: AnalysisInput
    ) -> ExtractedInformationByURL:
        summary = data.crawl_summary
        index = self.args.summary_path or (
            self.output
            / "scraper"
            / competitor_directory(str(summary.homepage_url))
            / "crawl_summary.json"
        )
        self.show_crawl(summary, index)
        if self.args.report:
            _report(f"Markdown sources ({len(data.pages)} after deduplication)")
            for page in data.pages:
                _report(
                    f"  {page.source.markdown_file}  {_display_url(str(page.source.url))}"
                )
            _report("Extracting information...")
        started = perf_counter()
        try:
            result = await super().extract_information(data)
        except (Exception, asyncio.CancelledError) as error:
            if self.args.report:
                status = (
                    "interrupted"
                    if isinstance(error, asyncio.CancelledError)
                    else "failed"
                )
                _report(f"Extraction {status} after {perf_counter() - started:.2f}s.")
            raise
        if self.args.report:
            _report(f"Extraction succeeded in {perf_counter() - started:.2f}s.")
            if not result.job_postings:
                _report("No job postings extracted.")
            for job in result.job_postings:
                count = sum(family.count for family in job.job_families)
                _report(f"  {job.role} ({count})  {_display_url(job.job_page_url)}")
        return result

    def show_unreported_crawls(self) -> None:
        # Input loading can fail before the analyzer sees an otherwise valid index.
        indexes = (
            [self.args.summary_path]
            if self.args.summary_path
            else (self.output / "scraper").glob("*/crawl_summary.json")
        )
        for index in indexes:
            if index in self.reported_indexes:
                continue
            try:
                summary = CrawlExportSummary.model_validate_json(index.read_bytes())
            except (OSError, ValueError):
                continue
            self.show_crawl(summary, index)
            if self.args.report:
                _report(
                    "Extraction did not start. Check the export and its Markdown files."
                )


async def _run(args, output: Path):
    client = OpenAIClient(Settings(_env_file=None))
    analyzer = (
        DiagnosticAnalyzer(client, args, output)
        if args.report or args.decision_trace
        else Analyzer(client)
    )
    try:
        if args.summary_path is not None:
            return [await analyze_export(args.summary_path, analyzer=analyzer)]
        return await run_pipeline(
            [CompetitorInput(homepage_url=url) for url in args.homepages],
            output_directory=output / "scraper",
            scraper_settings=ScraperConfig(
                max_pages=args.max_pages,
                selection=SelectionConfig(
                    mode=args.selection_mode, trace_enabled=args.decision_trace
                ),
            ),
            analyzer=analyzer,
        )
    except Exception:
        if isinstance(analyzer, DiagnosticAnalyzer):
            analyzer.show_unreported_crawls()
        raise
    finally:
        await client.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "homepages", nargs="*", help="Competitor homepage URLs to collect"
    )
    parser.add_argument(
        "--export", dest="summary_path", type=Path, help="Analyze an existing index"
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        help="Credential file; defaults to the repository-root .env",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Fresh run directory; defaults to examples/output/research-<UTC timestamp>",
    )
    parser.add_argument(
        "--max-pages", type=int, default=5, help="Pages per competitor (default: 5)"
    )
    parser.add_argument(
        "--selection-mode",
        choices=("llm", "structural"),
        default="llm",
        help="Crawl selection only; downstream analysis still uses OpenAI",
    )
    parser.add_argument(
        "--report", action="store_true", help="Print diagnostics to stderr"
    )
    parser.add_argument(
        "--decision-trace",
        action="store_true",
        help="Live only: save Decisions evidence and answers in decisions.jsonl",
    )
    args = parser.parse_args(argv)
    if bool(args.homepages) == (args.summary_path is not None):
        parser.error("Provide homepage URLs or --export, exclusively")
    if args.summary_path is not None and args.decision_trace:
        parser.error("--decision-trace applies only to live crawls")

    try:
        if args.summary_path is not None:
            args.summary_path = args.summary_path.resolve(strict=True)
        output = (
            args.output
            or (
                PROJECT_DIRECTORY
                / "examples/output"
                / f"research-{datetime.now(UTC):%Y-%m-%d_%H-%M-%S_%f}_UTC"
            )
        ).absolute()
        if output.exists() or output.is_symlink():
            raise FileExistsError(f"Run directory already exists: {output}")
        if args.summary_path and output.resolve().is_relative_to(
            args.summary_path.parent
        ):
            raise ValueError("Choose a run directory outside the original crawl bundle")
        output.mkdir(parents=True, exist_ok=False)
    except (OSError, ValueError) as error:
        parser.exit(1, f"Error: {error}\n")

    extraction_path = output / "extraction.json"
    if args.report:
        _report(f"Run directory: {output}")
        _report(f"Extraction output: {extraction_path}")
        _report(
            f"Reading saved crawl: {args.summary_path}"
            if args.summary_path
            else f"Crawling {len(args.homepages)} site(s), up to {args.max_pages} pages each..."
        )
    try:
        load_environment(args.env_file, required=args.env_file is not None)
        results = asyncio.run(_run(args, output.resolve()))
        payload = (
            json.dumps([result.model_dump(mode="json") for result in results], indent=2)
            + "\n"
        )
        with extraction_path.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
    except KeyboardInterrupt:
        return 130
    except Exception as error:
        if args.report or isinstance(error, (OSError, ValueError)):
            parser.exit(1, f"Error: research run failed ({type(error).__name__}).\n")
        raise
    sys.stdout.write(payload)
    return 0


if __name__ == "__main__":
    # The CLI's own diagnostics avoid the raw URLs and exception bodies in library logs.
    logging.disable(logging.CRITICAL)
    raise SystemExit(main())
