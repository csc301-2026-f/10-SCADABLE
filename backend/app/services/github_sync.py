"""Fetch a GitHub App-approved manifest and publish a validated SCADABLE baseline.

Sync is side-effect-free until validation succeeds. Repository selection and
commit checks protect against a slow fetch overwriting a newer push. A durable
pending state supports webhook processing after API restarts.
"""

from __future__ import annotations

import asyncio
import logging
import os

from sqlalchemy.orm import Session

from app.db.database import SessionLocal
from app.db.github_store import DatabaseGitHubSelectionStore
from app.services.github_client import GitHubAppClient, GitHubClientError
from app.services.github_features import FeatureFileError, parse_feature_manifest

logger = logging.getLogger(__name__)


class GitHubSyncError(RuntimeError):
    pass


def sync_selected_repository(db: Session, client: GitHubAppClient) -> dict[str, str | bool]:
    """Sync the selected file at the recorded push SHA, or at default branch.

    Can be called by the admin /sync endpoint or the optional polling worker.
    A failed sync preserves the last valid feature snapshot. No LLM API calls.
    """
    from app.core.baseline import get_scadable_baseline
    store = DatabaseGitHubSelectionStore(db)
    selected = store.get_selected()
    prior_baseline = get_scadable_baseline(db)
    if selected is None:
        raise GitHubSyncError("No GitHub repository selected")
    ref = selected.last_commit_sha or selected.default_branch
    try:
        metadata = client.read_repository_file_with_metadata(
            selected.installation_id, selected.full_name, selected.file_path, ref=ref
        )
        baseline = parse_feature_manifest(metadata.text)
    except (GitHubClientError, FeatureFileError, ValueError) as exc:
        error = str(exc)
        # Store only our own bounded sanitized error messages, never raw GitHub API bodies.
        store.record_failure(
            installation_id=selected.installation_id,
            repository_id=selected.repository_id,
            file_path=selected.file_path,
            expected_commit=selected.last_commit_sha,
            expected_revision=selected.revision,
            message=error,
        )
        raise GitHubSyncError(error) from exc
    published = store.commit_sync(
        installation_id=selected.installation_id,
        repository_id=selected.repository_id,
        full_name=selected.full_name,
        file_path=selected.file_path,
        expected_commit=selected.last_commit_sha,
        expected_revision=selected.revision,
        baseline=baseline,
        blob_sha=metadata.blob_sha,
    )
    if not published:
        raise GitHubSyncError("GitHub selection or commit changed during sync; retry")
    return {"status": "synced", "fileSha": metadata.blob_sha, "filePath": selected.file_path,
            "baselineChanged": prior_baseline != baseline}


def sync_pending_once() -> None:
    """Process one durable pending sync; safe to retry after a process restart."""
    with SessionLocal() as db:
        selected = DatabaseGitHubSelectionStore(db).get_selected()
        if selected is None or selected.sync_status != "pending":
            return
        try:
            with GitHubAppClient.from_env() as client:
                result = sync_selected_repository(db, client)
                if result["baselineChanged"] and os.getenv("GITHUB_RECOMPARE_ON_SYNC", "false").lower() == "true":
                    # Explicit opt-in: LLM comparisons can incur OpenAI charges.
                    from app.services.llm import get_default_analyzer
                    from app.services.orchestrator import refresh_overall_swot
                    asyncio.run(refresh_overall_swot(db, get_default_analyzer()))
        except (GitHubSyncError, GitHubClientError):
            logger.warning("GitHub feature sync failed; see /github/status for details")
        except Exception:
            logger.exception("Unexpected GitHub feature sync failure")


async def run_github_sync_worker(poll_seconds: float = 60.0) -> None:
    if poll_seconds <= 0:
        raise ValueError("GITHUB_SYNC_POLL_SECONDS must be positive")
    while True:
        try:
            await asyncio.to_thread(sync_pending_once)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("GitHub sync polling iteration failed")
        await asyncio.sleep(poll_seconds)
