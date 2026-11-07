import importlib.util
import os
from pathlib import Path

import pytest

from scraper import CompetitorCrawlResult, CrawlExportSummary, ScrapedPage
from scraper.types import utc_now


def load_runner():
    path = Path(__file__).parents[2] / "manual_test.py"
    spec = importlib.util.spec_from_file_location("manual_runner", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_manual_runner_exposes_selection_controls_without_credentials():
    runner = load_runner()
    # Edit selection settings beside the existing crawl limits; no config file needed.
    assert "selection" in runner.SETTINGS.model_fields_set
    assert {"mode", "budget_usd", "trace_enabled"} <= runner.SETTINGS.selection.model_fields_set
    assert runner.SETTINGS.selection.objective == runner.SelectionConfig().objective
    assert "objective" not in runner.SETTINGS.selection.model_fields_set
    assert runner.SETTINGS.selection.mode == "llm"
    assert runner.SETTINGS.selection.budget_usd == 0.03
    assert runner.SETTINGS.selection.trace_enabled is False
    assert "api_key" not in runner.SETTINGS.model_dump_json()


def test_manual_runner_loads_project_dotenv_from_any_working_directory(tmp_path, monkeypatch):
    runner = load_runner()
    project = tmp_path / "scraper"
    project.mkdir()
    (tmp_path / ".env").write_text("OPENAI_API_KEY=runner-fixture-key\n", encoding="utf-8")
    (project / ".env").write_text("OPENAI_API_KEY=obsolete-key\n", encoding="utf-8")
    monkeypatch.setattr(runner, "PROJECT_DIRECTORY", project)
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("PYTHON_DOTENV_DISABLED", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "")
    monkeypatch.delenv("OPENAI_API_KEY")
    runner.configure_runtime()
    assert os.environ["OPENAI_API_KEY"] == "runner-fixture-key"


async def test_manual_runner_uses_editable_settings_and_exports_each_website(tmp_path, monkeypatch):
    runner = load_runner()
    runner.OUTPUT_DIRECTORY = tmp_path / "manual-run"
    runner.WEBSITES = [
        runner.CompetitorInput(homepage_url="https://one.example/"),
        runner.CompetitorInput(homepage_url="https://two.example/"),
    ]
    runner.SETTINGS = runner.ScraperConfig(max_pages=2, max_depth=1)
    observed = {}

    class FixtureService:
        async def crawl_many(self, websites, settings):
            observed["websites"] = websites
            observed["settings"] = settings
            for competitor in websites:
                now = utc_now()
                yield CompetitorCrawlResult(
                    competitor_name=competitor.display_name,
                    homepage_url=competitor.homepage_url,
                    started_at=now,
                    finished_at=now,
                    run_status="success",
                    completion_reason="completed",
                    pages=[
                        ScrapedPage(
                            competitor_name=competitor.display_name,
                            homepage_url=competitor.homepage_url,
                            source_url=competitor.homepage_url,
                            crawl_timestamp=now,
                            crawl_status="success",
                            raw_markdown="# Home\n\nPlans $49",
                            cleaned_markdown="# Home\n\nPlans $49",
                        )
                    ],
                )

    monkeypatch.setattr(runner, "CompetitorScraper", FixtureService)
    monkeypatch.setattr(runner, "configure_runtime", lambda: None)
    assert await runner.main() == 0
    assert observed["websites"] == runner.WEBSITES
    assert observed["settings"].max_pages == 2
    for hostname in ("one.example", "two.example"):
        folder = runner.OUTPUT_DIRECTORY / hostname
        summary = CrawlExportSummary.model_validate_json(
            (folder / "crawl_summary.json").read_text(encoding="utf-8")
        )
        assert summary.counts.successful == 1
        assert (folder / summary.pages[0].markdown_file).read_text(
            encoding="utf-8"
        ) == "# Home\n\nPlans $49"


async def test_existing_run_is_rejected_before_crawling(tmp_path, monkeypatch):
    runner = load_runner()
    runner.OUTPUT_DIRECTORY = tmp_path
    notes = tmp_path / "notes.txt"
    notes.write_text("keep this")

    def forbidden():
        raise AssertionError("Should reject output before creating a crawler")

    monkeypatch.setattr(runner, "CompetitorScraper", forbidden)
    monkeypatch.setattr(runner, "configure_runtime", lambda: None)
    with pytest.raises(FileExistsError):
        await runner.main()
    assert notes.read_text() == "keep this"


async def test_output_failure_closes_the_crawl_iterator(tmp_path, monkeypatch):
    runner = load_runner()
    runner.OUTPUT_DIRECTORY = tmp_path / "run"
    closed = []

    class FixtureService:
        async def crawl_many(self, websites, settings):
            try:
                now = utc_now()
                yield CompetitorCrawlResult(
                    competitor_name="Example",
                    homepage_url=websites[0].homepage_url,
                    started_at=now,
                    finished_at=now,
                    run_status="failed",
                    completion_reason="failed",
                )
            finally:
                closed.append(True)

    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr(runner, "configure_runtime", lambda: None)
    monkeypatch.setattr(runner, "CompetitorScraper", FixtureService)
    monkeypatch.setattr(runner, "export_competitor", fail)
    with pytest.raises(OSError, match="disk full"):
        await runner.main()
    assert closed == [True]
