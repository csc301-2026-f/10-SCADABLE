"""Regression tests for freshness and scoring behavior reviewed in D3."""

import asyncio
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.database import Base
from app.db import models  # noqa: F401 - register tables
from app.db import repository as repo
from app.services.crawler import MockCrawl4AICrawler
from app.services.llm import MockOpenAIAnalyzer
from app.services import orchestrator
from app.api.routes.swot import _overview


@compiles(JSONB, "sqlite")
def _jsonb_on_sqlite(element, compiler, **kw):
    # These tests do not rely on PostgreSQL JSONB-specific operations.
    return "JSON"


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as session:
        yield session
    engine.dispose()


class EmptySwotAnalyzer(MockOpenAIAnalyzer):
    async def compare(self, baseline_context, competitors):
        result = await super().compare(baseline_context, competitors)
        return {**result, "swot_entries": []}


class FailedComparisonAnalyzer(MockOpenAIAnalyzer):
    async def compare(self, baseline_context, competitors):
        raise RuntimeError("simulated comparison failure")


def _completed(db):
    company = repo.create_competitor(db, "Vanta", "https://vanta.com")
    run = repo.create_research_run(db, company.id)
    completed = asyncio.run(orchestrator.execute_research_run(
        db, run.id, MockCrawl4AICrawler(), MockOpenAIAnalyzer()
    ))
    return company, completed


def test_new_competitor_does_not_invalidate_or_show_stale(db):
    repo.create_competitor(db, "Vanta", "https://vanta.com")
    assert repo.get_overall_swot_revision(db) == 0
    assert _overview(db).is_stale is False
    assert _overview(db).updated_at is None


def test_refresh_attempts_do_not_advance_data_revision(db):
    _, _ = _completed(db)
    original_revision = repo.get_overall_swot_revision(db)
    assert repo.is_overall_swot_current(db)
    asyncio.run(orchestrator.refresh_overall_swot(db, MockOpenAIAnalyzer()))
    asyncio.run(orchestrator.refresh_overall_swot(db, FailedComparisonAnalyzer()))
    assert repo.get_overall_swot_revision(db) == original_revision
    assert repo.is_overall_swot_current(db)
    assert _overview(db).is_stale is False


def test_empty_comparison_preserves_valid_scores(db):
    company = repo.create_competitor(db, "Vanta", "https://vanta.com")
    run = repo.create_research_run(db, company.id)
    repo.save_analysis_result(db, run.id, {"features": ["Evidence collection"]})
    repo.update_research_run_status(db, run.id, models.ResearchStatus.RUNNING)
    repo.update_research_run_status(db, run.id, models.ResearchStatus.COMPLETED)
    repo.mark_overall_swot_stale(db)
    asyncio.run(orchestrator.refresh_overall_swot(db, EmptySwotAnalyzer()))
    assert repo.get_research_run(db, run.id).analysis_result["scores"] == [
        {"feature": "Evidence collection", "score": 7}
    ]
    assert not repo.is_overall_swot_current(db)


def test_obsolete_comparison_cannot_overwrite_scores(db):
    company, run = _completed(db)
    original = repo.get_research_run(db, run.id).analysis_result["scores"]
    revision = repo.get_overall_swot_revision(db)
    repo.mark_overall_swot_stale(db)
    assert not repo.save_comparison_scores_if_current(
        db, revision, {run.id: [{"feature": "Wrong", "score": 10}]},
        [{"category": "strength", "content": "Outdated", "source_url": None}]
    )
    assert repo.get_research_run(db, run.id).analysis_result["scores"] == original
    assert not repo.is_overall_swot_current(db)


def test_stale_marker_failure_does_not_fail_completed_run(db, monkeypatch):
    company = repo.create_competitor(db, "Vanta", "https://vanta.com")
    run = repo.create_research_run(db, company.id)
    def marker_failure(*args, **kwargs):
        raise RuntimeError("simulated revision marker failure")
    monkeypatch.setattr(orchestrator, "mark_overall_swot_stale", marker_failure)
    result = asyncio.run(orchestrator.execute_research_run(
        db, run.id, MockCrawl4AICrawler(), MockOpenAIAnalyzer()
    ))
    assert result.status == models.ResearchStatus.COMPLETED


def test_initializing_state_twice_is_idempotent(db):
    repo.ensure_overall_swot_state(db)
    repo.ensure_overall_swot_state(db)
    db.commit()
    assert repo.get_overall_swot_revision(db) == 0


def test_deleting_unresearched_competitor_does_not_invalidate_overview(db):
    _, _ = _completed(db)
    assert repo.is_overall_swot_current(db)
    original = repo.get_overall_swot_revision(db)
    new = repo.create_competitor(db, "Secureframe", "https://secureframe.com")
    repo.delete_competitor(db, new.id)
    assert repo.get_overall_swot_revision(db) == original
    assert repo.is_overall_swot_current(db)
