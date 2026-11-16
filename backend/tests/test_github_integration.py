"""Offline tests: no GitHub credentials, outbound HTTP, or database required."""

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes import github as github_routes
from app.services.github_client import GitHubAppClient, GitHubClientError, GitHubRepo
from app.services.github_webhooks import InvalidWebhook, parse_main_push, verify_github_signature


class StubClient:
    def list_installations(self):
        return [{"id": 21, "account": "scadable", "account_type": "Organization"}]

    def list_repositories(self, installation_id):
        assert installation_id == 21
        return [GitHubRepo(101, "scadable/product", "main", True)]


class StubStore:
    def __init__(self):
        self.selection = None
        self.deliveries = set()
        self.events = []

    def get_selected(self):
        return self.selection

    def save_selected(self, selection):
        self.selection = selection

    def record_push(self, event):
        if event.delivery_id in self.deliveries:
            return False
        self.deliveries.add(event.delivery_id)
        self.events.append(event)
        # A changed commit is pending processing; it's not a completed sync.
        self.selection.sync_status = "pending"
        self.selection.last_commit_sha = event.commit_sha
        return True


@pytest.fixture
def api(monkeypatch):
    monkeypatch.setenv("GITHUB_INTEGRATION_ADMIN_TOKEN", "test-admin-secret")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "test-webhook-secret")
    app = FastAPI()
    app.include_router(github_routes.router)
    store = StubStore()
    app.dependency_overrides[github_routes.get_selection_store] = lambda: store
    app.dependency_overrides[github_routes.get_github_client] = lambda: StubClient()
    return TestClient(app), store


def signed_push(*, repo_id=101, branch="main", installation_id=21, delivery="delivery-a"):
    body = json.dumps({
        "ref": f"refs/heads/{branch}",
        "deleted": False,
        "after": "a" * 40,
        "installation": {"id": installation_id},
        "repository": {"id": repo_id, "full_name": "scadable/product"},
    }).encode()
    sig = "sha256=" + hmac.new(b"test-webhook-secret", body, hashlib.sha256).hexdigest()
    return body, {
        "X-GitHub-Event": "push",
        "X-GitHub-Delivery": delivery,
        "X-Hub-Signature-256": sig,
    }


def test_management_endpoints_fail_closed_without_auth(api):
    client, _ = api
    assert client.get("/github/installations").status_code == 401
    assert client.get("/github/repositories?installation_id=21").status_code == 401
    assert client.post("/github/selection", json={"installationId": 21, "repositoryId": 101}).status_code == 401


def test_installation_repo_selection_and_status(api):
    client, store = api
    headers = {"Authorization": "Bearer test-admin-secret"}
    assert client.get("/github/installations", headers=headers).json()[0]["account"] == "scadable"
    assert client.get("/github/repositories?installation_id=21", headers=headers).json()[0]["full_name"] == "scadable/product"
    bad = client.post("/github/selection", headers=headers, json={"installationId": 21, "repositoryId": 999})
    assert bad.status_code == 404
    ok = client.post("/github/selection", headers=headers, json={"installationId": 21, "repositoryId": 101})
    assert ok.status_code == 200
    assert ok.json()["fullName"] == "scadable/product"
    assert store.get_selected().repository_id == 101
    assert client.get("/github/status", headers=headers).json()["syncStatus"] == "not_synced"


def test_valid_push_and_duplicate_delivery(api):
    client, store = api
    client.post("/github/selection", headers={"Authorization": "Bearer test-admin-secret"}, json={"installationId": 21, "repositoryId": 101})
    body, headers = signed_push()
    first = client.post("/github/webhook", content=body, headers=headers)
    assert first.status_code == 202 and first.json() == {"status": "recorded"}
    assert len(store.events) == 1
    assert store.selection.sync_status == "pending"
    assert client.post("/github/webhook", content=body, headers=headers).json() == {"status": "duplicate"}
    assert len(store.events) == 1


@pytest.mark.parametrize("invalid_headers", [
    {"X-Hub-Signature-256": "sha256=" + "0" * 64},
    {"X-Hub-Signature-256": "sha1=bad"},
    {"X-Hub-Signature-256": ""},
])
def test_bad_webhook_signature_does_not_change_state(api, invalid_headers):
    client, store = api
    body, headers = signed_push()
    headers.update(invalid_headers)
    response = client.post("/github/webhook", content=body, headers=headers)
    assert response.status_code == 401
    assert not store.events


def test_webhooks_ignore_non_main_and_other_repositories(api):
    client, store = api
    client.post("/github/selection", headers={"Authorization": "Bearer test-admin-secret"}, json={"installationId": 21, "repositoryId": 101})
    for kw in ({"branch": "dev"}, {"repo_id": 999}, {"installation_id": 900}):
        body, headers = signed_push(**kw)
        assert client.post("/github/webhook", content=body, headers=headers).json() == {"status": "ignored"}
    assert not store.events


def test_bad_payload_and_oversized_payload_rejected(api):
    client, _ = api
    garbage = b"no json"
    sig = "sha256=" + hmac.new(b"test-webhook-secret", garbage, hashlib.sha256).hexdigest()
    r = client.post("/github/webhook", content=garbage, headers={"X-GitHub-Event": "push", "X-GitHub-Delivery": "bad", "X-Hub-Signature-256": sig})
    assert r.status_code == 401
    r = client.post("/github/webhook", content=b"X" * (github_routes.MAX_WEBHOOK_BYTES + 1))
    assert r.status_code == 413


def test_parse_non_push_event_ignored():
    assert parse_main_push("installation", "abc", b"{}") is None


def test_client_requests_installation_token_and_lists_repositories():
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path, request.headers.get("authorization")))
        if request.url.path.endswith("/access_tokens"):
            return httpx.Response(201, json={"token": "installation-short-lived-token"})
        if request.url.path == "/installation/repositories":
            return httpx.Response(200, json={"repositories": [
                {"id": 101, "full_name": "scadable/product", "default_branch": "main", "private": True}
            ]})
        raise AssertionError(request.url)

    transport = httpx.MockTransport(handler)
    with httpx.Client(transport=transport) as http:
        client = GitHubAppClient("123", "fake-private-key", http_client=http)
        client._app_jwt = lambda: "app-jwt"  # don't sign keys in this transport test
        repos = client.list_repositories(21)
    assert [repo.full_name for repo in repos] == ["scadable/product"]
    assert calls[0] == ("POST", "/app/installations/21/access_tokens", "Bearer app-jwt")
    assert calls[1] == ("GET", "/installation/repositories", "Bearer installation-short-lived-token")


def test_client_reads_file_when_path_is_known():
    def handler(request):
        if request.url.path.endswith("/access_tokens"):
            return httpx.Response(201, json={"token": "token"})
        if request.url.path == "/repos/scadable/product/contents/docs/features.md":
            assert request.url.params["ref"] == "main"
            return httpx.Response(200, json={"type": "file", "encoding": "base64", "sha": "b" * 40, "content": base64.b64encode(b"# Features\n- SOC 2").decode()})
        raise AssertionError(str(request.url))

    with httpx.Client(transport=httpx.MockTransport(handler)) as http:
        client = GitHubAppClient("123", "fake-private-key", http_client=http)
        client._app_jwt = lambda: "app-jwt"
        assert client.read_repository_file(21, "scadable/product", "docs/features.md") == "# Features\n- SOC 2"
    with pytest.raises(ValueError):
        client.read_repository_file(21, "scadable/product", "../.env")
