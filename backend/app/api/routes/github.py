"""GitHub App integration API with persistent repository selection.

Management endpoints require a server-side admin token; NEVER expose this token
in browser JavaScript. Replace it with authenticated-user permissions before
connecting the Our Company page. Webhooks use GitHub's HMAC verification.
"""

from __future__ import annotations

import os
from datetime import datetime
from collections.abc import Generator
from typing import Protocol

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException, Query, Request
from sqlalchemy.orm import Session

from app.db.database import get_db
from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

from app.services.github_client import GitHubAppClient, GitHubClientError, GitHubRepo
from app.services.github_features import DEFAULT_FEATURE_PATH, FeatureFileError, validate_feature_path
from app.services.github_sync import GitHubSyncError, sync_selected_repository
from app.services.github_webhooks import InvalidWebhook, MainPushEvent, parse_main_push, verify_github_signature

router = APIRouter(prefix="/github", tags=["github"])
MAX_WEBHOOK_BYTES = 1_000_000


class RepoSelection(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)
    installation_id: int = Field(gt=0)
    repository_id: int = Field(gt=0)


class SelectedRepository(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)
    installation_id: int
    repository_id: int
    full_name: str
    default_branch: str
    last_commit_sha: str | None = None
    sync_status: str = "not_synced"
    file_path: str = DEFAULT_FEATURE_PATH
    revision: int = 0
    file_sha: str | None = None
    last_synced_at: datetime | None = None
    last_error: str | None = None


class FeatureFileSelection(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)
    file_path: str = Field(min_length=1, max_length=512)


class SelectionStore(Protocol):
    """Storage contract for persistent GitHub repository state and deliveries.

    record_push atomically deduplicates deliveries and marks the repository
    *pending*, not *synced*, because feature extraction has not run yet.
    """

    def get_selected(self) -> SelectedRepository | None: ...

    def save_selected(self, selection: SelectedRepository) -> None: ...

    def record_push(self, event: MainPushEvent) -> bool: ...

    def select_file(self, path: str) -> SelectedRepository: ...


def require_admin(authorization: str | None = Header(default=None)) -> None:
    import hmac

    secret = os.getenv("GITHUB_INTEGRATION_ADMIN_TOKEN", "")
    if not secret:
        raise HTTPException(503, "GitHub management authentication is not configured")
    if not authorization or not authorization.startswith("Bearer ") or not hmac.compare_digest(authorization[7:], secret):
        raise HTTPException(401, "Unauthorized", headers={"WWW-Authenticate": "Bearer"})


def get_github_client() -> Generator[GitHubAppClient, None, None]:
    try:
        client = GitHubAppClient.from_env()
    except GitHubClientError as exc:
        raise HTTPException(503, str(exc)) from exc
    try:
        yield client
    finally:
        client.close()


def get_selection_store(db: Session = Depends(get_db)) -> SelectionStore:
    from app.db.github_store import DatabaseGitHubSelectionStore

    return DatabaseGitHubSelectionStore(db)


@router.get("/installations", dependencies=[Depends(require_admin)])
def installations(client: GitHubAppClient = Depends(get_github_client)) -> list[dict]:
    try:
        return client.list_installations()
    except GitHubClientError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.get("/repositories", dependencies=[Depends(require_admin)])
def repositories(installation_id: int, client: GitHubAppClient = Depends(get_github_client)) -> list[GitHubRepo]:
    if installation_id <= 0:
        raise HTTPException(422, "Invalid installation ID")
    try:
        return client.list_repositories(installation_id)
    except GitHubClientError as exc:
        raise HTTPException(502, str(exc)) from exc


@router.get("/status", dependencies=[Depends(require_admin)])
def status(store: SelectionStore = Depends(get_selection_store)) -> SelectedRepository | None:
    return store.get_selected()


@router.post("/selection", dependencies=[Depends(require_admin)])
def set_selection(
    selection: RepoSelection,
    store: SelectionStore = Depends(get_selection_store),
    client: GitHubAppClient = Depends(get_github_client),
) -> SelectedRepository:
    try:
        available = client.list_repositories(selection.installation_id)
    except GitHubClientError as exc:
        raise HTTPException(502, str(exc)) from exc
    repo = next((repo for repo in available if repo.id == selection.repository_id), None)
    if repo is None:
        raise HTTPException(404, "Repository not accessible to GitHub App")
    stored = SelectedRepository(
        installation_id=selection.installation_id,
        repository_id=repo.id,
        full_name=repo.full_name,
        default_branch=repo.default_branch,
    )
    store.save_selected(stored)
    return stored


@router.post("/file", dependencies=[Depends(require_admin)])
def configure_feature_file(
    payload: FeatureFileSelection,
    store: SelectionStore = Depends(get_selection_store),
) -> SelectedRepository:
    """Choose the team-defined JSON product feature file within the selected repo."""
    try:
        return store.select_file(validate_feature_path(payload.file_path))
    except (ValueError, FeatureFileError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/sync", dependencies=[Depends(require_admin)])
def sync_features(
    background_tasks: BackgroundTasks,
    recompare: bool = Query(False, description="Run billable LLM comparisons after a baseline change"),
    db: Session = Depends(get_db),
    client: GitHubAppClient = Depends(get_github_client),
) -> dict[str, str | bool]:
    """Sync a selected file; optionally schedule a potentially billable recompare."""
    try:
        result = sync_selected_repository(db, client)
        if result["baselineChanged"] and recompare:
            from app.services.orchestrator import trigger_overall_swot_refresh
            trigger_overall_swot_refresh(background_tasks)
        return result
    except GitHubSyncError as exc:
        raise HTTPException(422 if "No GitHub repository" in str(exc) else 502, str(exc)) from exc


@router.get("/features", dependencies=[Depends(require_admin)])
def synced_features(db: Session = Depends(get_db)) -> dict:
    """Inspect the currently effective SCADABLE baseline used by the LLM."""
    from app.core.baseline import get_scadable_baseline
    store = get_selection_store(db)
    selected = store.get_selected()
    return {
        "source": "github" if selected and selected.last_synced_at else "static",
        "baseline": get_scadable_baseline(db),
    }


@router.post("/webhook", status_code=202)
async def webhook(
    request: Request,
    store: SelectionStore = Depends(get_selection_store),
    x_github_event: str | None = Header(default=None),
    x_github_delivery: str | None = Header(default=None),
    x_hub_signature_256: str | None = Header(default=None),
) -> dict[str, str]:
    # Verify the signed raw bytes before interpreting any user-controlled JSON.
    secret = os.getenv("GITHUB_WEBHOOK_SECRET", "")
    if not secret:
        raise HTTPException(503, "GitHub webhook is not configured")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_WEBHOOK_BYTES:
            raise HTTPException(413, "Webhook payload too large")
    try:
        verify_github_signature(bytes(body), x_hub_signature_256, secret)
        event = parse_main_push(x_github_event, x_github_delivery, bytes(body))
    except InvalidWebhook as exc:
        raise HTTPException(401, str(exc)) from exc

    if event is None:
        return {"status": "ignored"}
    selected = store.get_selected()
    if (
        selected is None
        or event.installation_id != selected.installation_id
        or event.repository_id != selected.repository_id
        or event.branch != selected.default_branch
        or event.repository_full_name.casefold() != selected.full_name.casefold()
    ):
        return {"status": "ignored"}
    was_new = store.record_push(event)
    # An event is recorded/pending, not queued for background processing yet.
    return {"status": "recorded" if was_new else "duplicate"}
