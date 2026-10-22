"""Run from the repository root after installing both packages.

Analyze a saved bundle (no crawling):
    python examples/research_pipeline.py --export scraper/output/run-001/example.com/crawl_summary.json

Collect and analyze fresh content (replace the example URL):
    python examples/research_pipeline.py https://competitor.example/ --max-pages 3

Set OPENAI_API_KEY and OPENAI_MODEL in the root .env or process environment.
Live crawling also requires Chromium; see scraper/docs/quickstart.md for setup.
Results are printed as JSON; estimated API costs go to stderr at the end.
Crawl bundles retain the scraper's normal format.
"""

import argparse
import asyncio
import json
import logging
import sys
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

from openai import APITimeoutError
from scraper.environment import load_environment

from llm.analyzer import Analyzer
from llm.client import OpenAIClient
from llm.config import Settings
from llm.pipeline import analyze_export, run_pipeline
from llm.usage import LLMUsage, TokenRates
from scraper import CompetitorInput, ScraperConfig, SelectionConfig

PROJECT_DIRECTORY = Path(__file__).resolve().parents[1]


def _token_rate(value: str) -> Decimal:
    try:
        rate = Decimal(value)
    except InvalidOperation as error:
        raise argparse.ArgumentTypeError("rate must be a number") from error
    if not rate.is_finite() or rate < 0:
        raise argparse.ArgumentTypeError("rate must be finite and nonnegative")
    return rate


async def _run(args, client):
    try:
        analyzer = Analyzer(client)
        if args.summary_path is not None:
            return [await analyze_export(args.summary_path, analyzer=analyzer)]
        output = args.output or (
            PROJECT_DIRECTORY
            / "scraper/output"
            / f"research-{datetime.now(UTC):%Y-%m-%d_%H-%M-%S_%f}_UTC"
        )
        return await run_pipeline(
            [CompetitorInput(homepage_url=url) for url in args.homepages],
            output_directory=output,
            scraper_settings=ScraperConfig(
                max_pages=args.max_pages,
                selection=SelectionConfig(mode=args.selection_mode),
            ),
            analyzer=analyzer,
        )
    finally:
        await client.close()


def _print_cost_summary(results, usage: LLMUsage, *, replay: bool, rates) -> None:
    decisions = None
    if replay:
        decisions = Decimal(0)
    elif results is not None and all(
        result.crawl_summary.decision_usage is not None for result in results
    ):
        decisions = sum(
            (
                Decimal(str(result.crawl_summary.decision_usage.estimated_cost_usd))
                for result in results
            ),
            Decimal(0),
        )
    llm = usage.estimated_cost_usd(rates)
    total = decisions + llm if decisions is not None and llm is not None else None

    def amount(value):
        if value is None:
            return "unavailable"
        return f"${value.quantize(Decimal('0.000001'), rounding=ROUND_HALF_UP):.6f}"

    print("\nEstimated API cost for this run (USD)", file=sys.stderr)
    note = "saved export; no new crawl" if replay else "scraper estimate"
    print(f"Decisions API: {amount(decisions)} ({note})", file=sys.stderr)
    print(f"LLM analysis: {amount(llm)} ({usage.request_count} calls)", file=sys.stderr)
    if usage.responses:
        print(
            "  Tokens: "
            f"{sum(r.input_tokens for r in usage.responses):,} input "
            f"({sum(r.cached_input_tokens for r in usage.responses):,} cached reads, "
            f"{sum(r.cache_write_tokens for r in usage.responses):,} cache writes), "
            f"{sum(r.output_tokens for r in usage.responses):,} output",
            file=sys.stderr,
        )
    missing_usage = usage.request_count - len(usage.responses)
    if missing_usage:
        print(
            f"  Usage unavailable for {missing_usage} LLM call(s); "
            "token counts cover only responses with reported usage.",
            file=sys.stderr,
        )
    print(f"Total: {amount(total)}", file=sys.stderr)
    if total is None:
        print(
            "Incomplete estimate: missing usage/crawl totals or unsupported model, tier, "
            "or context pricing. Use --llm-rates for custom pricing.",
            file=sys.stderr,
        )
    print(
        "Rates: "
        + (
            "custom"
            if rates
            else "standard, verified 2026-10-20; default 30m cache writes"
        )
        + ". Estimates may differ from billing (unobserved retries, regional premiums).",
        file=sys.stderr,
    )


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
        help="Fresh crawl root; defaults to scraper/output/research-<UTC>",
    )
    parser.add_argument("--max-pages", type=int, default=3)
    parser.add_argument(
        "--llm-rates",
        nargs=4,
        type=_token_rate,
        metavar=("INPUT", "CACHED_INPUT", "OUTPUT", "CACHE_WRITE"),
        help="Override LLM USD per million tokens; default: built-in model rates",
    )
    parser.add_argument(
        "--selection-mode",
        choices=("llm", "structural"),
        default="llm",
        help="Crawl selection only; downstream analysis still uses OpenAI",
    )
    args = parser.parse_args(argv)
    if bool(args.homepages) == (args.summary_path is not None):
        parser.error("Provide homepage URLs or --export, exclusively")
    client = None
    results = None
    rates = None
    try:
        rates = TokenRates(*args.llm_rates) if args.llm_rates is not None else None
        load_environment(args.env_file, required=args.env_file is not None)
        client = OpenAIClient(Settings(_env_file=None))
        results = asyncio.run(_run(args, client))
        print(
            json.dumps(
                [result.model_dump(mode="json") for result in results], indent=2
            ),
            flush=True,
        )
        return 0
    except APITimeoutError as error:
        details = "\n".join(getattr(error, "__notes__", []))
        parser.exit(
            1,
            "Error: LLM request timed out after SDK retries. "
            "Increase OPENAI_TIMEOUT_SECONDS if needed and retry saved Markdown with --export.\n"
            + (f"{details}\n" if details else ""),
        )
    except (OSError, ValueError) as error:
        parser.exit(1, f"Error: {error}\n")
    except KeyboardInterrupt:
        return 130
    finally:
        if client is not None:
            _print_cost_summary(
                results, client.usage, replay=args.summary_path is not None, rates=rates
            )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    raise SystemExit(main())
