"""SQLite-backed persistence tests for the GitHub App adapter.

The production DB is PostgreSQL; these exercise SQLAlchemy logic without
loading the unrelated PostgreSQL JSONB tables.
"""

import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.routes import github
from app.db.database import get_db
from app.db.models import GitHubRepositorySelection, GitHubWebhookDelivery, GitHubFeatureState
from app.db.github_store import DatabaseGitHubSelectionStore
from app.main import create_app
from app.services.github_client import GitHubRepo
from app.services.github_webhooks import MainPushEvent


@pytest.fixture
def session_factory():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    GitHubRepositorySelection.__table__.create(engine)
    GitHubWebhookDelivery.__table__.create(engine)
    GitHubFeatureState.__table__.create(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    yield session_factory
    engine.dispose()


def selection():
    return github.SelectedRepository(
        installation_id=77,
        repository_id=101,
        full_name='scadable/app',
        default_branch='main',
    )


def event(delivery='delivery-1', repository_id=101):
    return MainPushEvent(
        installation_id=77,
        repository_id=repository_id,
        repository_full_name='scadable/app',
        commit_sha='a' * 40,
        delivery_id=delivery,
    )


def test_db_selection_survives_new_session_and_reselection_resets_state(session_factory):
    with session_factory() as db:
        store = DatabaseGitHubSelectionStore(db)
        assert store.get_selected() is None
        store.save_selected(selection())
        assert store.record_push(event())
        assert store.get_selected().sync_status == 'pending'

    with session_factory() as db:
        store = DatabaseGitHubSelectionStore(db)
        selected = store.get_selected()
        assert selected.full_name == 'scadable/app'
        assert selected.last_commit_sha == 'a' * 40
        store.save_selected(selection())
        assert store.get_selected().last_commit_sha is None
        assert store.get_selected().sync_status == 'not_synced'


def test_db_duplicate_delivery_persists_across_sessions(session_factory):
    with session_factory() as db:
        DatabaseGitHubSelectionStore(db).save_selected(selection())
    with session_factory() as db:
        assert DatabaseGitHubSelectionStore(db).record_push(event()) is True
    with session_factory() as db:
        store = DatabaseGitHubSelectionStore(db)
        assert store.record_push(event()) is False
        assert store.record_push(event('delivery-2')) is True
        assert store.record_push(event('delivery-3', repository_id=999)) is False
        assert store.get_selected().last_commit_sha == 'a' * 40
        assert db.query(GitHubWebhookDelivery).count() == 2


def test_github_router_registered_in_actual_app(monkeypatch):
    monkeypatch.setenv("GITHUB_INTEGRATION_ADMIN_TOKEN", "admin-test-secret")
    app = create_app()

    with TestClient(app) as client:
        # Registered protected endpoint returns 401, not 404.
        assert client.get("/github/status").status_code == 401

        # Webhook only accepts POST, so GET returns 405.
        assert client.get("/github/webhook").status_code == 405


def test_real_app_api_uses_database_and_webhook_works(monkeypatch, session_factory):
    monkeypatch.setenv('GITHUB_INTEGRATION_ADMIN_TOKEN', 'admin-test-secret')
    monkeypatch.setenv('GITHUB_WEBHOOK_SECRET', 'webhook-test-secret')

    class StubClient:
        def list_repositories(self, installation_id):
            return [GitHubRepo(101, 'scadable/app', 'main', True)]

    def override_db():
        with session_factory() as db:
            yield db

    app = create_app()
    app.dependency_overrides[get_db] = override_db
    app.dependency_overrides[github.get_github_client] = lambda: StubClient()
    with TestClient(app) as client:
        headers = {'Authorization': 'Bearer admin-test-secret'}
        assert client.get('/github/status', headers=headers).json() is None
        response = client.post('/github/selection', headers=headers,
                               json={'installationId': 77, 'repositoryId': 101})
        assert response.status_code == 200
        assert response.json()['fullName'] == 'scadable/app'

        raw = json.dumps({
            'ref': 'refs/heads/main',
            'deleted': False,
            'after': 'a' * 40,
            'installation': {'id': 77},
            'repository': {'id': 101, 'full_name': 'scadable/app'},
        }).encode()
        sig = 'sha256=' + hmac.new(b'webhook-test-secret', raw, hashlib.sha256).hexdigest()
        push_headers = {
            'X-GitHub-Event': 'push',
            'X-GitHub-Delivery': 'delivery-1',
            'X-Hub-Signature-256': sig,
        }
        assert client.post('/github/webhook', content=raw, headers=push_headers).json() == {'status': 'recorded'}
        assert client.post('/github/webhook', content=raw, headers=push_headers).json() == {'status': 'duplicate'}
        current = client.get('/github/status', headers=headers).json()
        assert current['syncStatus'] == 'pending'
        assert current['lastCommitSha'] == 'a' * 40
