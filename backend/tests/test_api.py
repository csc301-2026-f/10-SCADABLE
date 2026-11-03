"""API endpoint tests using an in-memory fake repository (no database needed)."""

import os

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://user:pass@localhost:1/unused")

from datetime import datetime, timedelta, timezone  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.api.routes import runs as runs_routes  # noqa: E402
from app.db import repository as repo  # noqa: E402
from app.db.database import get_db  # noqa: E402
from app.db.models import ResearchStatus, SWOTCategory  # noqa: E402
from app.main import create_app  # noqa: E402
from app.services import orchestrator  # noqa: E402

T0 = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)


class FakeStore:
    """Minimal stand-in for the repository layer."""

    def __init__(self) -> None:
        self.competitors: dict[int, SimpleNamespace] = {}
        self.runs: dict[int, SimpleNamespace] = {}
        self.entries: dict[int, list[SimpleNamespace]] = {}  # run_id -> entries
        self.overall: list[SimpleNamespace] = []
        self.overall_current = False
        self.triggered: list[int] = []
        self.overall_refreshes = 0

    def add_competitor(self, name: str, url: str) -> SimpleNamespace:
        cid = len(self.competitors) + 1
        competitor = SimpleNamespace(
            id=cid, name=name, website_url=url, research_runs=[], created_at=T0
        )
        self.competitors[cid] = competitor
        return competitor

    def add_run(self, competitor, status, analysis=None, scraped=None, error=None):
        rid = len(self.runs) + 1
        run = SimpleNamespace(
            id=rid,
            competitor_id=competitor.id,
            competitor=competitor,
            status=status,
            created_at=T0 + timedelta(minutes=rid),
            started_at=T0 + timedelta(minutes=rid),
            completed_at=(
                T0 + timedelta(minutes=rid, seconds=30)
                if status in (ResearchStatus.COMPLETED, ResearchStatus.FAILED)
                else None
            ),
            error_message=error,
            scraped_data=scraped,
            analysis_result=analysis,
        )
        self.runs[rid] = run
        competitor.research_runs.append(run)
        return run

    def add_entry(self, run, category, content, url):
        entry = SimpleNamespace(
            id=sum(len(v) for v in self.entries.values()) + 1,
            category=SWOTCategory(category),
            content=content,
            source_url=url,
            created_at=run.completed_at,
        )
        self.entries.setdefault(run.id, []).append(entry)
        return entry

    # --- repository fakes -------------------------------------------------

    def get_competitor(self, db, cid):
        return self.competitors.get(cid)

    def list_competitors(self, db, skip=0, limit=100):
        return list(self.competitors.values())[skip : skip + limit]

    def create_competitor(self, db, name, website_url):
        cleaned = repo.clean_url(website_url)
        normalized = repo.normalize_url(cleaned)
        if any(repo.normalize_url(c.website_url) == normalized for c in self.competitors.values()):
            raise repo.DuplicateCompetitorError("A competitor with this website already exists.")
        return self.add_competitor(name, cleaned)

    def delete_competitor(self, db, cid):
        return self.competitors.pop(cid, None) is not None

    def get_latest_research_run(self, db, cid):
        runs = self.competitors[cid].research_runs
        return max(runs, key=lambda r: r.id) if runs else None

    def get_latest_swot(self, db, cid):
        done = [r for r in self.competitors[cid].research_runs
                if r.status == ResearchStatus.COMPLETED]
        if not done:
            return []
        return self.entries.get(max(done, key=lambda r: r.id).id, [])

    def get_overall_swot(self, db):
        return self.overall

    def is_overall_swot_current(self, db):
        return self.overall_current

    def has_completed_research(self, db):
        return any(run.status == ResearchStatus.COMPLETED for run in self.runs.values())

    def get_research_run(self, db, rid):
        return self.runs.get(rid)

    def query_runs(self, db, competitor_id, limit):
        runs = sorted(self.runs.values(), key=lambda r: r.id, reverse=True)
        if competitor_id is not None:
            runs = [r for r in runs if r.competitor_id == competitor_id]
        return runs[:limit]

    def trigger_research_task(self, competitor_id, background_tasks, db):
        self.triggered.append(competitor_id)
        return self.add_run(self.competitors[competitor_id], ResearchStatus.PENDING)

    def trigger_overall_swot_refresh(self, background_tasks):
        self.overall_refreshes += 1


@pytest.fixture
def store(monkeypatch):
    fake = FakeStore()
    for name in (
        "get_competitor", "list_competitors", "create_competitor", "delete_competitor",
        "get_latest_research_run", "get_latest_swot", "get_overall_swot",
        "is_overall_swot_current", "has_completed_research", "get_research_run",
    ):
        monkeypatch.setattr(repo, name, getattr(fake, name))
    monkeypatch.setattr(runs_routes, "query_runs", fake.query_runs)
    monkeypatch.setattr(orchestrator, "trigger_research_task", fake.trigger_research_task)
    monkeypatch.setattr(
        orchestrator, "trigger_overall_swot_refresh", fake.trigger_overall_swot_refresh
    )
    return fake


@pytest.fixture
def client(store):
    app = create_app()
    app.dependency_overrides[get_db] = lambda: SimpleNamespace(expire=lambda *a, **k: None)
    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def researched(store):
    """Vanta with one completed run and a full SWOT."""
    vanta = store.add_competitor("Vanta", "https://vanta.com")
    run = store.add_run(
        vanta, ResearchStatus.COMPLETED,
        analysis={"summary": "Vanta automates SOC 2."}, scraped={"pages": []},
    )
    store.add_entry(run, "strength", "Broad integrations", "https://www.vanta.com/features/")
    store.add_entry(run, "weakness", "High pricing", "https://vanta.com/pricing")
    store.add_entry(run, "opportunity", "EU CRA gap", None)
    store.add_entry(run, "threat", "Big budget", "https://vanta.com/")
    return vanta


# ---------------------------------------------------------------------------
# Health, CORS, company
# ---------------------------------------------------------------------------


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_cors_allows_local_frontend(client):
    response = client.options(
        "/competitors",
        headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST"},
    )
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_company_profile(client):
    body = client.get("/company").json()
    assert body["offerings"][0].startswith("Frameworks: SOC 2")
    assert body["positioningNotes"]
    assert body["github"]["connected"] is False and "statusLabel" in body["github"]


# ---------------------------------------------------------------------------
# Competitors
# ---------------------------------------------------------------------------


def test_list_competitors_camelcase_and_status(client, researched, store):
    pending = store.add_competitor("Drata", "https://drata.com")
    store.add_run(pending, ResearchStatus.RUNNING)

    body = client.get("/competitors").json()
    by_name = {c["name"]: c for c in body}
    assert by_name["Vanta"] == {
        "id": str(researched.id),
        "name": "Vanta",
        "website": "https://vanta.com",
        "trend": "flat",
        "researchStatus": "success",
        "lastResearchedAt": "2026-10-19T12:01:30Z",
        "recentChangeSummary": "Research completed",
    }
    assert by_name["Drata"]["researchStatus"] == "running"
    assert by_name["Drata"]["lastResearchedAt"] is None


def test_list_competitors_rejects_bad_pagination(client):
    response = client.get("/competitors?limit=0")
    assert response.status_code == 422
    assert "limit" in response.json()["detail"]


def test_get_competitor_profile(client, researched):
    body = client.get(f"/competitors/{researched.id}").json()
    assert body["summary"] == "Vanta automates SOC 2."
    assert {f["category"] for f in body["findings"]} == {
        "strength", "weakness", "opportunity", "threat"}
    strength = next(f for f in body["findings"] if f["category"] == "strength")
    assert strength["text"] == "Broad integrations"
    assert strength["sourceLabel"] == "vanta.com/features"
    assert strength["competitorName"] == "Vanta"
    opportunity = next(f for f in body["findings"] if f["category"] == "opportunity")
    assert opportunity["sourceUrl"] == "" and opportunity["sourceLabel"] == "No source"
    assert body["chartSeries"]["scores"] == []
    assert body["chartSeries"]["activityOverTime"] == [
        {"label": label, "value": 0}
        for label in ("Sep 14", "Sep 21", "Sep 28", "Oct 5", "Oct 12")
    ] + [{"label": "Oct 19", "value": 1}]
    assert body["recentActivity"][0]["text"].startswith("Research run completed")


def test_get_competitor_not_researched(client, store):
    new = store.add_competitor("Secureframe", "https://secureframe.com")
    body = client.get(f"/competitors/{new.id}").json()
    assert body["researchStatus"] == "pending"
    assert body["summary"] == "Profile not researched yet."
    assert body["findings"] == []
    assert body["chartSeries"] == {
        "scores": [], "activityOverTime": [], "signalsBySource": [], "hiringTrend": []}


def test_profile_scores_from_analysis(client, store):
    drata = store.add_competitor("Drata", "https://drata.com")
    store.add_run(drata, ResearchStatus.COMPLETED, analysis={"summary": "s", "scores": [
        {"feature": "Evidence collection", "score": 8},
        {"feature": "Out of range", "score": 14},
        {"feature": "", "score": 5},
        {"feature": "Bad score", "score": "high"},
        "not a dict",
    ]})
    scores = client.get(f"/competitors/{drata.id}").json()["chartSeries"]["scores"]
    assert scores == [
        {"label": "Evidence collection", "competitor": 8, "scadable": None},
        {"label": "Out of range", "competitor": 10, "scadable": None},
    ]


def test_profile_reused_run_keeps_scores_and_counts_only_changes(client, store):
    drata = store.add_competitor("Drata", "https://drata.com")
    first = store.add_run(drata, ResearchStatus.COMPLETED, analysis={
        "summary": "Drata summary", "scores": [{"feature": "Audits", "score": 6}]})
    reused = store.add_run(drata, ResearchStatus.COMPLETED, analysis={
        "skipped_llm": True, "reused_from_run_id": first.id})
    reused.completed_at += timedelta(weeks=1)
    body = client.get(f"/competitors/{drata.id}").json()
    assert body["summary"] == "Drata summary"
    assert body["chartSeries"]["scores"] == [
        {"label": "Audits", "competitor": 6, "scadable": None}]
    activity = body["chartSeries"]["activityOverTime"]
    assert activity[-2:] == [{"label": "Oct 19", "value": 1}, {"label": "Oct 26", "value": 0}]


def test_profile_keeps_previous_scores_when_latest_run_has_none(client, store):
    drata = store.add_competitor("Drata", "https://drata.com")
    store.add_run(drata, ResearchStatus.COMPLETED, analysis={
        "summary": "old", "scores": [{"feature": "Audits", "score": 6}]})
    # New content analyzed, but the comparison that scores it failed or returned nothing
    store.add_run(drata, ResearchStatus.COMPLETED, analysis={"summary": "new", "scores": []})
    body = client.get(f"/competitors/{drata.id}").json()
    assert body["summary"] == "new"
    assert body["chartSeries"]["scores"] == [
        {"label": "Audits", "competitor": 6, "scadable": None}]


@pytest.mark.parametrize("path,status", [("/competitors/999", 404), ("/competitors/abc", 422),
                                         ("/competitors/0", 422)])
def test_get_competitor_errors(client, path, status):
    response = client.get(path)
    assert response.status_code == status
    assert isinstance(response.json()["detail"], str)


def test_create_competitor_queues_research(client, store):
    response = client.post(
        "/competitors",
        json={"name": "  Drata ", "dataSources": ["drata.com", "https://drata.com/careers"]},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["competitor"]["name"] == "Drata"
    assert body["competitor"]["website"] == "https://drata.com"
    assert body["competitor"]["researchStatus"] == "pending"
    assert body["queuedRunId"] == "1"
    assert store.triggered == [int(body["competitor"]["id"])]


@pytest.mark.parametrize(
    "payload,fragment",
    [
        ({"name": "", "dataSources": ["drata.com"]}, "name"),
        ({"name": "   ", "dataSources": ["drata.com"]}, "blank"),
        ({"name": "Drata", "dataSources": []}, "dataSources"),
        ({"name": "Drata", "dataSources": ["  "]}, "at least one data source"),
        ({"name": "Drata", "dataSources": ["javascript:alert(1)"]}, "HTTP or HTTPS"),
        ({"name": "Drata", "dataSources": ["ftp://drata.com"]}, "HTTP or HTTPS"),
        ({"name": "Drata"}, "dataSources"),
        ({"name": "Drata", "dataSources": ["drata.com"], "extra": 1}, "extra"),
    ],
)
def test_create_competitor_validation(client, store, payload, fragment):
    response = client.post("/competitors", json=payload)
    assert response.status_code == 422
    assert fragment.lower() in response.json()["detail"].lower()
    assert store.triggered == []


def test_create_competitor_invalid_json(client):
    response = client.post(
        "/competitors", content="{not json", headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 422


def test_create_duplicate_competitor_conflict(client, researched, store):
    response = client.post(
        "/competitors", json={"name": "Vanta again", "dataSources": ["https://www.vanta.com/pricing"]}
    )
    assert response.status_code == 409
    assert "already exists" in response.json()["detail"]
    assert store.triggered == []


def test_delete_competitor(client, researched, store):
    assert client.delete(f"/competitors/{researched.id}").status_code == 204
    assert researched.id not in store.competitors
    assert store.overall_refreshes == 1
    assert client.delete(f"/competitors/{researched.id}").status_code == 404
    assert store.overall_refreshes == 1  # no refresh when nothing was deleted


def test_rerun_queues_new_run(client, researched, store):
    response = client.post(f"/competitors/{researched.id}/rerun")
    assert response.status_code == 202
    body = response.json()
    assert body["researchStatus"] == "pending"
    # Previous findings stay visible while the new run is queued.
    assert len(body["findings"]) == 4
    assert store.triggered == [researched.id]


def test_rerun_conflicts_while_running(client, researched, store):
    store.add_run(researched, ResearchStatus.RUNNING)
    response = client.post(f"/competitors/{researched.id}/rerun")
    assert response.status_code == 409
    assert "already running" in response.json()["detail"]
    assert store.triggered == []


def test_rerun_unknown_competitor(client):
    assert client.post("/competitors/42/rerun").status_code == 404


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


def test_list_and_get_runs(client, researched, store):
    failed = store.add_run(
        researched, ResearchStatus.FAILED, scraped={"pages": []}, error="LLM timed out"
    )
    body = client.get("/runs").json()
    assert [r["id"] for r in body] == [str(failed.id), "1"]

    detail = client.get(f"/runs/{failed.id}").json()
    assert detail["status"] == "failed"
    assert detail["competitorName"] == "Vanta"
    assert detail["errorMessage"] == "Research failed due to an internal error."
    assert [s["status"] for s in detail["steps"]] == ["success", "failed", "pending"]
    assert detail["stepCount"] == 3

    ok = client.get("/runs/1").json()
    assert ok["status"] == "success"
    assert [s["status"] for s in ok["steps"]] == ["success", "success", "success"]
    assert ok["cacheMisses"] == 1 and ok["cacheHits"] == 0

def test_failed_run_does_not_expose_sensitive_errors(client, researched, store):
    secret = "postgresql://admin:password123@internal-db"
    run = store.add_run(
        researched,
        ResearchStatus.FAILED,
        error=f"Database connection failed: {secret}",
    )

    # Individual run endpoint
    detail = client.get(f"/runs/{run.id}").json()
    assert detail["errorMessage"] == "Research failed due to an internal error."
    assert secret not in str(detail)

    # Runs listing endpoint
    runs = client.get("/runs").json()
    assert secret not in str(runs)

    # Competitor activity endpoint
    profile = client.get(f"/competitors/{researched.id}").json()
    assert secret not in str(profile)
    assert profile["recentActivity"][0]["text"] == (
        "Research run failed: Research failed due to an internal error."
    )


def test_run_skipped_llm_counts_as_cache_hit(client, researched, store):
    run = store.add_run(
        researched, ResearchStatus.COMPLETED,
        analysis={"skipped_llm": True, "reused_from_run_id": 1}, scraped={"pages": []},
    )
    body = client.get(f"/runs/{run.id}").json()
    assert body["cacheHits"] == 1
    assert body["steps"][1]["result"] == "Skipped (content unchanged)"
    # Profile still shows the summary from the run whose analysis was reused.
    assert client.get(f"/competitors/{researched.id}").json()["summary"] == "Vanta automates SOC 2."


def test_list_runs_filter_and_errors(client, researched, store):
    other = store.add_competitor("Drata", "https://drata.com")
    store.add_run(other, ResearchStatus.PENDING)
    body = client.get(f"/runs?competitorId={other.id}").json()
    assert [r["competitorName"] for r in body] == ["Drata"]
    assert client.get("/runs/999").status_code == 404
    assert client.get("/runs?limit=1000").status_code == 422


# ---------------------------------------------------------------------------
# SWOT overview
# ---------------------------------------------------------------------------


def test_swot_overview_never_falls_back_to_individual_competitor_swot(client, researched):
    # A failed/unfinished overall comparison must not look like a current one.
    for url in ("/swot", "/swot/overview"):
        response = client.get(url)
        assert response.status_code == 200
        body = response.json()
        assert body["isStale"] is True
        assert body["updatedAt"] is None
        assert all(not body[key] for key in ("strengths", "weaknesses", "opportunities", "threats"))


def test_swot_overview_prefers_overall(client, researched, store):
    store.overall_current = True
    store.overall = [
        SimpleNamespace(id=99, category=SWOTCategory.STRENGTH, content="Overall strength",
                        source_url=None, created_at=T0)
    ]
    body = client.get("/swot/overview").json()
    assert body["isStale"] is False
    assert [f["text"] for f in body["strengths"]] == ["Overall strength"]
    assert body["strengths"][0]["competitorName"] == "SCADABLE"
    assert body["threats"] == []


def test_swot_overview_empty(client):
    assert client.get("/swot/overview").json() == {
        "isStale": False, "updatedAt": None, "strengths": [], "weaknesses": [],
        "opportunities": [], "threats": []}


def test_swot_overview_hides_saved_findings_when_stale(client, researched, store):
    store.overall = [
        SimpleNamespace(id=99, category=SWOTCategory.STRENGTH, content="Old overall SWOT",
                        source_url=None, created_at=T0)
    ]
    for url in ("/swot", "/swot/overview"):
        body = client.get(url).json()
        assert body["isStale"] is True
        assert body["strengths"] == []


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_unexpected_error_returns_clean_500(client, monkeypatch):
    def boom(*args, **kwargs):
        raise RuntimeError("database exploded with secrets")

    monkeypatch.setattr(repo, "list_competitors", boom)
    response = client.get("/competitors")
    assert response.status_code == 500
    assert response.json() == {"detail": "Internal server error."}


def test_get_swot_matches_overview(client, researched):
    assert client.get("/swot").json() == client.get("/swot/overview").json()


def test_get_swot_for_one_competitor(client, researched, store):
    other = store.add_competitor("Drata", "https://drata.com")
    run = store.add_run(other, ResearchStatus.COMPLETED, analysis={"summary": "x"})
    store.add_entry(run, "threat", "Drata threat", "https://drata.com")

    body = client.get(f"/swot?competitorId={other.id}").json()
    assert [f["text"] for f in body["threats"]] == ["Drata threat"]
    assert body["strengths"] == []
    assert client.get("/swot?competitorId=999").status_code == 404
    assert client.get("/swot?competitorId=0").status_code == 422
