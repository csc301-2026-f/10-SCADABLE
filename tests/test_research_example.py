"""The example exposes both invocation modes without credentials at startup."""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
from scraper.models import DiscoveredUrl, DiscoveryEvidence

from examples import research_pipeline
from llm import pipeline
from llm.schemas import JobCitedByURL, JobFamily
from tests.helpers import (
    StubAnalyzer,
    StubScraper,
    make_page,
    make_result,
    write_export,
)

EXAMPLE = Path(__file__).resolve().parents[1] / "examples/research_pipeline.py"


@pytest.mark.parametrize(
    "args,code,expected",
    [
        (["--help"], 0, "--export"),
        ([], 2, "Provide homepage URLs or --export"),
        (["https://example.com/", "--export", "unused.json"], 2, "exclusively"),
    ],
)
def test_example_invocation_modes_without_api_requests(args, code, expected):
    environment = dict(os.environ, PYTHON_DOTENV_DISABLED="1")
    environment.pop("OPENAI_API_KEY", None)
    result = subprocess.run(
        [sys.executable, "-B", str(EXAMPLE), *args],
        check=False,
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == code, result.stderr
    assert expected in result.stdout + result.stderr


HOME = "https://example.com/"
CAREERS = HOME + "careers"
JOBS = HOME + "jobs"
ATS = "https://boards.greenhouse.io/example"


def discovery(
    url, status, *, parent=None, source="internal_link", reason=None, **extra
):
    return DiscoveredUrl(
        url=url,
        normalized_url=url,
        disposition=status,
        discovery_source=source,
        parent_url=parent,
        depth=1 if parent else 0,
        reason=reason,
        **extra,
    )


@pytest.fixture
def diagnostic_crawl():
    return make_result(
        run_status="partial",
        completion_reason="page_limit",
        pages=[
            make_page(),
            make_page(
                source_url=CAREERS,
                page_category="careers",
                cleaned_markdown="# Careers\n\nfixture-body-must-stay-out-of-report",
            ),
            make_page(source_url=HOME + "duplicate"),
        ],
        discovered_urls=[
            discovery(
                HOME,
                "crawled",
                source="homepage",
                evidence=[
                    DiscoveryEvidence(
                        observed_url=HOME,
                        discovery_source="internal_link",
                        parent_url=CAREERS,
                    ),
                ],
            ),
            discovery(CAREERS, "crawled", parent=HOME),
            discovery(HOME + "duplicate", "crawled", parent=HOME),
            discovery(
                JOBS,
                "deferred",
                parent=CAREERS,
                reason="page_limit",
                evidence=[
                    DiscoveryEvidence(
                        observed_url=JOBS,
                        discovery_source="internal_link",
                        parent_url=HOME,
                    ),
                ],
            ),
            discovery(
                ATS + "?token=fixture-secret",
                "external",
                parent=CAREERS,
                source="external_link",
                reason="domain_not_approved",
            ),
            discovery(
                HOME + "sitemap-only",
                "deferred",
                source="sitemap",
                depth_basis="sitemap_seed",
                reason="page_limit",
            ),
            discovery(HOME + "orphan", "pending", parent=HOME + "missing-parent"),
        ],
    )


@pytest.fixture
def offline_cli(monkeypatch, tmp_path):
    class FakeClient:
        def __init__(self, settings):
            pass

        async def close(self):
            pass

    monkeypatch.setattr(research_pipeline, "PROJECT_DIRECTORY", tmp_path)
    monkeypatch.setattr(
        research_pipeline, "load_environment", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(research_pipeline, "Settings", lambda **kwargs: None)
    monkeypatch.setattr(research_pipeline, "OpenAIClient", FakeClient)
    monkeypatch.setattr(
        research_pipeline.Analyzer,
        "extract_information",
        StubAnalyzer().extract_information,
    )
    monkeypatch.setattr(
        pipeline, "CompetitorScraper", lambda: pytest.fail("Unexpected real crawl")
    )


def test_live_cli_exports_and_reports_with_fake_extraction(
    offline_cli, diagnostic_crawl, monkeypatch, tmp_path, capsys
):
    class FixtureScraper(StubScraper):
        async def crawl_many(self, competitors, settings):
            async for result in super().crawl_many(competitors, settings):
                yield result.model_copy(update={"effective_config": settings})

    class FixtureAnalyzer(StubAnalyzer):
        async def extract_information(self, data):
            extracted = await super().extract_information(data)
            return extracted.model_copy(
                update={
                    "job_postings": [
                        JobCitedByURL(
                            role="Engineering",
                            category="Technical",
                            job_page_url=CAREERS,
                            job_families=[
                                JobFamily(job_family="full-time", count=2),
                                JobFamily(job_family="part-time", count=1),
                            ],
                        ),
                    ]
                }
            )

    service = FixtureScraper([diagnostic_crawl])
    monkeypatch.setattr(pipeline, "CompetitorScraper", lambda: service)
    monkeypatch.setattr(
        research_pipeline.Analyzer,
        "extract_information",
        FixtureAnalyzer().extract_information,
    )
    assert research_pipeline.main([HOME, "--report", "--decision-trace"]) == 0
    stdout, report = capsys.readouterr()
    (run,) = (tmp_path / "examples/output").iterdir()
    assert re.fullmatch(r"research-\d{4}-\d\d-\d\d_\d\d-\d\d-\d\d_\d{6}_UTC", run.name)
    bundle = run / "scraper/example.com"
    assert (run / "extraction.json").read_text(encoding="utf-8") == stdout
    assert (
        json.loads(stdout)[0]["extracted_information"]["job_postings"][0]["role"]
        == "Engineering"
    )
    assert (bundle / "crawl_summary.json").is_file()
    assert (bundle / "decisions.jsonl").is_file()
    assert service.calls[0][1].max_pages == 5
    assert "crawl_summary.json" in report and "decisions.jsonl" in report
    assert "page_limit" in report
    assert re.search(r"fetched.*" + re.escape(CAREERS), report)
    assert re.search(r"deferred.*page_limit[\s\S]*" + re.escape(JOBS), report)
    assert re.search(
        r"external.*domain_not_approved.*blocked\)\n\s+\[ATS\].*" + re.escape(ATS),
        report,
    )
    assert "Sitemap entries" in report and "Unparented entries" in report
    assert "(cycle)" in report and "(also linked)" in report
    assert "Markdown sources (2" in report and "pages/careers.md" in report
    assert re.search(r"Engineering.*3.*" + re.escape(CAREERS), report)
    assert "fixture-secret" not in report and "fixture-body" not in report


def test_replay_cli_uses_fresh_output_and_preserves_bundle(
    offline_cli, diagnostic_crawl, tmp_path, capsys
):
    index = write_export(tmp_path / "original", diagnostic_crawl)
    before = {
        file: file.read_bytes() for file in index.parent.rglob("*") if file.is_file()
    }
    assert research_pipeline.main(["--export", str(index), "--report"]) == 0
    stdout, report = capsys.readouterr()
    (run,) = (tmp_path / "examples/output").iterdir()
    assert list(run.iterdir()) == [run / "extraction.json"]
    assert (run / "extraction.json").read_text(encoding="utf-8") == stdout
    assert json.loads(stdout)[0]["extracted_information"]["job_postings"] == []
    assert str(index) in report
    assert "No job postings extracted." in report
    assert {
        file: file.read_bytes() for file in index.parent.rglob("*") if file.is_file()
    } == before

    with pytest.raises(SystemExit, match="1"):
        research_pipeline.main(["--export", str(index), "--output", str(run)])
    stdout, stderr = capsys.readouterr()
    assert not stdout
    assert "already exists" in stderr


def test_live_output_override_and_page_limit_without_report(
    offline_cli, monkeypatch, tmp_path, capsys
):
    service = StubScraper([make_result()])
    monkeypatch.setattr(pipeline, "CompetitorScraper", lambda: service)
    run = tmp_path / "custom" / "run"
    assert research_pipeline.main([HOME, "--output", str(run), "--max-pages", "2"]) == 0
    stdout, stderr = capsys.readouterr()
    assert json.loads(stdout)[0]["crawl_summary"]["counts"]["successful"] == 1
    assert (run / "extraction.json").read_text(encoding="utf-8") == stdout
    assert not (run / "scraper/example.com/decisions.jsonl").exists()
    assert service.calls[0][1].max_pages == 2
    assert stderr == ""


def test_replay_report_retains_crawl_diagnostics_when_markdown_is_missing(
    offline_cli, tmp_path, capsys
):
    index = write_export(tmp_path / "original")
    (index.parent / "pages/index.md").unlink()
    with pytest.raises(SystemExit, match="1"):
        research_pipeline.main(["--export", str(index), "--report"])
    stdout, report = capsys.readouterr()
    assert stdout == ""
    assert "Crawl success (completed" in report
    assert "Extraction did not start" in report
    (run,) = (tmp_path / "examples/output").iterdir()
    assert not (run / "extraction.json").exists()
