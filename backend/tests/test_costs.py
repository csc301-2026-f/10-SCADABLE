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
)
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
    assert cost["usd"] == pytest.approx(0.01)
    assert cost["complete"] is False


def test_record_run_cost_marks_unpriced_calls_incomplete(monkeypatch, session_factory, run_id):
    _fake_costs(monkeypatch, {
        f"run-{run_id}:vanta:extraction": CallCost(found=1, usd=0.01),
        f"run-{run_id}:vanta:analysis": CallCost(found=1, usd=None),
    })

    with session_factory() as db:
        asyncio.run(orchestrator.record_run_cost(db, run_id))

    cost = _cost(session_factory, run_id)
    assert cost["usd"] == pytest.approx(0.01)
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

        save_analysis_result(db, run_id, {**run.analysis_result, "cost": {"usd": 0.035}})
        assert serializers.agent_run(get_research_run(db, run_id), "Vanta").cost_usd == 0.035
