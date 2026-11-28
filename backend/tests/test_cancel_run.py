"""Tests for cancelling a research run. Fake crawler and AI only, so no tokens are spent."""

import asyncio
import os
from datetime import datetime, timedelta, timezone

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db.database import Base, get_db
from app.db.models import ResearchRun, ResearchStatus
from app.db.repository import (
    InvalidStatusTransitionError,
    create_competitor,
    create_research_run,
    get_latest_swot,
    get_research_run,
    update_research_run_status,
)
from app.main import create_app
from app.services import cost_recovery, orchestrator
from app.services.crawler import MockCrawl4AICrawler
from app.services.llm import MockOpenAIAnalyzer
from app.services.refresh_schedule import _as_utc, next_refresh_at


@pytest.fixture
def session_factory(tmp_path):
    # A file, so the API test client's thread and the test share one database
    engine = create_engine(
        f"sqlite:///{tmp_path / 'runs.db'}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


@pytest.fixture
def competitor_id(session_factory):
    with session_factory() as db:
        return create_competitor(db, name="Vanta", website_url="https://vanta.com").id


class BlockingCrawler(MockCrawl4AICrawler):
    """Keeps crawling until cancelled, so a test can stop the run mid crawl."""

    def __init__(self):
        super().__init__()
        self.started = asyncio.Event()
        self.stopped = False

    async def crawl(self, competitor_name, website_url, *, research_run_id=None):
        self.started.set()
        try:
            await asyncio.Event().wait()
        finally:
            self.stopped = True


class ChangedCrawler(MockCrawl4AICrawler):
    """Returns different page text, so the run doesn't skip the AI call."""

    async def crawl(self, competitor_name, website_url, *, research_run_id=None):
        data = await super().crawl(competitor_name, website_url, research_run_id=research_run_id)
        data["pages"][0]["text"] += " Now with a new reporting feature."
        return data


class BlockingAnalyzer(MockOpenAIAnalyzer):
    """Waits in the AI call until cancelled."""

    def __init__(self):
        super().__init__()
        self.started = asyncio.Event()

    async def analyze(self, scraped_data, baseline_context, trace_prefix=None, session_id=None):
        self.started.set()
        await asyncio.Event().wait()


def _cancel(session_factory, run_id):
    """What the cancel endpoint does: save the status, then stop the job."""
    with session_factory() as db:
        update_research_run_status(db, run_id, ResearchStatus.CANCELLED)
    return orchestrator.request_run_cancel(run_id)


# --- Stopping the job ---------------------------------------------------------

def test_cancel_stops_the_run_during_the_crawl(session_factory, competitor_id):
    crawler = BlockingCrawler()

    async def scenario():
        with session_factory() as db:
            run_id = create_research_run(db, competitor_id).id
            task = asyncio.create_task(
                orchestrator.execute_research_run(db, run_id, crawler, MockOpenAIAnalyzer())
            )
            await crawler.started.wait()
            assert _cancel(session_factory, run_id) is True
            return run_id, await task

    run_id, result = asyncio.run(scenario())

    assert result.status == ResearchStatus.CANCELLED
    assert crawler.stopped
    assert run_id not in orchestrator._active_runs
    with session_factory() as db:
        run = get_research_run(db, run_id)
        assert run.status == ResearchStatus.CANCELLED
        assert run.completed_at is not None
        assert run.scraped_data is None


def test_cancel_during_the_ai_call_keeps_earlier_results(session_factory, competitor_id):
    with session_factory() as db:
        first = create_research_run(db, competitor_id)
        asyncio.run(orchestrator.execute_research_run(
            db, first.id, MockCrawl4AICrawler(), MockOpenAIAnalyzer()
        ))
        earlier = [entry.content for entry in get_latest_swot(db, competitor_id)]
    assert earlier

    analyzer = BlockingAnalyzer()

    async def scenario():
        with session_factory() as db:
            run_id = create_research_run(db, competitor_id).id
            task = asyncio.create_task(
                orchestrator.execute_research_run(db, run_id, ChangedCrawler(), analyzer)
            )
            await analyzer.started.wait()
            _cancel(session_factory, run_id)
            return run_id, await task

    run_id, result = asyncio.run(scenario())

    assert result.status == ResearchStatus.CANCELLED
    with session_factory() as db:
        run = get_research_run(db, run_id)
        assert run.scraped_data is not None  # The crawl finished before the cancel
        assert run.analysis_result is None
        assert [entry.content for entry in get_latest_swot(db, competitor_id)] == earlier


def test_a_cancel_saved_while_a_step_finishes_is_not_overwritten(session_factory, competitor_id):
    with session_factory() as db:
        run_id = create_research_run(db, competitor_id).id

    class CancelledDuringAnalysis(MockOpenAIAnalyzer):
        # The cancel lands after the last pause, so only the saved status can stop the run
        async def analyze(self, scraped_data, baseline_context, trace_prefix=None, session_id=None):
            with session_factory() as other:
                update_research_run_status(other, run_id, ResearchStatus.CANCELLED)
            return await super().analyze(scraped_data, baseline_context)

    with session_factory() as db:
        result = asyncio.run(orchestrator.execute_research_run(
            db, run_id, MockCrawl4AICrawler(), CancelledDuringAnalysis()
        ))

    assert result.status == ResearchStatus.CANCELLED
    with session_factory() as db:
        assert get_research_run(db, run_id).status == ResearchStatus.CANCELLED
        assert get_latest_swot(db, competitor_id) == []


def test_a_status_update_sees_a_cancel_saved_by_another_session(session_factory, competitor_id):
    with session_factory() as worker:
        # Held for the whole job, like the pipeline does, so the session keeps its copy
        run = create_research_run(worker, competitor_id)
        run_id = run.id
        update_research_run_status(worker, run_id, ResearchStatus.RUNNING)
        with session_factory() as other:
            update_research_run_status(other, run_id, ResearchStatus.CANCELLED)

        # The worker's copy still says "running"
        assert run.status == ResearchStatus.RUNNING
        with pytest.raises(InvalidStatusTransitionError):
            update_research_run_status(worker, run_id, ResearchStatus.COMPLETED)

    with session_factory() as db:
        assert get_research_run(db, run_id).status == ResearchStatus.CANCELLED


def test_a_run_cancelled_before_it_starts_never_crawls(session_factory, competitor_id):
    crawler = BlockingCrawler()
    with session_factory() as db:
        run_id = create_research_run(db, competitor_id).id
    assert _cancel(session_factory, run_id) is False  # No job yet

    with session_factory() as db:
        result = asyncio.run(
            orchestrator.execute_research_run(db, run_id, crawler, MockOpenAIAnalyzer())
        )

    assert result.status == ResearchStatus.CANCELLED
    assert not crawler.started.is_set()


def test_a_server_shutdown_is_not_treated_as_a_cancel(session_factory, competitor_id):
    crawler = BlockingCrawler()

    async def scenario():
        with session_factory() as db:
            run_id = create_research_run(db, competitor_id).id
            task = asyncio.create_task(
                orchestrator.execute_research_run(db, run_id, crawler, MockOpenAIAnalyzer())
            )
            await crawler.started.wait()
            task.cancel()  # Shutdown stops the job without marking the run cancelled
            with pytest.raises(asyncio.CancelledError):
                await task
            return run_id

    run_id = asyncio.run(scenario())

    assert run_id not in orchestrator._active_runs
    with session_factory() as db:
        # Left as running, so the scheduler's stale run check still retries it
        assert get_research_run(db, run_id).status == ResearchStatus.RUNNING


def test_cost_is_still_recorded_after_a_cancel(monkeypatch, session_factory, competitor_id):
    recorded = []

    async def record_cost_later(_session_factory, run_id):
        recorded.append(run_id)

    monkeypatch.setattr(orchestrator, "_record_cost_later", record_cost_later)

    class FakeBackgroundTasks:
        def __init__(self):
            self.tasks = []

        def add_task(self, func, *args):
            self.tasks.append((func, args))

    background = FakeBackgroundTasks()
    crawler = BlockingCrawler()
    with session_factory() as db:
        run = orchestrator.trigger_research_task(
            competitor_id, background, db,
            session_factory=session_factory, crawler=crawler, analyzer=MockOpenAIAnalyzer(),
        )

    async def scenario():
        func, args = background.tasks[0]
        task = asyncio.create_task(func(*args))
        await crawler.started.wait()
        _cancel(session_factory, run.id)
        await task

    asyncio.run(scenario())

    assert recorded == [run.id]


# --- The cancel endpoint --------------------------------------------------------

BRIDGE_TOKEN = "test-bridge-token"
AUTH = {"X-Scadable-Research-Bridge-Token": BRIDGE_TOKEN}


@pytest.fixture
def client(monkeypatch, session_factory):
    monkeypatch.setenv("ACCOUNT_RESEARCH_BRIDGE_TOKEN", BRIDGE_TOKEN)
    app = create_app()

    def session():
        with session_factory() as db:
            yield db

    app.dependency_overrides[get_db] = session
    return TestClient(app)


def test_cancel_endpoint_cancels_a_queued_run(client, session_factory, competitor_id):
    with session_factory() as db:
        run_id = create_research_run(db, competitor_id).id

    response = client.post(f"/runs/{run_id}/cancel", headers=AUTH)

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "cancelled"
    assert body["finishedAt"] is not None
    assert body["steps"][0] == {
        "name": "Crawl competitor website", "result": "Cancelled", "status": "cancelled",
    }
    with session_factory() as db:
        assert get_research_run(db, run_id).status == ResearchStatus.CANCELLED


def test_cancel_endpoint_clears_a_run_stuck_as_running(client, session_factory, competitor_id):
    # No job is running it, as after a server restart
    with session_factory() as db:
        run_id = create_research_run(db, competitor_id).id
        update_research_run_status(db, run_id, ResearchStatus.RUNNING)

    response = client.post(f"/runs/{run_id}/cancel", headers=AUTH)

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"


@pytest.mark.parametrize(
    "final", [ResearchStatus.COMPLETED, ResearchStatus.FAILED, ResearchStatus.CANCELLED]
)
def test_cancel_endpoint_rejects_a_finished_run(client, session_factory, competitor_id, final):
    with session_factory() as db:
        run_id = create_research_run(db, competitor_id).id
        update_research_run_status(db, run_id, ResearchStatus.RUNNING)
        update_research_run_status(db, run_id, final)

    response = client.post(f"/runs/{run_id}/cancel", headers=AUTH)

    assert response.status_code == 409
    assert response.json() == {"detail": f"Research run {run_id} already finished."}
    with session_factory() as db:
        assert get_research_run(db, run_id).status == final


def test_cancel_endpoint_unknown_run(client):
    response = client.post("/runs/999/cancel", headers=AUTH)

    assert response.status_code == 404


@pytest.mark.parametrize("headers", [
    {},  # A request sent straight to the backend, without the app server
    {"X-Scadable-Research-Bridge-Token": "wrong-token"},
])
def test_cancel_endpoint_rejects_requests_without_the_bridge_token(
    client, session_factory, competitor_id, headers
):
    with session_factory() as db:
        run_id = create_research_run(db, competitor_id).id

    response = client.post(f"/runs/{run_id}/cancel", headers=headers)

    assert response.status_code == 401
    with session_factory() as db:
        assert get_research_run(db, run_id).status == ResearchStatus.PENDING


def test_cancel_endpoint_is_off_when_the_bridge_token_is_not_set(
    monkeypatch, client, session_factory, competitor_id
):
    monkeypatch.delenv("ACCOUNT_RESEARCH_BRIDGE_TOKEN")
    with session_factory() as db:
        run_id = create_research_run(db, competitor_id).id

    response = client.post(f"/runs/{run_id}/cancel", headers=AUTH)

    assert response.status_code == 503
    with session_factory() as db:
        assert get_research_run(db, run_id).status == ResearchStatus.PENDING


# --- After a cancel ---------------------------------------------------------------

def test_a_cancelled_run_allows_a_new_run_and_keeps_the_refresh_date(
    session_factory, competitor_id
):
    with session_factory() as db:
        done = create_research_run(db, competitor_id)
        update_research_run_status(db, done.id, ResearchStatus.RUNNING)
        update_research_run_status(db, done.id, ResearchStatus.COMPLETED)
        cancelled = create_research_run(db, competitor_id)
        update_research_run_status(db, cancelled.id, ResearchStatus.CANCELLED)

        runs = list(db.scalars(
            select(ResearchRun).where(ResearchRun.competitor_id == competitor_id)
        ).all())
        week = timedelta(days=7)
        # Auto refresh still counts from the last success, not from the cancel
        assert next_refresh_at(runs, interval=week, retry=timedelta(hours=1)) == (
            _as_utc(done.completed_at) + week
        )
        # A cancelled run is not active, so a new run can start right away
        assert create_research_run(db, competitor_id).status == ResearchStatus.PENDING


def test_cost_recovery_includes_cancelled_runs(session_factory, competitor_id):
    now = datetime.now(timezone.utc)
    with session_factory() as db:
        run = create_research_run(db, competitor_id)
        update_research_run_status(db, run.id, ResearchStatus.CANCELLED)
        run.completed_at = now - timedelta(hours=1)
        db.commit()

        assert cost_recovery.claim_missing_cost_runs(db, now=now) == [run.id]
