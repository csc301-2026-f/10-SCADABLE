"""Cost recovery retries missing/incomplete telemetry across backend restarts."""

import asyncio
import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.db import models  # noqa: F401
from app.db import repository as repo
from app.services import cost_recovery


@compiles(JSONB, "sqlite")
def jsonb_sqlite(element, compiler, **kw):
    return "JSON"


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


def completed(session_factory, now, *, cost=None):
    with session_factory() as db:
        company = repo.create_competitor(db, "Vanta", "https://vanta.com")
        run = repo.create_research_run(db, company.id)
        repo.update_research_run_status(db, run.id, models.ResearchStatus.RUNNING)
        repo.update_research_run_status(db, run.id, models.ResearchStatus.COMPLETED)
        run.completed_at = now - timedelta(hours=1)
        run.analysis_result = {"llm_calls": [f"run-{run.id}:vanta:extraction"]}
        if cost is not None:
            run.analysis_result["cost"] = cost
        db.commit()
        return run.id


def test_missing_cost_recovery_is_claimed_after_delay_and_rate_limited(session_factory):
    now = datetime.now(timezone.utc)
    run_id = completed(session_factory, now)
    with session_factory() as db:
        assert cost_recovery.claim_missing_cost_runs(db, now=now) == [run_id]
        assert cost_recovery.claim_missing_cost_runs(db, now=now) == []
        run = repo.get_research_run(db, run_id)
        assert run.analysis_result["cost_recovery"]["attempts"] == 1
        assert cost_recovery.claim_missing_cost_runs(db, now=now + timedelta(minutes=15)) == [run_id]
        assert run.analysis_result["cost_recovery"]["attempts"] == 2


def test_complete_cost_never_recovered(session_factory):
    now = datetime.now(timezone.utc)
    completed(session_factory, now, cost={"complete": True, "usd": 0.01})
    with session_factory() as db:
        assert cost_recovery.claim_missing_cost_runs(db, now=now) == []


def test_recent_run_waits_for_original_cost_lookup(session_factory):
    now = datetime.now(timezone.utc)
    run_id = completed(session_factory, now)
    with session_factory() as db:
        run = repo.get_research_run(db, run_id)
        run.completed_at = now - timedelta(minutes=5)
        db.commit()
        assert cost_recovery.claim_missing_cost_runs(db, now=now) == []


def test_max_attempts_are_bounded(session_factory):
    now = datetime.now(timezone.utc)
    run_id = completed(session_factory, now)
    with session_factory() as db:
        run = repo.get_research_run(db, run_id)
        run.analysis_result = {**run.analysis_result,
            "cost_recovery": {"attempts": cost_recovery.MAX_RECOVERY_ATTEMPTS,
                              "last_attempt_at": (now - timedelta(days=1)).isoformat()}}
        db.commit()
        assert cost_recovery.claim_missing_cost_runs(db, now=now) == []


def test_recovery_runs_original_cost_function_once(monkeypatch, session_factory):
    now = datetime.now(timezone.utc)
    run_id = completed(session_factory, now, cost={"complete": False, "usd": None})
    calls = []

    async def fake_record(db, selected, final):
        calls.append((selected, final))
        run = repo.get_research_run(db, selected)
        repo.save_analysis_result(db, selected, {**run.analysis_result,
                                                "cost": {"complete": True, "usd": 0.009}})
        return True

    monkeypatch.setattr(cost_recovery, "is_cost_tracking_configured", lambda: True)
    monkeypatch.setattr(cost_recovery, "record_run_cost", fake_record)
    assert asyncio.run(cost_recovery.recover_missing_costs_once(session_factory, now=now)) == [run_id]
    assert calls == [(run_id, True)]
    assert asyncio.run(cost_recovery.recover_missing_costs_once(session_factory, now=now)) == []
