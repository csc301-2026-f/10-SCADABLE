"""Transactional persistence for the single selected SCADABLE GitHub repository.

No GitHub credentials are stored here; only installation/repository identifiers
and webhook delivery receipts. All writes use DB transactions to survive restarts.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import GitHubRepositorySelection, GitHubWebhookDelivery
from app.services.github_webhooks import MainPushEvent

if TYPE_CHECKING:
    from app.api.routes.github import SelectedRepository


class DatabaseGitHubSelectionStore:
    def __init__(self, db: Session):
        self.db = db

    def get_selected(self) -> SelectedRepository | None:
        row = self.db.get(GitHubRepositorySelection, 1)
        if row is None:
            return None
        # Import at call time to avoid a circular import with the API router.
        from app.api.routes.github import SelectedRepository

        return SelectedRepository(
            installation_id=row.installation_id,
            repository_id=row.repository_id,
            full_name=row.full_name,
            default_branch=row.default_branch,
            last_commit_sha=row.last_commit_sha,
            sync_status=row.sync_status,
        )

    def save_selected(self, selection: SelectedRepository) -> None:
        try:
            row = self.db.scalar(
                select(GitHubRepositorySelection)
                .where(GitHubRepositorySelection.id == 1)
                .with_for_update()
            )
            if row is None:
                row = GitHubRepositorySelection(id=1)
                self.db.add(row)
            row.installation_id = selection.installation_id
            row.repository_id = selection.repository_id
            row.full_name = selection.full_name
            row.default_branch = selection.default_branch
            # Selecting a repository resets prior sync state (even on reselect).
            row.last_commit_sha = None
            row.sync_status = "not_synced"
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    def record_push(self, event: MainPushEvent) -> bool:
        """Atomically ignore duplicates and refuse events from stale selections."""
        try:
            selected = self.db.scalar(
                select(GitHubRepositorySelection)
                .where(GitHubRepositorySelection.id == 1)
                .with_for_update()
            )
            if selected is None or (
                selected.installation_id != event.installation_id
                or selected.repository_id != event.repository_id
            ):
                self.db.rollback()
                return False

            try:
                # A savepoint keeps the outer transaction usable on duplicate
                # delivery_id unique-key violations (PostgreSQL safe).
                with self.db.begin_nested():
                    self.db.add(GitHubWebhookDelivery(
                        delivery_id=event.delivery_id,
                        repository_id=event.repository_id,
                        commit_sha=event.commit_sha,
                    ))
                    self.db.flush()
            except IntegrityError:
                self.db.rollback()
                return False

            selected.last_commit_sha = event.commit_sha
            selected.sync_status = "pending"
            self.db.commit()
            return True
        except Exception:
            self.db.rollback()
            raise
