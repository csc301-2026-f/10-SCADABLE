"""AI steps get their own, longer timeout than the crawl (reviewer retries take time)."""

import asyncio
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.api import serializers
from app.db.database import Base
from app.db.models import ResearchStatus
from app.db.repository import create_competitor, create_research_run
from app.services import orchestrator
from app.services.crawler import MockCrawl4AICrawler
from app.services.llm import MockOpenAIAnalyzer


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as session:
        yield session
    engine.dispose()


def _run(db, analyzer, timeout_seconds):
    competitor = create_competitor(db, name="Vanta", website_url="https://vanta.com")
    run = create_research_run(db, competitor.id)
    return asyncio.run(orchestrator.execute_research_run(
        db, run.id, MockCrawl4AICrawler(), analyzer, timeout_seconds=timeout_seconds,
    ))


def test_ai_steps_default_to_at_least_five_minutes(monkeypatch):
    monkeypatch.delenv("ANALYSIS_TIMEOUT_SECONDS", raising=False)

    assert orchestrator._llm_step_timeout("ANALYSIS_TIMEOUT_SECONDS", 90) == 300
    # A caller that allows longer keeps its own limit
    assert orchestrator._llm_step_timeout("ANALYSIS_TIMEOUT_SECONDS", 600) == 600


def test_ai_step_timeout_can_be_configured(monkeypatch):
    monkeypatch.setenv("ANALYSIS_TIMEOUT_SECONDS", "45")
    assert orchestrator._llm_step_timeout("ANALYSIS_TIMEOUT_SECONDS", 90) == 45

    monkeypatch.setenv("ANALYSIS_TIMEOUT_SECONDS", "0")
    with pytest.raises(ValueError, match="must be positive"):
        orchestrator._llm_step_timeout("ANALYSIS_TIMEOUT_SECONDS", 90)


def test_analysis_is_not_cut_off_at_the_crawl_timeout(monkeypatch, db):
    monkeypatch.delenv("ANALYSIS_TIMEOUT_SECONDS", raising=False)

    run = _run(db, MockOpenAIAnalyzer(delay_seconds=0.3), timeout_seconds=0.1)

    assert run.status == ResearchStatus.COMPLETED


def test_analysis_still_times_out_at_its_own_limit(monkeypatch, db):
    monkeypatch.setenv("ANALYSIS_TIMEOUT_SECONDS", "0.1")

    run = _run(db, MockOpenAIAnalyzer(delay_seconds=1.0), timeout_seconds=5)

    assert run.status == ResearchStatus.FAILED
    # The message names the analysis limit, not the crawl's, and the API shows it as is.
    assert run.error_message == "Research run timed out after 0.1 seconds."
    assert serializers.safe_run_error(run.error_message) == run.error_message


def test_crawl_timeout_names_the_crawl_limit(db):
    competitor = create_competitor(db, name="Drata", website_url="https://drata.com")
    slow = create_research_run(db, competitor.id)
    run = asyncio.run(orchestrator.execute_research_run(
        db, slow.id, MockCrawl4AICrawler(delay_seconds=1.0), MockOpenAIAnalyzer(), timeout_seconds=0.05,
    ))

    assert run.status == ResearchStatus.FAILED
    assert run.error_message == "Research run timed out after 0.05 seconds."
