"""Tests for looking up research run costs in Langfuse (Langfuse is faked; no network)."""

import asyncio
import os
from types import SimpleNamespace

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.api import serializers
from app.db.database import Base
from app.db.repository import (
    create_competitor,
    create_research_run,
    delete_competitor,
    get_research_run,
    save_analysis_result,
    update_research_run_status,
)
from app.db.models import ResearchStatus
from app.services import costs, orchestrator
from app.services.costs import (
    CallCost,
    _cost_for_name,
    company_slug,
    run_call_names,
)
from app.services.crawler import MockCrawl4AICrawler
from app.services.llm import MockOpenAIAnalyzer


@pytest.fixture
def session_factory():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


@pytest.fixture
def run_id(session_factory):
    with session_factory() as db:
        competitor = create_competitor(db, name="Vanta", website_url="https://vanta.com")
        run = create_research_run(db, competitor.id)
        save_analysis_result(db, run.id, {
            "summary": "s",
            "scores": [{"feature": "Evidence", "score": 7}],
            "llm_calls": [f"run-{run.id}:vanta:extraction", f"run-{run.id}:vanta:analysis"],
        })
        return run.id


def _fake_costs(monkeypatch, by_name):
    async def fetch(names):
        return {name: by_name.get(name, CallCost(found=0, usd=0.0)) for name in names}

    monkeypatch.setattr(orchestrator, "fetch_call_costs", fetch)

    async def no_decisions(run_id, reference, *, expected_requests, started_at):
        return costs.DecisionsCost(found=0, usd=0.0, complete=True, expected=0)

    monkeypatch.setattr(orchestrator, "fetch_decisions_cost", no_decisions)


def _cost(session_factory, run_id):
    with session_factory() as db:
        return get_research_run(db, run_id).analysis_result.get("cost")


# --- Call names ----------------------------------------------------------------

@pytest.mark.parametrize("name, slug", [
    ("Vanta", "vanta"),
    ("vanta.com", "vanta-com"),
    ("  Vanta Inc. ", "vanta-inc"),
    ("Drata/SOC-2", "drata-soc-2"),
    ("!!!", "company"),
])
def test_company_slug(name, slug):
    assert company_slug(name) == slug


def test_run_call_names():
    assert run_call_names(42, "Vanta") == [
        "run-42:vanta:extraction",
        "run-42:vanta:analysis",
        "run-42:comparison",
    ]


class PrefixRecordingAnalyzer(MockOpenAIAnalyzer):
    def __init__(self):
        super().__init__()
        self.prefixes = []

    async def analyze(self, scraped_data, baseline_context, trace_prefix=None):
        self.prefixes.append(("analyze", trace_prefix))
        return await super().analyze(scraped_data, baseline_context)

    async def compare(self, baseline_context, competitors, trace_prefix=None):
        self.prefixes.append(("compare", trace_prefix))
        return await super().compare(baseline_context, competitors)


def test_run_names_its_calls_by_run_and_company(session_factory):
    analyzer = PrefixRecordingAnalyzer()
    with session_factory() as db:
        competitor = create_competitor(db, name="Vanta Inc.", website_url="https://vanta.com")
        run = create_research_run(db, competitor.id)
        asyncio.run(orchestrator.execute_research_run(db, run.id, MockCrawl4AICrawler(), analyzer))

    assert analyzer.prefixes == [
        ("analyze", f"run-{run.id}:vanta-inc"),
        ("compare", f"run-{run.id}"),
    ]


# --- Reading Langfuse --------------------------------------------------------

class FakeObservations:
    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    async def get_many(self, **kwargs):
        self.calls.append(kwargs)
        return self.pages[len(self.calls) - 1]


def _page(observations, cursor=None):
    return SimpleNamespace(
        data=[SimpleNamespace(name=name, total_cost=cost) for name, cost in observations],
        meta=SimpleNamespace(cursor=cursor),
    )


def test_cost_for_name_sums_every_page_and_ignores_other_names():
    observations = FakeObservations([
        _page([("run-1:analysis", 0.002), ("run-10:analysis", 5.0)], cursor="next"),
        _page([("run-1:analysis", 0.001)]),
    ])
    api = SimpleNamespace(observations=observations)

    cost = asyncio.run(_cost_for_name(api, "run-1:analysis"))

    assert cost == CallCost(found=2, usd=pytest.approx(0.003))
    assert observations.calls[0]["name"] == "run-1:analysis"
    assert observations.calls[1]["cursor"] == "next"


def test_cost_for_name_is_unknown_when_a_call_has_no_price():
    observations = FakeObservations([_page([("run-1:analysis", 0.002), ("run-1:analysis", None)])])
    api = SimpleNamespace(observations=observations)

    assert asyncio.run(_cost_for_name(api, "run-1:analysis")) == CallCost(found=2, usd=None)


def test_fetch_call_costs_is_none_without_langfuse_keys(monkeypatch):
    monkeypatch.setattr(costs, "_langfuse_settings", lambda: None)

    assert asyncio.run(costs.fetch_call_costs(run_call_names(1, "Vanta"))) is None


def test_fetch_call_costs_keeps_the_shared_langfuse_recorder_running(monkeypatch):
    langfuse = pytest.importorskip("langfuse")
    langfuse_api = pytest.importorskip("langfuse.api")
    from langfuse._client.resource_manager import LangfuseResourceManager

    settings = SimpleNamespace(
        langfuse_public_key="pk-lf-test",
        langfuse_secret_key=SimpleNamespace(get_secret_value=lambda: "sk-lf-test"),
        langfuse_base_url="http://127.0.0.1:9",
    )
    monkeypatch.setattr(costs, "_langfuse_settings", lambda: settings)
    pages = FakeObservations([_page([("run-1:vanta:extraction", 0.01)])] + [_page([])] * 2)
    monkeypatch.setattr(
        langfuse_api, "AsyncLangfuseAPI", lambda **kwargs: SimpleNamespace(observations=pages)
    )

    # The recorder the analyzer's OpenAIClient uses for this key
    recorder = langfuse.Langfuse(
        public_key="pk-lf-test", secret_key="sk-lf-test", base_url="http://127.0.0.1:9"
    )
    try:
        result = asyncio.run(costs.fetch_call_costs(run_call_names(1, "Vanta")))

        assert result["run-1:vanta:extraction"] == CallCost(found=1, usd=0.01)
        assert not LangfuseResourceManager._instances["pk-lf-test"]._shutdown
    finally:
        recorder.shutdown()
        LangfuseResourceManager._instances.pop("pk-lf-test", None)


# --- Saving a run's cost -----------------------------------------------------

def test_record_run_cost_saves_total_and_keeps_other_fields(monkeypatch, session_factory, run_id):
    _fake_costs(monkeypatch, {
        f"run-{run_id}:vanta:extraction": CallCost(found=1, usd=0.01),
        f"run-{run_id}:vanta:analysis": CallCost(found=1, usd=0.02),
        f"run-{run_id}:comparison": CallCost(found=1, usd=0.005),
    })

    with session_factory() as db:
        assert asyncio.run(orchestrator.record_run_cost(db, run_id))

    cost = _cost(session_factory, run_id)
    assert cost["usd"] == pytest.approx(0.035)
    assert cost["complete"] is True
    with session_factory() as db:
        assert get_research_run(db, run_id).analysis_result["scores"]  # not overwritten


def test_record_run_cost_waits_for_missing_calls_before_the_final_attempt(
    monkeypatch, session_factory, run_id
):
    # Analysis isn't ingested yet
    _fake_costs(monkeypatch, {f"run-{run_id}:vanta:extraction": CallCost(found=1, usd=0.01)})

    with session_factory() as db:
        assert not asyncio.run(orchestrator.record_run_cost(db, run_id, final=False))
    assert _cost(session_factory, run_id) is None

    with session_factory() as db:
        assert asyncio.run(orchestrator.record_run_cost(db, run_id, final=True))
    cost = _cost(session_factory, run_id)
    assert cost["usd"] is None
    assert cost["analysis_usd"] == pytest.approx(0.01)
    assert cost["complete"] is False


def test_record_run_cost_marks_unpriced_calls_incomplete(monkeypatch, session_factory, run_id):
    _fake_costs(monkeypatch, {
        f"run-{run_id}:vanta:extraction": CallCost(found=1, usd=0.01),
        f"run-{run_id}:vanta:analysis": CallCost(found=1, usd=None),
    })

    with session_factory() as db:
        asyncio.run(orchestrator.record_run_cost(db, run_id))

    cost = _cost(session_factory, run_id)
    assert cost["usd"] is None
    assert cost["analysis_usd"] == pytest.approx(0.01)
    assert cost["complete"] is False


def test_record_run_cost_with_no_calls_saves_zero(monkeypatch, session_factory):
    # A mock or unchanged run makes no paid calls
    with session_factory() as db:
        competitor = create_competitor(db, name="Drata", website_url="https://drata.com")
        run = create_research_run(db, competitor.id)
        save_analysis_result(db, run.id, {"skipped_llm": True})
        run_id = run.id
    _fake_costs(monkeypatch, {})

    with session_factory() as db:
        assert asyncio.run(orchestrator.record_run_cost(db, run_id, final=False))

    cost = _cost(session_factory, run_id)
    assert cost["usd"] == 0.0
    assert cost["calls"] == {}
    assert cost["complete"] is True


def test_record_run_cost_skips_deleted_runs(monkeypatch, session_factory, run_id):
    _fake_costs(monkeypatch, {})
    with session_factory() as db:
        delete_competitor(db, get_research_run(db, run_id).competitor_id)

    with session_factory() as db:
        assert asyncio.run(orchestrator.record_run_cost(db, run_id))


def test_record_run_cost_does_nothing_without_langfuse(monkeypatch, session_factory, run_id):
    async def not_configured(names):
        return None

    monkeypatch.setattr(orchestrator, "fetch_call_costs", not_configured)

    with session_factory() as db:
        assert asyncio.run(orchestrator.record_run_cost(db, run_id))
    assert _cost(session_factory, run_id) is None


# --- Scheduling ----------------------------------------------------------------

def test_cost_lookup_is_skipped_without_langfuse(monkeypatch, session_factory, run_id):
    monkeypatch.setattr(orchestrator, "is_cost_tracking_configured", lambda: False)

    async def fail_if_called(*args, **kwargs):
        raise AssertionError("should not wait or look anything up")

    monkeypatch.setattr(orchestrator.asyncio, "sleep", fail_if_called)

    asyncio.run(orchestrator._record_cost_later(session_factory, run_id))


def test_cost_lookup_retries_once_then_stops(monkeypatch, session_factory, run_id):
    monkeypatch.setattr(orchestrator, "is_cost_tracking_configured", lambda: True)
    monkeypatch.setattr(orchestrator, "COST_LOOKUP_DELAY_SECONDS", 0)
    attempts = []

    async def record(db, target_run_id, final):
        attempts.append(final)
        return final

    monkeypatch.setattr(orchestrator, "record_run_cost", record)

    asyncio.run(orchestrator._record_cost_later(session_factory, run_id))

    assert attempts == [False, True]


def test_cost_lookup_errors_never_raise(monkeypatch, session_factory, run_id):
    monkeypatch.setattr(orchestrator, "is_cost_tracking_configured", lambda: True)
    monkeypatch.setattr(orchestrator, "COST_LOOKUP_DELAY_SECONDS", 0)

    async def broken(db, target_run_id, final):
        raise RuntimeError("Langfuse is down")

    monkeypatch.setattr(orchestrator, "record_run_cost", broken)

    asyncio.run(orchestrator._record_cost_later(session_factory, run_id))


# --- API -------------------------------------------------------------------------

def test_agent_run_reports_saved_cost(session_factory, run_id):
    with session_factory() as db:
        run = get_research_run(db, run_id)
        assert serializers.agent_run(run, "Vanta").cost_usd is None

        save_analysis_result(db, run_id, {**run.analysis_result, "cost": {"usd": 0.035, "complete": True}})
        assert serializers.agent_run(get_research_run(db, run_id), "Vanta").cost_usd == 0.035


def test_saved_cost_does_not_mark_a_failed_crawl_as_analyzed(monkeypatch, session_factory):
    # A failed crawl saves no analysis; the cost lookup later saves only "cost"
    with session_factory() as db:
        competitor = create_competitor(db, name="Drata", website_url="https://drata.com")
        run_id = create_research_run(db, competitor.id).id
        update_research_run_status(
            db, run_id, ResearchStatus.FAILED, error_message="Could not crawl the competitor's website."
        )
    _fake_costs(monkeypatch, {})

    with session_factory() as db:
        assert asyncio.run(orchestrator.record_run_cost(db, run_id))
        run = get_research_run(db, run_id)
        assert run.analysis_result == {"cost": run.analysis_result["cost"]}

        api_run = serializers.agent_run(run, "Drata")
    assert api_run.cost_usd == 0.0  # Fake cost lookup explicitly reports zero Decisions calls.
    assert [step.status for step in api_run.steps] == ["failed", "pending", "pending"]
    assert api_run.cache_hits is None
    assert api_run.cache_misses is None


def test_record_run_cost_adds_decisions_to_analysis_cost(monkeypatch, session_factory, run_id):
    with session_factory() as db:
        run = get_research_run(db, run_id)
        from app.db.repository import save_scraped_data
        save_scraped_data(db, run_id, {
            "mode": "real",
            "pages": [{"url": "https://vanta.com", "text": "Evidence"}],
            "decision_usage": {"request_count": 2, "estimated_cost_usd": 99.0},
            "decision_tracking": {"trace_id": "a"*32, "observation_id": "b"*16},
        })
    _fake_costs(monkeypatch, {
        f"run-{run_id}:vanta:extraction": CallCost(found=1, usd=0.01),
        f"run-{run_id}:vanta:analysis": CallCost(found=1, usd=0.02),
    })

    async def decisions(target_run, reference, *, expected_requests, started_at):
        assert target_run == run_id
        assert expected_requests == 2
        return costs.DecisionsCost(found=2, usd=0.003, complete=True, expected=2)
    monkeypatch.setattr(orchestrator, "fetch_decisions_cost", decisions)
    with session_factory() as db:
        assert asyncio.run(orchestrator.record_run_cost(db, run_id))
    record = _cost(session_factory, run_id)
    assert record["usd"] == pytest.approx(0.033)
    assert record["analysis_usd"] == pytest.approx(0.03)
    assert record["decisions_usd"] == pytest.approx(0.003)
    assert record["complete"] is True
    # The budget reservation is never added to measured Langfuse costs.
    assert record["usd"] != 99.033


def test_record_run_cost_incomplete_decisions_is_not_measured_zero(
    monkeypatch, session_factory, run_id
):
    _fake_costs(monkeypatch, {
        f"run-{run_id}:vanta:extraction": CallCost(found=1, usd=0.01),
        f"run-{run_id}:vanta:analysis": CallCost(found=1, usd=0.02),
    })
    async def incomplete(*args, **kwargs):
        return costs.DecisionsCost(found=0, usd=0.0, complete=False, expected=2)
    monkeypatch.setattr(orchestrator, "fetch_decisions_cost", incomplete)
    with session_factory() as db:
        assert asyncio.run(orchestrator.record_run_cost(db, run_id, final=False)) is False
    assert _cost(session_factory, run_id) is None
    with session_factory() as db:
        assert asyncio.run(orchestrator.record_run_cost(db, run_id, final=True)) is True
    record = _cost(session_factory, run_id)
    assert record["usd"] is None
    assert record["complete"] is False
    assert record["analysis_usd"] == 0.03


def test_unchanged_analysis_can_still_have_scraper_decisions_costs(
    monkeypatch, session_factory
):
    with session_factory() as db:
        competitor = create_competitor(db, name="Scrut", website_url="https://scrut.io")
        run_id = create_research_run(db, competitor.id).id
        from app.db.repository import save_scraped_data
        save_scraped_data(db, run_id, {
            "mode": "real", "pages": [],
            "decision_usage": {"request_count": 1},
        })
        save_analysis_result(db, run_id, {"skipped_llm": True})
    _fake_costs(monkeypatch, {})

    async def paid_decisions(*args, **kwargs):
        return costs.DecisionsCost(found=1, usd=0.0008, complete=True, expected=1)
    monkeypatch.setattr(orchestrator, "fetch_decisions_cost", paid_decisions)
    with session_factory() as db:
        assert asyncio.run(orchestrator.record_run_cost(db, run_id))
    item = _cost(session_factory, run_id)
    assert item["usd"] == pytest.approx(0.0008)
    assert item["analysis_usd"] == 0.0
    assert item["decisions_usd"] == pytest.approx(0.0008)
    assert item["complete"] is True


def test_failed_crawl_without_scraper_telemetry_keeps_cost_unavailable(
    monkeypatch, session_factory
):
    with session_factory() as db:
        competitor = create_competitor(db, name="Foresite", website_url="https://foresite.com")
        run_id = create_research_run(db, competitor.id).id
        update_research_run_status(db, run_id, ResearchStatus.FAILED)
    _fake_costs(monkeypatch, {})
    async def unknown(*args, **kwargs):
        return costs.DecisionsCost(found=0, usd=0.0, complete=False, expected=None)
    monkeypatch.setattr(orchestrator, "fetch_decisions_cost", unknown)
    with session_factory() as db:
        assert not asyncio.run(orchestrator.record_run_cost(db, run_id, final=False))
    with session_factory() as db:
        assert asyncio.run(orchestrator.record_run_cost(db, run_id, final=True))
        run = get_research_run(db, run_id)
        assert run.analysis_result["cost"]["usd"] is None
        assert serializers.agent_run(run, "Foresite").cost_usd is None
