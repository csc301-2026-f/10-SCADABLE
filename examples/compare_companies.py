"""Compare SCADABLE against competitors. Run from the repository root.

Crawl everything fresh, then compare (replace the competitor URLs):
    python examples/compare_companies.py --scadable https://scadable.com/ https://cluely.com/ https://www.bluej.com/ --max-pages 3

Compare saved crawls without crawling again:
    python examples/compare_companies.py --scadable-export scraper/output/<run>/scadable.com/crawl_summary.json --export scraper/output/<run>/cluely.com/crawl_summary.json

Each company costs one extraction call and the comparison adds one call over all of them;
fresh crawls also make the scraper's page-selection calls (skip them with
--selection-mode structural). Set OPENAI_API_KEY and OPENAI_MODEL in the root .env.
Prints a readable report, or the raw result with --json.
"""

import argparse
import asyncio
import json
import logging
from datetime import UTC, datetime
from pathlib import Path

from scraper.environment import load_environment

from llm.analyzer import Analyzer
from llm.client import OpenAIClient
from llm.config import Settings
from llm.pipeline import analyze_export, run_pipeline
from llm.schemas import ComparisonInput, SWOTAnalysisByURL
from scraper import CompetitorInput, ScraperConfig, SelectionConfig

PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]
SWOT_HEADINGS = {
    "strength": "Strengths",
    "weakness": "Weaknesses",
    "opportunity": "Opportunities",
    "threat": "Threats",
}


async def _extract_fresh(args, analyzer):
    scadable = CompetitorInput(homepage_url=args.scadable, name="SCADABLE")
    competitors = [CompetitorInput(homepage_url=url) for url in args.competitors]
    output = args.output or (
        PROJECT_DIRECTORY
        / "scraper/output"
        / f"comparison-{datetime.now(UTC):%Y-%m-%d_%H-%M-%S_%f}_UTC"
    )
    results = await run_pipeline(
        competitors,
        scadable=scadable,
        output_directory=output,
        scraper_settings=ScraperConfig(
            max_pages=args.max_pages,
            selection=SelectionConfig(mode=args.selection_mode),
        ),
        analyzer=analyzer,
    )
    # Results arrive in crawl-completion order; put them back in input order
    by_homepage = {str(r.crawl_summary.homepage_url): r.extracted_information for r in results}
    return (
        by_homepage[str(scadable.homepage_url)],
        [by_homepage[str(c.homepage_url)] for c in competitors],
    )


async def _extract_saved(args, analyzer):
    scadable = await analyze_export(args.scadable_export, analyzer=analyzer)
    competitors = [await analyze_export(path, analyzer=analyzer) for path in args.exports]
    return scadable.extracted_information, [c.extracted_information for c in competitors]


async def _run(args) -> SWOTAnalysisByURL:
    client = OpenAIClient(Settings(_env_file=None))
    try:
        analyzer = Analyzer(client)
        extract = _extract_saved if args.scadable_export else _extract_fresh
        scadable, competitors = await extract(args, analyzer)
        return await analyzer.compare(ComparisonInput(scadable=scadable, competitors=competitors))
    finally:
        await client.close()


def _print_report(swot: SWOTAnalysisByURL) -> None:
    print("SWOT for SCADABLE")
    for component, heading in SWOT_HEADINGS.items():
        entries = [e for e in swot.SWOT_entries if e.component == component]
        print(f"\n{heading} ({len(entries)})")
        for entry in entries:
            print(f"  - {entry.what}")
            for url in entry.swot_component_page_urls:
                print(f"      source: {url}")

    print("\nFeature scores (0-10)")
    if not swot.scores:
        print("  none")
    for company in dict.fromkeys(s.company_name for s in swot.scores):
        print(f"  {company}")
        for score in (s for s in swot.scores if s.company_name == company):
            print(f"    {score.score:>2}  {score.feature}")

    print("\nCompetitor notes")
    for note in swot.competitor_notes:
        print(f"  {note.company_name}: {note.why_it_matters}")

    conflicts = [entry for entry in swot.conflicting_data if entry.conflicts]
    if conflicts:
        print("\nConflicting claims")
        for entry in conflicts:
            print(f"  {entry.company_name}")
            for c in entry.conflicts:
                print(f"    - \"{c.claim_a}\" ({c.claim_a_page_URL})")
                print(f"      vs \"{c.claim_b}\" ({c.claim_b_page_URL})")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("competitors", nargs="*", help="Competitor homepage URLs to crawl")
    parser.add_argument("--scadable", help="SCADABLE homepage URL to crawl")
    parser.add_argument(
        "--scadable-export", type=Path, help="SCADABLE's saved crawl_summary.json"
    )
    parser.add_argument(
        "--export",
        dest="exports",
        type=Path,
        action="append",
        default=[],
        help="A competitor's saved crawl_summary.json; repeat for each competitor",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        help="Credential file; defaults to the repository-root .env",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Fresh crawl root; defaults to scraper/output/comparison-<UTC>",
    )
    parser.add_argument("--max-pages", type=int, default=3)
    parser.add_argument(
        "--selection-mode",
        choices=("llm", "structural"),
        default="llm",
        help="Crawl selection only; analysis still uses OpenAI",
    )
    parser.add_argument("--json", action="store_true", help="Print the raw result as JSON")
    args = parser.parse_args(argv)

    fresh = args.scadable is not None or bool(args.competitors)
    saved = args.scadable_export is not None or bool(args.exports)
    if fresh == saved:
        parser.error("Use either --scadable with competitor URLs, or --scadable-export with --export")
    if fresh and not (args.scadable and args.competitors):
        parser.error("Fresh comparisons need --scadable and at least one competitor URL")
    if saved and not (args.scadable_export and args.exports):
        parser.error("Saved comparisons need --scadable-export and at least one --export")

    try:
        load_environment(args.env_file, required=args.env_file is not None)
        swot = asyncio.run(_run(args))
    except (OSError, ValueError) as error:
        parser.exit(1, f"Error: {error}\n")
    except KeyboardInterrupt:
        return 130
    if args.json:
        print(json.dumps(swot.model_dump(mode="json"), indent=2))
    else:
        _print_report(swot)
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    raise SystemExit(main())
