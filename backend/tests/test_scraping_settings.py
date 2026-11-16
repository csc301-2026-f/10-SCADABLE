"""Scraping depth settings: persistence, API permissions, and crawler wiring."""

import asyncio
import os
import sys
from types import ModuleType, SimpleNamespace

os.environ.setdefault("DATABASE_URL", "sqlite+pysqlite:///:memory:")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.db.database import get_db  # noqa: E402
from app.db.models import ScrapingSettings  # noqa: E402
from app.main import create_app  # noqa: E402
from app.services import crawler  # noqa: E402
from app.services.scraping_settings import (  # noqa: E402
    get_scraping_depth,
    get_scraping_page_limit,
    set_scraping_depth,
)


@pytest.fixture
def sessions():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ScrapingSettings.__table__.create(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        engine.dispose()


@pytest.fixture
def client(sessions, monkeypatch):
    monkeypatch.setenv("SCRAPING_SETTINGS_ADMIN_TOKEN", "test-admin-token")
    monkeypatch.delenv("AUTO_REFRESH_ENABLED", raising=False)
    app = create_app()

    def use_test_db():
        with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = use_test_db
    with TestClient(app) as test_client:
        yield test_client


def test_depth_defaults_to_low_and_is_persisted(sessions):
    with sessions() as db:
        assert get_scraping_depth(db) == "low"
        assert get_scraping_page_limit(db) == 5
        assert set_scraping_depth(db, "medium") == 10
    with sessions() as db:
        assert get_scraping_depth(db) == "medium"
        assert set_scraping_depth(db, "high") == 15
    with sessions() as db:
        assert get_scraping_page_limit(db) == 15
        assert set_scraping_depth(db, "low") == 5
    with sessions() as db:
        assert db.query(ScrapingSettings).count() == 1
        assert get_scraping_depth(db) == "low"


def test_settings_api_get_update_and_persist(client):
    assert client.get("/settings/scraping").json() == {"depth": "low", "maxPages": 5}
    for depth, max_pages in (("medium", 10), ("high", 15), ("low", 5)):
        response = client.put(
            "/settings/scraping",
            json={"depth": depth},
            headers={"Authorization": "Bearer test-admin-token"},
        )
        assert response.status_code == 200, response.text
        assert response.json() == {"depth": depth, "maxPages": max_pages}
        assert client.get("/settings/scraping").json() == response.json()


def test_settings_api_requires_admin_token(client):
    for header in ({}, {"Authorization": "Bearer wrong"}, {"Authorization": "Basic test-admin-token"}):
        response = client.put("/settings/scraping", json={"depth": "high"}, headers=header)
        assert response.status_code == 401
    assert client.get("/settings/scraping").json()["depth"] == "low"


def test_settings_api_fails_closed_without_configured_token(client, monkeypatch):
    monkeypatch.delenv("SCRAPING_SETTINGS_ADMIN_TOKEN")
    response = client.put("/settings/scraping", json={"depth": "medium"})
    assert response.status_code == 503
    assert client.get("/settings/scraping").status_code == 200


@pytest.mark.parametrize("payload", [{"depth": "ultra"}, {"depth": "HIGH"}, {}, {"depth": 15}, {"depth": "low", "maxPages": 999}])
def test_settings_api_rejects_invalid_values(client, payload):
    response = client.put(
        "/settings/scraping",
        json=payload,
        headers={"Authorization": "Bearer test-admin-token"},
    )
    assert response.status_code == 422, response.text
    assert client.get("/settings/scraping").json()["depth"] == "low"


def test_settings_api_allows_put_cors(client):
    response = client.options(
        "/settings/scraping",
        headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "PUT"},
    )
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert "PUT" in response.headers["access-control-allow-methods"]


def test_real_crawler_uses_current_database_setting(sessions, monkeypatch):
    """Exercise the real adapter path, replacing only external scraper I/O."""
    monkeypatch.setattr(crawler, "SessionLocal", sessions)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("ALLOW_MOCK_CRAWLER", raising=False)
    observed = []

    class FakeScraper:
        def __init__(self, *, decision_tracking_context_factory):
            self.context_factory = decision_tracking_context_factory

        async def crawl(self, competitor, config):
            observed.append(config.max_pages)
            return SimpleNamespace(
                pages=[SimpleNamespace(
                    source_url="https://example.com/features",
                    final_url="https://example.com/features",
                    page_title="Features",
                    page_category="features",
                    cleaned_markdown="Some genuine product features",
                    crawl_status="success",
                )],
                run_status="success", warnings=[], homepage_url="https://example.com",
                decision_tracking=None, decision_usage=None,
            )

    def module(name, **attrs):
        obj = ModuleType(name)
        for key, value in attrs.items():
            setattr(obj, key, value)
        monkeypatch.setitem(sys.modules, name, obj)
        return obj

    module(
        "scraper",
        CompetitorInput=lambda **kwargs: SimpleNamespace(**kwargs),
        ScraperConfig=lambda **kwargs: SimpleNamespace(**kwargs),
        SelectionConfig=lambda **kwargs: SimpleNamespace(**kwargs),
    )
    module("scraper.environment", load_environment=lambda: None)
    module(
        "scraper.output",
        competitor_directory=lambda _: "example.com",
        export_competitor=lambda *args: None,
    )
    module("scraper.service", CompetitorScraper=FakeScraper)
    module("scraper.decision_tracking", DecisionTrackingContext=lambda **kwargs: None)

    for depth, limit in (("low", 5), ("medium", 10), ("high", 15)):
        with sessions() as db:
            set_scraping_depth(db, depth)
        result = asyncio.run(crawler.Crawl4AIAdapter().crawl("Example", "https://example.com"))
        assert result["mode"] == "real"
        assert observed[-1] == limit
    assert observed == [5, 10, 15]
