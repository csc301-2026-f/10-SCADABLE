"""Async orchestration over the scraper's published Markdown and structured LLM analysis."""

import asyncio
from collections.abc import Sequence
from contextlib import AsyncExitStack
from pathlib import Path

from scraper.interfaces import ScraperService
from scraper.service import CompetitorScraper

from llm.analyzer import Analyzer
from llm.inputs import load_analysis_input
from llm.schemas import ComparisonInput, CompetitorResearchResult, SWOTAnalysisByURL
from scraper import CompetitorInput, ScraperConfig
from scraper.output import competitor_directory, export_competitor


async def analyze_export(
    summary_path: str | Path,
    *,
    analyzer: Analyzer
) -> CompetitorResearchResult:
    """Analyze an existing bundle without crawling. The caller owns the LLM client."""
    data = await asyncio.to_thread(load_analysis_input, summary_path)
    facts = await analyzer.extract_information(data)
    return CompetitorResearchResult(
        crawl_summary=data.crawl_summary,
        extracted_information=facts
    )


def _check_output(
    output_directory: Path, competitors: Sequence[CompetitorInput]
) -> None:
    if output_directory.exists() and not output_directory.is_dir():
        raise NotADirectoryError(f"Output must be a directory: {output_directory}")
    names = [competitor_directory(str(company.homepage_url)) for company in competitors]
    if len({name.casefold() for name in names}) != len(names):
        raise ValueError("Competitor output folders conflict; provide unique homepages")
    for name in names:
        target = output_directory / name
        if target.exists() or target.is_symlink():
            raise FileExistsError(
                f"Competitor output exists: {target}; choose a fresh output directory"
            )


async def run_pipeline(
    competitors: Sequence[CompetitorInput],
    *,
    output_directory: str | Path,
    analyzer: Analyzer,
    scraper_settings: ScraperConfig | None = None,
    scraper: ScraperService | None = None,
    scadable: CompetitorInput | None = None,
) -> list[CompetitorResearchResult]:
    """Collect, export and analyze companies in crawl-completion order.

    All companies share crawl_many's capacity limits, including optional SCADABLE.
    Errors/cancellation close the crawl iterator. Published bundles remain available
    for analyze_export() retries. Full Markdown is supplied without truncation;
    model context-limit errors propagate. The caller owns the analysis client.
    """
    companies = ([scadable] if scadable is not None else []) + list(competitors)
    if not companies:
        return []
    root = Path(output_directory).resolve()
    _check_output(root, companies)
    service = scraper if scraper is not None else CompetitorScraper()
    settings = scraper_settings if scraper_settings is not None else ScraperConfig()
    insights = []
    async with AsyncExitStack() as resources:
        results = service.crawl_many(companies, settings)
        close = getattr(results, "aclose", None)
        if close is not None:
            resources.push_async_callback(close)
        async for result in results:
            summary = await asyncio.to_thread(export_competitor, result, root)
            index = (
                root
                / competitor_directory(str(summary.homepage_url))
                / "crawl_summary.json"
            )
            insights.append(
                await analyze_export(
                    index,
                    analyzer=analyzer
                )
            )
    return insights


async def run_competitor(
    competitor: CompetitorInput,
    *,
    output_directory: str | Path,
    analyzer: Analyzer,
    scraper_settings: ScraperConfig | None = None,
    scraper: ScraperService | None = None,
) -> CompetitorResearchResult:
    results = await run_pipeline(
        [competitor],
        output_directory=output_directory,
        analyzer=analyzer,
        scraper_settings=scraper_settings,
        scraper=scraper,
    )
    return results[0]


async def run_scadable(
    scadable: CompetitorInput,
    *,
    output_directory: str | Path,
    analyzer: Analyzer,
    scraper_settings: ScraperConfig | None = None,
    scraper: ScraperService | None = None,
) -> CompetitorResearchResult:
    results = await run_pipeline(
        [],
        scadable=scadable,
        output_directory=output_directory,
        analyzer=analyzer,
        scraper_settings=scraper_settings,
        scraper=scraper,
    )
    return results[0]


async def run_comparison(
    scadable: CompetitorInput,
    *,
    competitors: Sequence[CompetitorInput],
    output_directory: str | Path,
    analyzer: Analyzer,
    scraper_settings: ScraperConfig | None = None,
    scraper: ScraperService | None = None,
) -> SWOTAnalysisByURL:
    results = await run_pipeline(
        competitors,
        scadable=scadable,
        output_directory=output_directory,
        analyzer=analyzer,
        scraper_settings=scraper_settings,
        scraper=scraper,
    )
    by_homepage = {
        str(result.crawl_summary.homepage_url): result.extracted_information for result in results
    }
    return await analyzer.compare(
        ComparisonInput(
            scadable=by_homepage[str(scadable.homepage_url)],
            competitors=[
                by_homepage[str(company.homepage_url)] for company in competitors
            ],
        )
    )
