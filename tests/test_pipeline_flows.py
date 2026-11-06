"""Preserve SCADABLE and comparison flows using the scraper's contracts."""

import pytest

from llm import pipeline
from scraper import CompetitorInput
from tests.helpers import StubAnalyzer, StubScraper, company_result

pytestmark = pytest.mark.asyncio

SCADABLE = CompetitorInput(homepage_url="https://scadable.com/", name="SCADABLE")
CLUELY = CompetitorInput(homepage_url="https://cluely.com/", name="Cluely")
BLUEJ = CompetitorInput(homepage_url="https://www.bluej.com/", name="Blue J")


async def test_run_scadable_uses_scadable_extraction(tmp_path):
    scraper = StubScraper([company_result(SCADABLE)])
    analyzer = StubAnalyzer()

    result = await pipeline.run_scadable(
        SCADABLE, scraper=scraper, analyzer=analyzer, output_directory=tmp_path
    )

    assert result.extracted_information.company_name == "SCADABLE"
    assert analyzer.calls == [("extract", "SCADABLE")]


@pytest.mark.parametrize("run", [pipeline.run_scadable, pipeline.run_competitor])
async def test_company_without_pages_is_rejected_before_llm_calls(tmp_path, run):
    scraper = StubScraper([company_result(SCADABLE, empty=True)])
    analyzer = StubAnalyzer()

    with pytest.raises(ValueError, match="No usable Markdown"):
        await run(
            SCADABLE, scraper=scraper, analyzer=analyzer, output_directory=tmp_path
        )

    assert analyzer.calls == []
    assert scraper.closed


async def test_comparison_uses_shared_scraper_and_restores_competitor_input_order(
    tmp_path,
):
    scraper = StubScraper([company_result(c) for c in (SCADABLE, CLUELY, BLUEJ)])
    analyzer = StubAnalyzer()

    swot = await pipeline.run_comparison(
        SCADABLE,
        competitors=[CLUELY, BLUEJ],
        scraper=scraper,
        analyzer=analyzer,
        output_directory=tmp_path,
    )

    assert [e.what for e in swot.SWOT_entries] == ["s"]
    assert [note.company_name for note in swot.competitor_notes] == ["Cluely", "Blue J"]
    assert analyzer.comparison_input.scadable.company_name == "SCADABLE"
    assert len(scraper.calls) == 1
    assert [c.display_name for c in scraper.calls[0][0]] == [
        "SCADABLE",
        "Cluely",
        "Blue J",
    ]
    assert analyzer.calls[-1] == ("compare", None)


async def test_comparison_with_no_competitors_still_compares(tmp_path):
    analyzer = StubAnalyzer()
    await pipeline.run_comparison(
        SCADABLE,
        competitors=[],
        scraper=StubScraper([company_result(SCADABLE)]),
        analyzer=analyzer,
        output_directory=tmp_path,
    )
    assert analyzer.comparison_input.competitors == []


async def test_comparison_skips_comparison_when_company_extraction_fails(tmp_path):
    scraper = StubScraper([company_result(c) for c in (SCADABLE, CLUELY, BLUEJ)])
    analyzer = StubAnalyzer(fail_for={"Cluely"})
    with pytest.raises(RuntimeError, match="Cluely"):
        await pipeline.run_comparison(
            SCADABLE,
            competitors=[CLUELY, BLUEJ],
            scraper=scraper,
            analyzer=analyzer,
            output_directory=tmp_path,
        )
    assert ("compare", None) not in analyzer.calls
    assert scraper.closed
