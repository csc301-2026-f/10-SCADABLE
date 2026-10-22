"""Verify export replay and the shared scraper-to-analysis handoff."""

import asyncio

import httpx2
import pytest
from openai import APITimeoutError

from llm import pipeline
from scraper import CompetitorInput, ScraperConfig
from tests.helpers import (
    MARKDOWN,
    StubAnalyzer,
    StubScraper,
    company_result,
    make_result,
    write_export,
)

pytestmark = pytest.mark.asyncio


async def test_analyze_export_preserves_sources_and_returns_facts_and_analysis(
    tmp_path,
):
    index = write_export(tmp_path)
    analyzer = StubAnalyzer()

    result = await pipeline.analyze_export(index, analyzer=analyzer)

    assert result.crawl_summary.competitor_name == "Example"
    assert result.extracted_information.summary == "facts"
    assert result.analysis.summary == "competitor"
    assert analyzer.inputs[0].pages[0].markdown == MARKDOWN
    assert analyzer.calls == [("extract", "Example"), ("competitor", "Example")]


async def test_run_pipeline_uses_shared_crawl_many_and_keeps_export_metadata(tmp_path):
    companies = [
        CompetitorInput(homepage_url="https://one.example/", name="One"),
        CompetitorInput(homepage_url="https://two.example/", name="Two"),
    ]
    scraper = StubScraper([company_result(company) for company in companies])
    settings = ScraperConfig(selection={"mode": "structural"}, max_pages=3)
    analyzer = StubAnalyzer()

    results = await pipeline.run_pipeline(
        companies,
        scraper=scraper,
        scraper_settings=settings,
        output_directory=tmp_path,
        analyzer=analyzer,
    )

    assert [result.analysis.company_name for result in results] == ["Two", "One"]
    assert scraper.calls == [(companies, settings)]
    assert scraper.closed
    for name in ("one.example", "two.example"):
        assert (tmp_path / name / "crawl_summary.json").is_file()
    assert analyzer.inputs[0].pages[0].markdown == "  Two page  \n"


@pytest.mark.parametrize("problem", ["duplicates", "existing"])
async def test_output_conflicts_fail_before_scraping(tmp_path, problem):
    competitor = CompetitorInput(homepage_url="https://example.com/", name="Example")
    scraper = StubScraper([make_result()])
    companies = [competitor]
    if problem == "duplicates":
        companies.append(competitor)
        error = ValueError
    else:
        write_export(tmp_path)
        error = FileExistsError

    with pytest.raises(error):
        await pipeline.run_pipeline(
            companies,
            scraper=scraper,
            output_directory=tmp_path,
            analyzer=StubAnalyzer(),
        )
    assert scraper.calls == []


async def test_analysis_failure_closes_scraper_iterator_and_keeps_export_for_replay(
    tmp_path,
):
    competitor = CompetitorInput(homepage_url="https://example.com/", name="Example")
    scraper = StubScraper([make_result()])

    with pytest.raises(RuntimeError, match="Example"):
        await pipeline.run_pipeline(
            [competitor],
            scraper=scraper,
            output_directory=tmp_path,
            analyzer=StubAnalyzer(fail_for={"Example"}),
        )

    assert scraper.closed
    index = tmp_path / "example.com/crawl_summary.json"
    result = await pipeline.analyze_export(index, analyzer=StubAnalyzer())
    assert result.analysis.company_name == "Example"


@pytest.mark.parametrize("stage", ["extraction", "company analysis"])
async def test_timeout_preserves_sdk_error_and_identifies_saved_export(tmp_path, stage):
    index = write_export(tmp_path)
    timeout = APITimeoutError(
        request=httpx2.Request("POST", "https://api.openai.com/v1/responses")
    )

    class TimeoutAnalyzer(StubAnalyzer):
        async def extract_information(self, data):
            if stage == "extraction":
                raise timeout
            return await super().extract_information(data)

        async def analyze_competitor(self, data):
            raise timeout

    with pytest.raises(APITimeoutError) as raised:
        await pipeline.analyze_export(index, analyzer=TimeoutAnalyzer())
    assert raised.value is timeout
    notes = "\n".join(getattr(raised.value, "__notes__", []))
    assert stage in notes
    assert "Example" in notes
    assert str(index.resolve()) in notes
    assert "--export" in notes
    replay = await pipeline.analyze_export(index, analyzer=StubAnalyzer())
    assert replay.analysis.company_name == "Example"


async def test_cancelling_analysis_closes_scraper_iterator(tmp_path):
    entered = asyncio.Event()
    scraper = StubScraper([make_result()])

    class BlockedAnalyzer(StubAnalyzer):
        async def extract_information(self, data):
            entered.set()
            await asyncio.Event().wait()

    task = asyncio.create_task(
        pipeline.run_pipeline(
            [CompetitorInput(homepage_url="https://example.com/")],
            scraper=scraper,
            output_directory=tmp_path,
            analyzer=BlockedAnalyzer(),
        )
    )
    try:
        await asyncio.wait_for(entered.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert scraper.closed
        assert (tmp_path / "example.com/crawl_summary.json").is_file()
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_pipeline_accepts_protocol_iterator_without_aclose(tmp_path):
    class Results:
        pending = True

        def __aiter__(self):
            return self

        async def __anext__(self):
            if not self.pending:
                raise StopAsyncIteration
            self.pending = False
            return make_result()

    class IteratorScraper:
        def crawl_many(self, competitors, global_settings):
            return Results()

    results = await pipeline.run_pipeline(
        [CompetitorInput(homepage_url="https://example.com/")],
        scraper=IteratorScraper(),
        output_directory=tmp_path,
        analyzer=StubAnalyzer(),
    )

    assert results[0].analysis.company_name == "Example"
