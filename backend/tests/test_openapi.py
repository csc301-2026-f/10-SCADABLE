"""Ensure handoff API documentation covers the current core workflows."""
import os

os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from fastapi.testclient import TestClient
from app.main import create_app


def test_swagger_and_openapi_cover_core_workflows():
    client = TestClient(create_app())
    assert client.get("/docs").status_code == 200
    response = client.get("/openapi.json")
    assert response.status_code == 200
    paths = response.json()["paths"]
    for path, method in [
        ("/health", "get"), ("/competitors", "post"),
        ("/competitors/{competitor_id}", "get"),
        ("/competitors/{competitor_id}/rerun", "post"),
        ("/competitors/{competitor_id}/changes", "get"),
        ("/runs", "get"), ("/runs/{run_id}/cancel", "post"),
        ("/swot/overview", "get"), ("/company", "get"),
        ("/github/status", "get"), ("/github/sync", "post"),
        ("/github/webhook", "post"), ("/settings/scraping", "put"),
    ]:
        operation = paths[path][method]
        assert operation["summary"]
        assert operation.get("description"), path
    cancel = paths["/runs/{run_id}/cancel"]["post"]
    assert any(p["name"].lower() == "x-scadable-research-bridge-token" for p in cancel["parameters"])
    assert any(p["name"].lower() == "authorization" for p in paths["/github/sync"]["post"]["parameters"])
    schemas = response.json()["components"]["schemas"]
    assert "conflictingData" in schemas["CompetitorProfile"]["properties"]
    assert "isStale" in schemas["SwotOverview"]["properties"]
