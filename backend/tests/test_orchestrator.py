"""Tests for research workflow orchestration, change detection, and task execution."""

import os

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session
from starlette.datastructures import State

from app.db.models import ResearchStatus, SWOTCategory, SWOTScope
from app.db.repository import (
    create_competitor,
    create_research_run,
    delete_competitor,
    get_latest_swot,
    get_overall_swot,
    is_overall_swot_current,
    mark_overall_swot_stale,
    publish_overall_swot,
    get_research_run,
    save_analysis_result,
    save_swot_entries,
    update_research_run_status,
)
from app.services.crawler import MockCrawl4AICrawler
from app.services.diff import compute_content_hash, has_content_changed
from app.services.llm import MockOpenAIAnalyzer
from app.services.orchestrator import (
    _scores_by_company,
    execute_research_run,
    refresh_overall_swot,
    trigger_overall_swot_refresh,
    trigger_research_task,
)


# ---------------------------------------------------------------------------
# Unit Tests for Diffing and Mock Services (Run anywhere without a database)
# ---------------------------------------------------------------------------

def test_compute_content_hash():
    data1 = {
        "pages": [
            {"url": "https://vanta.com/features", "text": "Continuous Automated Evidence"},
            {"url": "https://vanta.com/pricing", "text": "Starting at $7500/year"},
        ]
    }
    # Same content with different casing and whitespace should yield identical hash
    data2 = {
        "pages": [
            {"url": "https://vanta.com/features", "text": "  continuous   automated evidence  \n"},
            {"url": "https://vanta.com/pricing", "text": "starting at $7500/year  "},
        ]
    }
    data3 = {
        "pages": [
            {"url": "https://vanta.com/features", "text": "Continuous Automated Evidence"},
            {"url": "https://vanta.com/pricing", "text": "Starting at $10,000/year"},
        ]
    }

    assert compute_content_hash(data1) == compute_content_hash(data2)
    assert compute_content_hash(data1) != compute_content_hash(data3)
    assert compute_content_hash(None) == ""


def test_has_content_changed():
    initial_scrape = {
        "pages": [
            {"url": "https://drata.com/features", "text": "Real-time compliance monitoring"},
        ]
    }
    same_scrape = {
        "pages": [
            {"url": "https://drata.com/features", "text": "  Real-time compliance monitoring  "},
        ]
    }
    updated_scrape = {
        "pages": [
            {"url": "https://drata.com/features", "text": "Real-time compliance monitoring with AI"},
        ]
    }

    # Initial crawl has no prior snapshot: should register as changed
    assert has_content_changed(initial_scrape, None) is True
    # Unchanged content: should register as unchanged (skip LLM)
    assert has_content_changed(same_scrape, initial_scrape) is False
    # Modified content: should register as changed
    assert has_content_changed(updated_scrape, initial_scrape) is True


import asyncio

def test_mock_crawler():
    crawler = MockCrawl4AICrawler()
    result = asyncio.run(crawler.crawl("Vanta", "https://vanta.com"))

    assert result["competitor_name"] == "Vanta"
    assert len(result["pages"]) == 3
    urls = [p["url"] for p in result["pages"]]
    assert "https://vanta.com/features" in urls
    assert "https://vanta.com/pricing" in urls
    assert "https://vanta.com/docs" in urls


def test_mock_analyzer():
    analyzer = MockOpenAIAnalyzer()
    scraped = {
        "competitor_name": "Drata",
        "base_url": "https://drata.com",
    }
    result = asyncio.run(analyzer.analyze(scraped, {"baseline": "SCADABLE"}))

    assert result["competitor_name"] == "Drata"
    entries = result["swot_entries"]
    assert len(entries) >= 4
    categories = {e["category"] for e in entries}
    assert {"strength", "weakness", "opportunity", "threat"}.issubset(categories)
    for entry in entries:
        assert entry["source_url"].startswith("https://drata.com")


def test_mock_compare():
    analyzer = MockOpenAIAnalyzer()
    competitors = [
        {"company_name": "Vanta", "features": ["Evidence collection"]},
        {"company_name": "Drata"},
    ]
    baseline = {"differentiators": ["Native EU CRA tracking"]}
    result = asyncio.run(analyzer.compare(baseline, competitors))

    entries = result["swot_entries"]
    categories = {e["category"] for e in entries}
    assert {"strength", "weakness", "opportunity", "threat"} == categories
    assert any("Vanta, Drata" in e["content"] for e in entries)
    assert all(e["source_url"] is None for e in entries)
    assert result["scores"] == [
        {"company_name": "Vanta", "feature": "Evidence collection", "score": 7}
    ]


def test_scores_by_company_drops_invalid_items():
    raw = [
        {"company_name": "Vanta", "feature": " Audits ", "score": 6},
        {"company_name": "Vanta", "feature": "", "score": 5},
        {"company_name": "Vanta", "feature": "Too high", "score": 11},
        {"company_name": "Vanta", "feature": "Not a number", "score": "high"},
        {"company_name": "Drata", "feature": "Flag", "score": True},
        {"feature": "No company", "score": 4},
        "not a dict",
    ]
    assert _scores_by_company(raw) == {"Vanta": [{"feature": "Audits", "score": 6}]}
    assert _scores_by_company(None) == {}


class EmptyScoresAnalyzer(MockOpenAIAnalyzer):
    """Compares normally but returns no usable feature scores."""

    def __init__(self, scores):
        super().__init__()
        self.scores = scores

    async def compare(self, baseline_context, competitors):
        result = await super().compare(baseline_context, competitors)
        return {**result, "scores": self.scores}


def test_trigger_overall_swot_refresh():
    from fastapi import BackgroundTasks

    bg_tasks = BackgroundTasks()
    trigger_overall_swot_refresh(bg_tasks)

    assert len(bg_tasks.tasks) == 1


class CompareFailingAnalyzer(MockOpenAIAnalyzer):
    """Analyzes normally but fails the overall comparison."""

    async def compare(self, baseline_context, competitors):
        raise RuntimeError("Simulated comparison failure.")


class EmptyComparisonAnalyzer(MockOpenAIAnalyzer):
    """LLM comparison succeeded at transport level but yielded no findings."""

    async def compare(self, baseline_context, competitors):
        result = await super().compare(baseline_context, competitors)
        return {**result, "swot_entries": []}


# ---------------------------------------------------------------------------
# Integration Tests requiring PostgreSQL test database
# ---------------------------------------------------------------------------

test_url = os.getenv("TEST_DATABASE_URL")

if test_url:
    if test_url.startswith("postgres://"):
        test_url = test_url.replace("postgres://", "postgresql+psycopg://", 1)
    elif test_url.startswith("postgresql://"):
        test_url = test_url.replace("postgresql://", "postgresql+psycopg://", 1)

    parsed_url = make_url(test_url)
    if (
        parsed_url.get_backend_name() != "postgresql"
        or not parsed_url.database
        or not parsed_url.database.endswith("_test")
    ):
        raise RuntimeError("Tests require a PostgreSQL database ending in _test.")

    os.environ["DATABASE_URL"] = test_url
    from app.db.database import Base

    @pytest.fixture(scope="session")
    def engine():
        test_engine = create_engine(test_url, pool_pre_ping=True)
        Base.metadata.create_all(test_engine)
        yield test_engine
        test_engine.dispose()

    @pytest.fixture
    def db(engine):
        connection = engine.connect()
        transaction = connection.begin()
        session = Session(
            bind=connection,
            join_transaction_mode="create_savepoint",
            expire_on_commit=False,
        )
        try:
            yield session
        finally:
            session.close()
            transaction.rollback()
            connection.close()

    @pytest.fixture
    def competitor(db):
        return create_competitor(db, name="Vanta", website_url="https://vanta.com")

    def test_execute_research_run_success(db, competitor):
        run = create_research_run(db, competitor.id)
        assert run.status == ResearchStatus.PENDING

        crawler = MockCrawl4AICrawler()
        analyzer = MockOpenAIAnalyzer()

        finished_run = asyncio.run(execute_research_run(
            db=db,
            run_id=run.id,
            crawler=crawler,
            analyzer=analyzer,
        ))

        assert finished_run.status == ResearchStatus.COMPLETED
        assert finished_run.started_at is not None
        assert finished_run.completed_at is not None
        assert finished_run.error_message is None
        assert finished_run.scraped_data is not None
        assert finished_run.analysis_result is not None

        # Verify SWOT entries were persisted to DB with source URLs
        swot_rows = get_latest_swot(db, competitor.id)
        assert len(swot_rows) > 0
        for entry in swot_rows:
            assert entry.source_url is not None
            assert entry.source_url.startswith("https://vanta.com")

    def test_execute_research_run_diff_skips_llm(db, competitor):
        crawler = MockCrawl4AICrawler()
        analyzer = MockOpenAIAnalyzer()

        # Run 1: initial run, should call LLM
        run1 = create_research_run(db, competitor.id)
        res1 = asyncio.run(execute_research_run(db, run1.id, crawler, analyzer))
        assert res1.status == ResearchStatus.COMPLETED

        # Run 2: content has not changed, should skip LLM
        run2 = create_research_run(db, competitor.id)
        res2 = asyncio.run(execute_research_run(db, run2.id, crawler, analyzer))

        assert res2.status == ResearchStatus.COMPLETED
        assert res2.analysis_result.get("skipped_llm") is True
        assert res2.analysis_result.get("reused_from_run_id") == run1.id

    def test_execute_research_run_saves_overall_swot(db, competitor):
        crawler = MockCrawl4AICrawler()
        analyzer = MockOpenAIAnalyzer()

        run1 = create_research_run(db, competitor.id)
        asyncio.run(execute_research_run(db, run1.id, crawler, analyzer))

        overall = get_overall_swot(db)
        assert {e.category for e in overall} == set(SWOTCategory)
        assert all(e.scope == SWOTScope.OVERALL and e.competitor_id is None for e in overall)
        assert any("Vanta" in e.content for e in overall)
        # compare() scores are saved on the competitor's run for its profile chart
        scores = get_research_run(db, run1.id).analysis_result["scores"]
        assert scores and all(set(s) == {"feature", "score"} for s in scores)

        # Unchanged re-run must not call compare() again, but keeps the scores
        overall_ids = [e.id for e in overall]
        run2 = create_research_run(db, competitor.id)
        res2 = asyncio.run(execute_research_run(db, run2.id, crawler, analyzer))
        assert [e.id for e in get_overall_swot(db)] == overall_ids
        assert res2.analysis_result["scores"] == scores

    def test_overall_swot_revision_starts_stale_and_becomes_current(db, competitor):
        assert not is_overall_swot_current(db)
        run = create_research_run(db, competitor.id)
        asyncio.run(execute_research_run(
            db, run.id, MockCrawl4AICrawler(), MockOpenAIAnalyzer()
        ))
        assert is_overall_swot_current(db)
        assert get_overall_swot(db)

    def test_failed_refresh_keeps_current_results_when_unchanged(db, competitor):
        run = create_research_run(db, competitor.id)
        asyncio.run(execute_research_run(
            db, run.id, MockCrawl4AICrawler(), MockOpenAIAnalyzer()
        ))
        original = [e.id for e in get_overall_swot(db)]
        assert is_overall_swot_current(db)

        asyncio.run(refresh_overall_swot(db, CompareFailingAnalyzer()))
        # Old records remain for recovery, but public routes must hide them.
        assert [e.id for e in get_overall_swot(db)] == original
        assert is_overall_swot_current(db)

        # A good retry repopulates them and makes the overall view current again.
        asyncio.run(refresh_overall_swot(db, MockOpenAIAnalyzer()))
        assert is_overall_swot_current(db)
        assert get_overall_swot(db)

    def test_empty_refresh_keeps_current_results_when_unchanged(db, competitor):
        run = create_research_run(db, competitor.id)
        asyncio.run(execute_research_run(
            db, run.id, MockCrawl4AICrawler(), MockOpenAIAnalyzer()
        ))
        assert is_overall_swot_current(db)

        asyncio.run(refresh_overall_swot(db, EmptyComparisonAnalyzer()))
        assert is_overall_swot_current(db)
        assert get_overall_swot(db)  # preserved in storage, not served

        # Unchanged re-run must retry if the overall result is stale even when
        # old overall rows still exist.
        rerun = create_research_run(db, competitor.id)
        finished = asyncio.run(execute_research_run(
            db, rerun.id, MockCrawl4AICrawler(), MockOpenAIAnalyzer()
        ))
        assert finished.analysis_result["skipped_llm"] is True
        assert is_overall_swot_current(db)

    def test_older_comparison_cannot_publish_after_newer_update(db, competitor):
        revision = mark_overall_swot_stale(db)
        mark_overall_swot_stale(db)
        published = publish_overall_swot(db, [
            {"category": "strength", "content": "Old comparison", "source_url": None}
        ], revision)
        assert published is False
        assert not is_overall_swot_current(db)
        assert get_overall_swot(db) == []

    def test_deleting_competitor_invalidates_overall_immediately(db, competitor):
        run = create_research_run(db, competitor.id)
        asyncio.run(execute_research_run(
            db, run.id, MockCrawl4AICrawler(), MockOpenAIAnalyzer()
        ))
        assert is_overall_swot_current(db)
        delete_competitor(db, competitor.id)
        assert not is_overall_swot_current(db)
        assert get_overall_swot(db)
        asyncio.run(refresh_overall_swot(db, MockOpenAIAnalyzer()))
        assert is_overall_swot_current(db)
        assert get_overall_swot(db) == []

    def test_execute_research_run_compare_failure_keeps_run_completed(db, competitor):
        run = create_research_run(db, competitor.id)
        finished_run = asyncio.run(execute_research_run(
            db=db,
            run_id=run.id,
            crawler=MockCrawl4AICrawler(),
            analyzer=CompareFailingAnalyzer(),
        ))

        assert finished_run.status == ResearchStatus.COMPLETED
        assert len(get_latest_swot(db, competitor.id)) > 0
        assert get_overall_swot(db) == []

    @pytest.mark.parametrize("bad_scores", [
        [],
        [{"company_name": "Someone else", "feature": "Audits", "score": 5}],
        [{"company_name": "Vanta", "feature": "Audits", "score": 42}],
    ])
    def test_refresh_overall_swot_keeps_scores_when_comparison_has_none(
        db, competitor, bad_scores
    ):
        run = create_research_run(db, competitor.id)
        asyncio.run(execute_research_run(
            db, run.id, MockCrawl4AICrawler(), MockOpenAIAnalyzer()
        ))
        scores = get_research_run(db, run.id).analysis_result["scores"]
        assert scores

        asyncio.run(refresh_overall_swot(db, EmptyScoresAnalyzer(bad_scores)))
        assert get_research_run(db, run.id).analysis_result["scores"] == scores

    def test_refresh_overall_swot_clears_after_last_competitor_deleted(db, competitor):
        run = create_research_run(db, competitor.id)
        asyncio.run(execute_research_run(
            db, run.id, MockCrawl4AICrawler(), MockOpenAIAnalyzer()
        ))
        assert get_overall_swot(db)

        delete_competitor(db, competitor.id)
        asyncio.run(refresh_overall_swot(db, MockOpenAIAnalyzer()))
        assert get_overall_swot(db) == []

    def _completed_run(db, competitor, analysis, entries):
        run = create_research_run(db, competitor.id)
        save_analysis_result(db, run.id, analysis)
        save_swot_entries(db, run.id, entries)
        update_research_run_status(db, run.id, ResearchStatus.RUNNING)
        return update_research_run_status(db, run.id, ResearchStatus.COMPLETED)

    def test_refresh_overall_swot_scores_competitor_without_strengths_or_weaknesses(
        db, competitor
    ):
        # Analysis found features but no strengths/weaknesses (only template opportunity/threat)
        run = _completed_run(
            db,
            competitor,
            {"summary": "Vanta automates compliance.", "features": ["Evidence collection"]},
            [
                {"category": "opportunity", "content": "O", "source_url": "https://vanta.com"},
                {"category": "threat", "content": "T", "source_url": "https://vanta.com"},
            ],
        )

        asyncio.run(refresh_overall_swot(db, MockOpenAIAnalyzer()))

        assert get_research_run(db, run.id).analysis_result["scores"] == [
            {"feature": "Evidence collection", "score": 7}
        ]
        assert any("Vanta" in e.content for e in get_overall_swot(db))

    def test_refresh_overall_swot_scores_competitor_without_any_swot_entries(db, competitor):
        # Analysis found features but saved no SWOT rows at all
        run = _completed_run(
            db,
            competitor,
            {"summary": "Vanta automates compliance.", "features": ["Evidence collection"]},
            [],
        )

        asyncio.run(refresh_overall_swot(db, MockOpenAIAnalyzer()))

        assert get_research_run(db, run.id).analysis_result["scores"] == [
            {"feature": "Evidence collection", "score": 7}
        ]

    def test_refresh_overall_swot_keeps_scores_of_runs_without_features(db, competitor):
        # Run saved before features were stored: it is compared but its scores are kept
        old_scores = [{"feature": "Integrations", "score": 5}]
        run = _completed_run(
            db,
            competitor,
            {"summary": "Vanta automates compliance.", "scores": old_scores},
            [{"category": "strength", "content": "S", "source_url": "https://vanta.com/f"}],
        )

        asyncio.run(refresh_overall_swot(db, MockOpenAIAnalyzer()))

        assert get_research_run(db, run.id).analysis_result["scores"] == old_scores
        assert get_overall_swot(db)

    def test_execute_research_run_crawler_failure(db, competitor, caplog):
        run = create_research_run(db, competitor.id)
        failing_crawler = MockCrawl4AICrawler(should_fail=True)

        failed_run = asyncio.run(execute_research_run(
            db=db,
            run_id=run.id,
            crawler=failing_crawler,
        ))

        assert failed_run.status == ResearchStatus.FAILED
        assert failed_run.error_message == "Research failed due to an internal error."
        assert "simulated failure" in caplog.text
        assert "simulated failure" not in failed_run.error_message

    def test_execute_research_run_analyzer_failure(db, competitor):
        run = create_research_run(db, competitor.id)
        crawler = MockCrawl4AICrawler()
        failing_analyzer = MockOpenAIAnalyzer(should_fail=True)

        failed_run = asyncio.run(execute_research_run(
            db=db,
            run_id=run.id,
            crawler=crawler,
            analyzer=failing_analyzer,
        ))

        assert failed_run.status == ResearchStatus.FAILED
        assert failed_run.error_message == "Research failed due to an internal error."
        # Scraped data must remain preserved even if LLM fails
        assert failed_run.scraped_data is not None

    def test_trigger_research_task(db, competitor):
        from fastapi import BackgroundTasks

        bg_tasks = BackgroundTasks()
        run = trigger_research_task(
            competitor_id=competitor.id,
            background_tasks=bg_tasks,
            db=db,
        )

        assert run.id is not None
        assert run.status == ResearchStatus.PENDING
        assert len(bg_tasks.tasks) == 1

