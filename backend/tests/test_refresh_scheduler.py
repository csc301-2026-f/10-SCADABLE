"""Refresh policy, DB claims, restart recovery, and API scheduling behavior."""

import os
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session

from app.db.database import Base
from app.db.models import ResearchStatus
from app.db.repository import (
    ResearchAlreadyActiveError,
    create_competitor,
    create_research_run,
    get_latest_research_run,
    update_research_run_status,
)
from app.services.refresh_schedule import next_refresh_at
from app.services.refresh_scheduler import claim_due_refreshes

T = datetime(2026, 11, 4, 12, 0, tzinfo=timezone.utc)
WEEK = timedelta(days=7)
HOUR = timedelta(hours=1)


def make_run(status, completed_at=None, started_at=None, created_at=None):
    return SimpleNamespace(
        status=status,
        completed_at=completed_at,
        started_at=started_at,
        created_at=created_at or T - WEEK,
    )


def test_refresh_policy_after_success_failure_and_active():
    success = make_run(ResearchStatus.COMPLETED, completed_at=T - WEEK)
    assert next_refresh_at([success], WEEK, HOUR) == T
    failure = make_run(ResearchStatus.FAILED, completed_at=T - timedelta(minutes=5))
    assert next_refresh_at([success, failure], WEEK, HOUR) == T + timedelta(minutes=55)
    assert next_refresh_at([failure], WEEK, HOUR) == T + timedelta(minutes=55)
    assert next_refresh_at([success, failure, make_run(ResearchStatus.PENDING)]) is None
    assert next_refresh_at([]) is None


def test_refresh_policy_handles_naive_postgres_timestamps():
    success = make_run(ResearchStatus.COMPLETED, completed_at=(T - WEEK).replace(tzinfo=None))
    assert next_refresh_at([success], WEEK, HOUR) == T


@compiles(JSONB, "sqlite")
def _jsonb_sqlite(_type, compiler, **kwargs):
    return "JSON"


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def complete(db, competitor_id, when):
    run = create_research_run(db, competitor_id)
    update_research_run_status(db, run.id, ResearchStatus.RUNNING)
    update_research_run_status(db, run.id, ResearchStatus.COMPLETED)
    run.completed_at = when
    db.commit()
    return run


def test_scheduler_claims_due_once_and_blocks_duplicate_manual_reruns(db):
    competitor = create_competitor(db, "Vanta", "https://vanta.com")
    complete(db, competitor.id, T - WEEK)
    ids = claim_due_refreshes(db, now=T, interval=WEEK, retry=HOUR)
    assert len(ids) == 1
    assert get_latest_research_run(db, competitor.id).id == ids[0]
    assert claim_due_refreshes(db, now=T, interval=WEEK, retry=HOUR) == []
    with pytest.raises(ResearchAlreadyActiveError):
        create_research_run(db, competitor.id)


def test_scheduler_retries_failures_after_backoff_preserving_completed_run(db):
    competitor = create_competitor(db, "Drata", "https://drata.com")
    original = complete(db, competitor.id, T - WEEK)
    ids = claim_due_refreshes(db, now=T, interval=WEEK, retry=HOUR)
    update_research_run_status(db, ids[0], ResearchStatus.FAILED, error_message="failed")
    failed = get_latest_research_run(db, competitor.id)
    failed.completed_at = T
    db.commit()
    assert claim_due_refreshes(db, now=T + timedelta(minutes=59), interval=WEEK, retry=HOUR) == []
    retried = claim_due_refreshes(db, now=T + HOUR, interval=WEEK, retry=HOUR)
    assert len(retried) == 1
    assert original.status == ResearchStatus.COMPLETED  # historic successful research is intact


def test_scheduler_recovers_stale_pending_then_backs_off(db):
    competitor = create_competitor(db, "Secureframe", "https://secureframe.com")
    complete(db, competitor.id, T - WEEK)
    ids = claim_due_refreshes(db, now=T, interval=WEEK, retry=HOUR)
    abandoned = get_latest_research_run(db, competitor.id)
    abandoned.created_at = T - timedelta(hours=2)
    db.commit()
    assert claim_due_refreshes(db, now=T, interval=WEEK, retry=HOUR) == []
    db.refresh(abandoned)
    assert abandoned.status == ResearchStatus.FAILED
    assert "interrupted" in abandoned.error_message
    assert claim_due_refreshes(db, now=T + HOUR, interval=WEEK, retry=HOUR)


def test_scheduler_does_not_schedule_an_initial_run_or_fresh_pending(db):
    competitor = create_competitor(db, "New", "https://new.example")
    assert claim_due_refreshes(db, now=T) == []
    create_research_run(db, competitor.id)
    assert claim_due_refreshes(db, now=T, stale_after=timedelta(days=999)) == []
