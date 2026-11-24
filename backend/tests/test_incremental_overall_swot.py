"""Incremental overview: reruns cannot rewrite unrelated competitors' claims."""

import asyncio
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import sessionmaker

from app.db.database import Base
from app.db import models
from app.db import repository as repo
from app.services import orchestrator
from app.services.crawler import MockCrawl4AICrawler
from app.services.llm import MockOpenAIAnalyzer


@compiles(JSONB, "sqlite")
def jsonb_sqlite(element, compiler, **kw):
    return "JSON"


@pytest.fixture
def db():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine, expire_on_commit=False)() as session:
        yield session
    engine.dispose()


class PerCompetitorAnalyzer(MockOpenAIAnalyzer):
    def __init__(self):
        super().__init__()
        self.comparisons = []

    async def compare(self, baseline_context, competitors, trace_prefix=None):
        self.comparisons.append(([c["company_name"] for c in competitors], baseline_context["summary"]))
        name = competitors[0]["company_name"]
        return {
            "swot_entries": [
                {"category": "strength", "content": f"SCADABLE relative to {name}", "source_url": None},
            ],
            "scores": [{"company_name": name, "feature": "Evidence", "score": 7}],
        }


def _run(db, company, analyzer, crawler=None):
    run = repo.create_research_run(db, company.id)
    result = asyncio.run(orchestrator.execute_research_run(
        db, run.id, crawler or MockCrawl4AICrawler(), analyzer
    ))
    assert result.status == models.ResearchStatus.COMPLETED
    return run


def _contents(db):
    return [row.content for row in repo.get_overall_swot(db)]


def test_new_competitor_only_compares_itself_and_preserves_prior_ids(db):
    analyzer = PerCompetitorAnalyzer()
    vanta = repo.create_competitor(db, "Vanta", "https://vanta.com")
    _run(db, vanta, analyzer)
    old = repo.get_overall_swot(db)[0]

    secureframe = repo.create_competitor(db, "Secureframe", "https://secureframe.com")
    _run(db, secureframe, analyzer)
    assert analyzer.comparisons == [(["Vanta"], analyzer.comparisons[0][1]),
                                    (["Secureframe"], analyzer.comparisons[0][1])]
    assert _contents(db) == ["SCADABLE relative to Vanta", "SCADABLE relative to Secureframe"]
    original = next(e for e in repo.get_overall_swot(db) if e.content == old.content)
    assert original.id == old.id
    assert original.created_at == old.created_at

    # An unchanged source should not lead to a new comparison.
    _run(db, secureframe, analyzer)
    assert len(analyzer.comparisons) == 2
    assert _contents(db) == ["SCADABLE relative to Vanta", "SCADABLE relative to Secureframe"]
    assert repo.is_overall_swot_current(db)


def test_changed_competitor_only_recomputes_own_snapshot(db):
    class ChangingCrawler(MockCrawl4AICrawler):
        async def crawl(self, name, website_url, research_run_id=None):
            data = await super().crawl(name, website_url, research_run_id=research_run_id)
            data["pages"][0]["text"] += " Fresh product update"
            return data

    analyzer = PerCompetitorAnalyzer()
    vanta = repo.create_competitor(db, "Vanta", "https://vanta.com")
    secureframe = repo.create_competitor(db, "Secureframe", "https://secureframe.com")
    _run(db, vanta, analyzer)
    _run(db, secureframe, analyzer)
    before = [(entry.id, entry.content) for entry in repo.get_overall_swot(db)]
    _run(db, secureframe, analyzer, crawler=ChangingCrawler())
    assert [names for names, _ in analyzer.comparisons] == [["Vanta"], ["Secureframe"], ["Secureframe"]]
    assert [(entry.id, entry.content) for entry in repo.get_overall_swot(db)] == before


def test_deletion_only_removes_own_findings(db):
    analyzer = PerCompetitorAnalyzer()
    vanta = repo.create_competitor(db, "Vanta", "https://vanta.com")
    secureframe = repo.create_competitor(db, "Secureframe", "https://secureframe.com")
    _run(db, vanta, analyzer)
    _run(db, secureframe, analyzer)
    repo.delete_competitor(db, secureframe.id)
    asyncio.run(orchestrator.refresh_overall_swot(db, analyzer))
    assert _contents(db) == ["SCADABLE relative to Vanta"]
    assert [names for names, _ in analyzer.comparisons] == [["Vanta"], ["Secureframe"]]
    assert repo.is_overall_swot_current(db)
    assert set(repo.get_overall_swot_contributions(db)) == {vanta.id}


def test_new_baseline_invalidates_and_recomputes_all(db, monkeypatch):
    analyzer = PerCompetitorAnalyzer()
    vanta = repo.create_competitor(db, "Vanta", "https://vanta.com")
    secureframe = repo.create_competitor(db, "Secureframe", "https://secureframe.com")
    _run(db, vanta, analyzer)
    _run(db, secureframe, analyzer)
    old = orchestrator.get_scadable_baseline(db)
    monkeypatch.setattr(orchestrator, "get_scadable_baseline", lambda db: {**old, "summary": "Updated GitHub profile"})
    repo.mark_overall_swot_stale(db)
    asyncio.run(orchestrator.refresh_overall_swot(db, analyzer))
    assert [names for names, _ in analyzer.comparisons][-2:] == [["Secureframe"], ["Vanta"]] or [names for names, _ in analyzer.comparisons][-2:] == [["Vanta"], ["Secureframe"]]
    assert all(summary == "Updated GitHub profile" for _, summary in analyzer.comparisons[-2:])
    assert repo.is_overall_swot_current(db)


def test_failed_comparison_retains_old_rows_but_marks_stale(db):
    class CompareError(PerCompetitorAnalyzer):
        async def compare(self, baseline_context, competitors, trace_prefix=None):
            raise RuntimeError("no LLM")

    analyzer = PerCompetitorAnalyzer()
    vanta = repo.create_competitor(db, "Vanta", "https://vanta.com")
    _run(db, vanta, analyzer)
    previous = _contents(db)
    current = repo.get_overall_swot(db)[0].id
    repo.mark_overall_swot_stale(db)
    old = orchestrator.get_scadable_baseline(db)
    old["summary"] = "changed baseline"
    original = orchestrator.get_scadable_baseline
    orchestrator.get_scadable_baseline = lambda db: old
    try:
        asyncio.run(orchestrator.refresh_overall_swot(db, CompareError()))
    finally:
        orchestrator.get_scadable_baseline = original
    assert _contents(db) == previous
    assert repo.get_overall_swot(db)[0].id == current
    assert not repo.is_overall_swot_current(db)


def test_inflight_comparison_cannot_publish_after_revision_changes(db):
    company = repo.create_competitor(db, "Vanta", "https://vanta.com")

    class ConcurrentUpdateAnalyzer(PerCompetitorAnalyzer):
        async def compare(self, baseline_context, competitors, trace_prefix=None):
            # Simulate a newer competitor or baseline change during the await.
            repo.mark_overall_swot_stale(db)
            return await super().compare(baseline_context, competitors, trace_prefix)

    analyzer = ConcurrentUpdateAnalyzer()
    _run(db, company, analyzer)
    assert repo.get_overall_swot(db) == []
    assert repo.get_overall_swot_contributions(db) == {}
    assert not repo.is_overall_swot_current(db)


def test_existing_legacy_overview_backfills_contributions_on_next_refresh(db):
    analyzer = PerCompetitorAnalyzer()
    names = ("Vanta", "Secureframe")
    for name in names:
        company = repo.create_competitor(db, name, f"https://{name.lower()}.com")
        run = repo.create_research_run(db, company.id)
        repo.save_analysis_result(db, run.id, {"summary": "ok", "features": ["Evidence"]})
        repo.update_research_run_status(db, run.id, models.ResearchStatus.RUNNING)
        repo.update_research_run_status(db, run.id, models.ResearchStatus.COMPLETED)

    repo.publish_overall_swot(db, [{
        "category": "strength", "content": "Legacy overview", "source_url": None,
    }], repo.get_overall_swot_revision(db))
    assert repo.is_overall_swot_current(db)
    assert repo.get_overall_swot_contributions(db) == {}

    # An actual change makes the global revision stale; the new version then
    # bootstraps both competitors without dropping the old overview mid-flight.
    repo.mark_overall_swot_stale(db)
    asyncio.run(orchestrator.refresh_overall_swot(db, analyzer))
    assert len(repo.get_overall_swot_contributions(db)) == 2
    assert sorted(_contents(db)) == sorted([f"SCADABLE relative to {name}" for name in names])
    assert repo.is_overall_swot_current(db)
    assert len(analyzer.comparisons) == 2


def test_overall_snapshot_preserves_multiple_citations_and_merges_duplicate_sources(db):
    from app.api.routes.swot import _citation_sources
    first = repo.create_competitor(db, "Vanta", "https://vanta.com")
    second = repo.create_competitor(db, "Scrut", "https://scrut.io")
    first_run = repo.create_research_run(db, first.id)
    second_run = repo.create_research_run(db, second.id)
    finding = "SCADABLE has developer-first compliance tooling"
    first_entries = [{
        "category": "strength", "content": finding,
        "source_url": "https://vanta.com/features",
        "source_urls": ["https://vanta.com/features", "https://vanta.com/platform"],
        "uses_scadable_baseline": True,
    }]
    second_entries = [{
        "category": "strength", "content": finding,
        "source_url": "https://scrut.io/platform",
        "source_urls": ["https://scrut.io/platform"],
        "uses_scadable_baseline": True,
    }]
    rev = repo.get_overall_swot_revision(db)
    combined = orchestrator._combine_contributions({
        first.id: first_entries, second.id: second_entries,
    })
    assert len(combined) == 1
    assert combined[0]["source_urls"] == [
        "https://vanta.com/features", "https://vanta.com/platform",
        "https://scrut.io/platform",
    ]
    assert repo.save_comparison_scores_if_current(
        db, rev, {}, combined, contribution_updates={
            first.id: {"research_run_id": first_run.id, "baseline_digest": "test", "entries": first_entries},
            second.id: {"research_run_id": second_run.id, "baseline_digest": "test", "entries": second_entries},
        },
    )
    first_snapshot = repo.get_overall_swot_contributions(db)[first.id]
    assert first_snapshot.entries[0]["source_urls"] == [
        "https://vanta.com/features", "https://vanta.com/platform",
    ]
    sources = _citation_sources(db)[("strength", finding.casefold())]
    assert sources[0]["kind"] == "baseline"
    assert [s["url"] for s in sources[1:]] == [
        "https://vanta.com/features", "https://vanta.com/platform", "https://scrut.io/platform",
    ]

def test_policy_change_recompares_cached_competitors_without_recrawling(db, monkeypatch):
    """Changing comparison logic makes old snapshots ineligible for reuse."""
    analyzer = PerCompetitorAnalyzer()
    vanta = repo.create_competitor(db, "Vanta", "https://vanta.com")
    scrut = repo.create_competitor(db, "Scrut", "https://scrut.io")
    _run(db, vanta, analyzer)
    _run(db, scrut, analyzer)
    initial = repo.get_overall_swot_contributions(db)
    digests = {key: item.baseline_digest for key, item in initial.items()}
    runs = {key: item.research_run_id for key, item in initial.items()}
    assert len(analyzer.comparisons) == 2

    monkeypatch.setattr(orchestrator, "COMPARISON_POLICY_VERSION", "new-comparison-rules")
    asyncio.run(orchestrator.refresh_overall_swot(db, analyzer))
    assert len(analyzer.comparisons) == 4
    updated = repo.get_overall_swot_contributions(db)
    assert all(item.baseline_digest != digests[key] for key, item in updated.items())
    assert {key: item.research_run_id for key, item in updated.items()} == runs
    assert repo.is_overall_swot_current(db)
    assert _contents(db) == ["SCADABLE relative to Vanta", "SCADABLE relative to Scrut"]

    asyncio.run(orchestrator.refresh_overall_swot(db, analyzer))
    assert len(analyzer.comparisons) == 4  # New snapshots are reused.


def test_only_if_missing_does_not_skip_comparison_policy_upgrade(db, monkeypatch):
    analyzer = PerCompetitorAnalyzer()
    vanta = repo.create_competitor(db, "Vanta", "https://vanta.com")
    _run(db, vanta, analyzer)
    assert repo.is_overall_swot_current(db)
    monkeypatch.setattr(orchestrator, "COMPARISON_POLICY_VERSION", "new-policy-v2")
    asyncio.run(orchestrator.refresh_overall_swot(db, analyzer, only_if_missing=True))
    assert len(analyzer.comparisons) == 2
    assert repo.is_overall_swot_current(db)
    asyncio.run(orchestrator.refresh_overall_swot(db, analyzer, only_if_missing=True))
    assert len(analyzer.comparisons) == 2


def test_distinct_supported_weaknesses_are_preserved_without_a_quota(db):
    """Fixture supplies two different supported gaps; neither is dropped."""
    class TwoWeaknessAnalyzer(PerCompetitorAnalyzer):
        async def compare(self, baseline_context, competitors, trace_prefix=None):
            name = competitors[0]["company_name"]
            self.comparisons.append(([name], baseline_context["summary"]))
            return {
                "swot_entries": [
                    {"category": "strength", "content": "Documented remediation guidance", "source_url": None},
                    {"category": "weakness", "content": "Verified framework coverage disadvantage", "source_url": None},
                    {"category": "weakness", "content": "Verified integration coverage disadvantage", "source_url": None},
                ],
                "scores": [],
            }

    analyzer = TwoWeaknessAnalyzer()
    company = repo.create_competitor(db, "Vanta", "https://vanta.com")
    _run(db, company, analyzer)
    entries = repo.get_overall_swot(db)
    weaknesses = [entry.content for entry in entries if entry.category == models.SWOTCategory.WEAKNESS]
    assert weaknesses == [
        "Verified framework coverage disadvantage",
        "Verified integration coverage disadvantage",
    ]
    saved = repo.get_overall_swot_contributions(db)[company.id].entries
    assert len([item for item in saved if item["category"] == "weakness"]) == 2
