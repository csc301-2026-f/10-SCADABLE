"""Offline, end-to-end tests: GitHub App HTTP -> Postgres-compatible state -> baseline.

SQLite creates only the GitHub and freshness tables: no real GitHub credentials,
paid LLM, scraper, or network requests are used.
"""

import base64
import hashlib
import hmac
import json

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.routes import github
from app.core.baseline import SCADABLE_BASELINE, get_scadable_baseline
from app.db.database import get_db
from app.db.github_store import DatabaseGitHubSelectionStore
from app.db.models import (
    GitHubFeatureState, GitHubRepositorySelection, GitHubWebhookDelivery, OverallSwotState
)
from app.db.repository import get_overall_swot_revision
from app.main import create_app
from app.services.github_client import GitHubAppClient, GitHubClientError, GitHubRepo
from app.services.github_features import FeatureFileError, parse_feature_manifest, validate_feature_path
from app.services.github_sync import GitHubSyncError, sync_selected_repository, sync_pending_once
from app.services.github_webhooks import MainPushEvent


MANIFEST = {
    "schema_version": 1,
    "company_name": "SCADABLE",
    "website_url": "https://scadable.com",
    "domain": "Compliance automation",
    "summary": "An evidence-based security and compliance platform.",
    "supported_frameworks": ["SOC 2", "ISO 27001"],
    "core_capabilities": ["GitHub automated evidence", "Real-time policy mapping"],
    "differentiators": ["Automated remediation"],
}


class StubClient:
    def __init__(self):
        self.file = json.dumps(MANIFEST)
        self.sha = "b" * 40
        self.calls = []
        self.fail = False

    def list_installations(self):
        return [{"id": 77, "account": "scadable", "account_type": "Organization"}]

    def list_repositories(self, installation_id):
        assert installation_id == 77
        return [GitHubRepo(101, "scadable/product", "develop", True),
                GitHubRepo(102, "scadable/new-product", "main", True)]

    def read_repository_file_with_metadata(self, install_id, full_name, path, *, ref):
        from app.services.github_client import GitHubFileContents
        self.calls.append((install_id, full_name, path, ref))
        if self.fail:
            raise GitHubClientError("GitHub API request failed")
        return GitHubFileContents(text=self.file, blob_sha=self.sha)


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setenv("GITHUB_INTEGRATION_ADMIN_TOKEN", "secure-test-token")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "test-github-secret")
    monkeypatch.delenv("GITHUB_APP_ID", raising=False)
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    for table in (GitHubRepositorySelection.__table__, GitHubWebhookDelivery.__table__,
                  GitHubFeatureState.__table__, OverallSwotState.__table__):
        table.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    stub = StubClient()
    app = create_app()

    def db_override():
        with factory() as db:
            yield db

    app.dependency_overrides[get_db] = db_override
    app.dependency_overrides[github.get_github_client] = lambda: stub
    with TestClient(app) as testclient:
        yield testclient, factory, stub
    engine.dispose()


HEADERS = {"Authorization": "Bearer secure-test-token"}


def _select(client, repository_id=101):
    return client.post("/github/selection", headers=HEADERS,
                       json={"installationId": 77, "repositoryId": repository_id})


def _signed_push(branch="develop", *, delivery="delivery-1", sha="a" * 40, repo_id=101):
    payload = json.dumps({
        "ref": "refs/heads/" + branch, "deleted": False, "after": sha,
        "installation": {"id": 77},
        "repository": {"id": repo_id, "full_name": "scadable/product"}
    }).encode()
    signature = "sha256=" + hmac.new(b"test-github-secret", payload, hashlib.sha256).hexdigest()
    return payload, {"X-Hub-Signature-256": signature, "X-GitHub-Event": "push", "X-GitHub-Delivery": delivery}


def test_end_to_end_manual_sync_changes_company_and_revision(setup):
    client, factory, github_client = setup
    assert client.post("/github/sync", headers=HEADERS).status_code == 422
    assert client.get("/github/features").status_code == 401
    assert _select(client).status_code == 200
    assert client.get("/github/status", headers=HEADERS).json()["defaultBranch"] == "develop"
    assert client.post("/github/file", headers=HEADERS,
                       json={"filePath": "product/features.json"}).status_code == 200
    res = client.post("/github/sync", headers=HEADERS)
    assert res.status_code == 200, res.text
    assert res.json() == {"status": "synced", "fileSha": "b" * 40,
                          "filePath": "product/features.json", "baselineChanged": True}
    assert github_client.calls == [(77, "scadable/product", "product/features.json", "develop")]
    company = client.get("/company").json()
    assert "GitHub automated evidence" in company["offerings"]
    assert company["github"]["connected"] is True
    assert company["github"]["trackedFile"] == "product/features.json"
    assert company["github"]["repository"] == "scadable/product"
    assert client.get("/github/features", headers=HEADERS).json()["baseline"]["core_capabilities"] == MANIFEST["core_capabilities"]
    assert client.get("/github/status", headers=HEADERS).json()["syncStatus"] == "synced"
    with factory() as db:
        assert get_overall_swot_revision(db) == 1
    again = client.post("/github/sync", headers=HEADERS)
    assert again.json()["baselineChanged"] is False
    with factory() as db:
        assert get_overall_swot_revision(db) == 1


def test_bad_file_preserves_previous_data_and_records_error(setup):
    client, factory, fake = setup
    _select(client)
    assert client.post("/github/sync", headers=HEADERS).status_code == 200
    fake.file = '{"schema_version": 1, "core_capabilities": []}'
    payload, headers = _signed_push()
    assert client.post("/github/webhook", content=payload, headers=headers).json() == {"status": "recorded"}
    assert client.post("/github/sync", headers=HEADERS).status_code == 502
    status = client.get("/github/status", headers=HEADERS).json()
    assert status["syncStatus"] == "failed"
    assert "core_capabilities" in status["lastError"]
    assert client.get("/company").json()["github"]["connected"] is True
    assert "GitHub automated evidence" in client.get("/company").json()["offerings"]
    with factory() as db:
        assert get_overall_swot_revision(db) == 1


def test_webhook_matches_selected_default_branch_and_syncs_immutable_sha(setup):
    client, factory, fake = setup
    _select(client)
    payload, headers = _signed_push("main")
    assert client.post("/github/webhook", content=payload, headers=headers).json() == {"status": "ignored"}
    payload, headers = _signed_push("develop")
    assert client.post("/github/webhook", content=payload, headers=headers).json() == {"status": "recorded"}
    assert client.post("/github/webhook", content=payload, headers=headers).json() == {"status": "duplicate"}
    assert client.get("/github/status", headers=HEADERS).json()["syncStatus"] == "pending"
    assert client.post("/github/sync", headers=HEADERS).status_code == 200
    assert fake.calls[-1][-1] == "a" * 40
    assert client.get("/github/status", headers=HEADERS).json()["lastCommitSha"] == "a" * 40


def test_repo_change_clears_old_live_baseline_and_marks_stale(setup):
    client, factory, fake = setup
    _select(client)
    assert client.post("/github/sync", headers=HEADERS).status_code == 200
    _select(client, repository_id=102)
    assert "GitHub automated evidence" not in client.get("/company").json()["offerings"]
    assert client.get("/github/features", headers=HEADERS).json()["source"] == "static"
    assert client.get("/github/status", headers=HEADERS).json()["filePath"] == "docs/scadable-features.json"
    with factory() as db:
        assert get_overall_swot_revision(db) == 2


def test_file_change_invalidates_baseline_and_is_path_validated(setup):
    client, factory, _ = setup
    _select(client)
    assert client.post("/github/sync", headers=HEADERS).status_code == 200
    for path in ("../secrets.json", "/root.json", ".github/config.json", "docs/readme.md", "docs//bad.json"):
        assert client.post("/github/file", headers=HEADERS, json={"filePath": path}).status_code == 422
    res = client.post("/github/file", headers=HEADERS, json={"filePath": "features/v2.json"})
    assert res.status_code == 200
    assert res.json()["syncStatus"] == "pending"
    assert client.get("/github/features", headers=HEADERS).json()["source"] == "static"
    with factory() as db:
        assert get_overall_swot_revision(db) == 2


def test_api_failure_preserves_previous_feature_snapshot(setup):
    client, _, fake = setup
    _select(client)
    assert client.post("/github/sync", headers=HEADERS).status_code == 200
    fake.fail = True
    assert client.post("/github/sync", headers=HEADERS).status_code == 502
    assert client.get("/github/status", headers=HEADERS).json()["syncStatus"] == "failed"
    assert "GitHub automated evidence" in client.get("/company").json()["offerings"]


def test_changed_during_fetch_does_not_publish_stale_result(setup):
    client, factory, stub = setup
    _select(client)
    class RacingClient:
        def read_repository_file_with_metadata(self, *args, **kwargs):
            from app.services.github_client import GitHubFileContents
            with factory() as other:
                DatabaseGitHubSelectionStore(other).record_push(MainPushEvent(
                    77, 101, "scadable/product", "c" * 40, "new-delivery", "develop"))
            return GitHubFileContents(json.dumps(MANIFEST), "b" * 40)
    with factory() as db:
        with pytest.raises(GitHubSyncError, match="changed during sync"):
            sync_selected_repository(db, RacingClient())
    with factory() as db:
        selected = DatabaseGitHubSelectionStore(db).get_selected()
        assert selected.sync_status == "pending"
        assert selected.last_commit_sha == "c" * 40
        assert get_scadable_baseline(db) == SCADABLE_BASELINE


def test_mock_git_api_client_rejects_oversize_and_invalid_base64():
    def make_client(response):
        def handler(request):
            if request.url.path.endswith("/access_tokens"):
                return httpx.Response(201, json={"token": "test-installation-token"})
            return httpx.Response(200, json=response)
        http = httpx.Client(transport=httpx.MockTransport(handler))
        gh = GitHubAppClient("123", "test-pem", http_client=http)
        gh._app_jwt = lambda: "test-app-jwt"
        return gh, http
    for payload in (
        {"type": "file", "size": 200_000, "sha": "a" * 40,
         "content": base64.b64encode(b"{}").decode(), "encoding": "base64"},
        {"type": "file", "size": 5, "sha": "a" * 40, "content": "not-base64!!!", "encoding": "base64"},
    ):
        gh, http = make_client(payload)
        with http:
            with pytest.raises(GitHubClientError):
                gh.read_repository_file_with_metadata(77, "scadable/product", "docs/features.json")


@pytest.mark.parametrize("field,payload", [
    ("schema_version", {"schema_version": 2}),
    ("company_name", {"company_name": "OtherCo"}),
    ("core_capabilities", {"core_capabilities": []}),
    ("core_capabilities", {"core_capabilities": [" "]}),
    ("website_url", {"website_url": "javascript:bad"}),
])
def test_manifest_validator_rejects_invalid_inputs(field, payload):
    doc = {**MANIFEST, **payload}
    with pytest.raises(FeatureFileError, match=field):
        parse_feature_manifest(json.dumps(doc))


def test_manifest_parser_deduplicates_and_does_not_inherit_static_fields():
    doc = {"schema_version": 1, "core_capabilities": [" One  ", "one", "Two"]}
    parsed = parse_feature_manifest(json.dumps(doc))
    assert parsed["core_capabilities"] == ["One", "Two"]
    assert parsed["company_name"] == "SCADABLE"
    assert "schema_version" not in parsed


def test_background_worker_processes_pending_after_restart(setup, monkeypatch):
    client, factory, stub = setup
    assert _select(client).status_code == 200
    payload, headers = _signed_push()
    assert client.post("/github/webhook", content=payload, headers=headers).json()["status"] == "recorded"

    from app.services import github_sync
    monkeypatch.setattr(github_sync, "SessionLocal", factory)

    class ManagedStub:
        def __enter__(self):
            return stub
        def __exit__(self, *exc):
            return None

    monkeypatch.setattr(github_sync.GitHubAppClient, "from_env", lambda: ManagedStub())
    sync_pending_once()
    assert client.get("/github/status", headers=HEADERS).json()["syncStatus"] == "synced"
    assert client.get("/github/features", headers=HEADERS).json()["source"] == "github"
    assert stub.calls[-1][-1] == "a" * 40
    sync_pending_once()  # No extra work when already synced.
    assert len(stub.calls) == 1


def test_reselection_during_fetch_prevents_superseded_publish(setup):
    client, factory, stub = setup
    _select(client)

    class ReselectingClient:
        def read_repository_file_with_metadata(self, *args, **kwargs):
            from app.services.github_client import GitHubFileContents
            with factory() as other:
                DatabaseGitHubSelectionStore(other).save_selected(github.SelectedRepository(
                    installation_id=77, repository_id=101, full_name="scadable/product",
                    default_branch="develop"))
            return GitHubFileContents(json.dumps(MANIFEST), "b" * 40)

    with factory() as db:
        with pytest.raises(GitHubSyncError, match="changed during sync"):
            sync_selected_repository(db, ReselectingClient())
    assert client.get("/github/status", headers=HEADERS).json()["syncStatus"] == "not_synced"
    assert client.get("/github/features", headers=HEADERS).json()["source"] == "static"


def test_webhook_duplicate_and_wrong_repo_cannot_trigger_sync(setup):
    client, factory, stub = setup
    _select(client)
    payload, headers = _signed_push(repo_id=999)
    assert client.post("/github/webhook", content=payload, headers=headers).json()["status"] == "ignored"
    assert client.get("/github/status", headers=HEADERS).json()["syncStatus"] == "not_synced"
    assert stub.calls == []
