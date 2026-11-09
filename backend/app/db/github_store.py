"""Transactional, restart-safe GitHub repository and feature baseline state.

A single selected repository is intentional: Live SWOT is single-tenant today.
The last *validated* feature snapshot stays in effect if a subsequent sync fails.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.baseline import SCADABLE_BASELINE
from app.db.models import GitHubFeatureState, GitHubRepositorySelection, GitHubWebhookDelivery
from app.db.repository import mark_overall_swot_stale
from app.services.github_features import DEFAULT_FEATURE_PATH, validate_feature_path
from app.services.github_webhooks import MainPushEvent

if TYPE_CHECKING:
    from app.api.routes.github import SelectedRepository


def _features(db: Session, *, create: bool = False, refresh: bool = False) -> GitHubFeatureState | None:
    row = db.get(GitHubFeatureState, 1, populate_existing=refresh)
    if row is None and create:
        row = GitHubFeatureState(id=1, file_path=DEFAULT_FEATURE_PATH)
        db.add(row)
        db.flush()
    return row


def _stale_if_changed(db: Session, old: dict[str, Any], new: dict[str, Any]) -> None:
    if old != new:
        mark_overall_swot_stale(db, commit=False)


class DatabaseGitHubSelectionStore:
    def __init__(self, db: Session):
        self.db = db

    def get_selected(self) -> SelectedRepository | None:
        row = self.db.get(GitHubRepositorySelection, 1)
        if row is None:
            return None
        features = _features(self.db)
        from app.api.routes.github import SelectedRepository
        return SelectedRepository(
            installation_id=row.installation_id,
            repository_id=row.repository_id,
            full_name=row.full_name,
            default_branch=row.default_branch,
            last_commit_sha=row.last_commit_sha,
            sync_status=row.sync_status,
            file_path=features.file_path if features else DEFAULT_FEATURE_PATH,
            revision=features.revision if features else 0,
            file_sha=features.blob_sha if features else None,
            last_synced_at=features.last_synced_at if features else None,
            last_error=features.last_error if features else None,
        )

    def save_selected(self, selection: SelectedRepository) -> None:
        try:
            row = self.db.get(GitHubRepositorySelection, 1, with_for_update=True, populate_existing=True)
            feature = _features(self.db, create=True)
            if row is None:
                row = GitHubRepositorySelection(id=1)
                self.db.add(row)
            # Reselecting any repository deliberately resets its feature baseline,
            # including when choosing the same repository again.
            old = feature.baseline_data or SCADABLE_BASELINE
            feature.file_path = DEFAULT_FEATURE_PATH
            feature.baseline_data = None
            feature.blob_sha = None
            feature.synced_commit_sha = None
            feature.last_synced_at = None
            feature.last_error = None
            feature.revision += 1
            _stale_if_changed(self.db, old, SCADABLE_BASELINE)
            row.installation_id = selection.installation_id
            row.repository_id = selection.repository_id
            row.full_name = selection.full_name
            row.default_branch = selection.default_branch
            row.last_commit_sha = None
            row.sync_status = "not_synced"
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise

    def select_file(self, path: str) -> SelectedRepository:
        path = validate_feature_path(path)
        try:
            selected = self.db.get(GitHubRepositorySelection, 1, with_for_update=True, populate_existing=True)
            if selected is None:
                raise ValueError("Select a GitHub repository before configuring a feature file")
            feature = _features(self.db, create=True)
            if feature.file_path != path:
                old = feature.baseline_data or SCADABLE_BASELINE
                feature.file_path = path
                feature.baseline_data = None
                feature.blob_sha = None
                feature.synced_commit_sha = None
                feature.last_synced_at = None
                _stale_if_changed(self.db, old, SCADABLE_BASELINE)
            feature.last_error = None
            feature.revision += 1
            selected.sync_status = "pending"
            self.db.commit()
            return self.get_selected()
        except Exception:
            self.db.rollback()
            raise

    def record_push(self, event: MainPushEvent) -> bool:
        """Deduplicate deliveries atomically and queue a branch-appropriate sync."""
        try:
            selected = self.db.get(GitHubRepositorySelection, 1, with_for_update=True, populate_existing=True)
            if selected is None or (
                selected.installation_id != event.installation_id
                or selected.repository_id != event.repository_id
                or selected.full_name.casefold() != event.repository_full_name.casefold()
                or selected.default_branch != event.branch
            ):
                self.db.rollback()
                return False
            try:
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
            feature = _features(self.db, create=True)
            feature.revision += 1
            selected.sync_status = "pending"
            self.db.commit()
            return True
        except Exception:
            self.db.rollback()
            raise

    def commit_sync(
        self, *, installation_id: int, repository_id: int, full_name: str,
        file_path: str, expected_commit: str | None, expected_revision: int,
        baseline: dict[str, Any], blob_sha: str,
    ) -> bool:
        """Publish only if no selection/config/newer push superseded this fetch."""
        try:
            selection = self.db.get(GitHubRepositorySelection, 1, with_for_update=True, populate_existing=True)
            feature = _features(self.db, refresh=True)
            if (
                selection is None or feature is None
                or selection.installation_id != installation_id
                or selection.repository_id != repository_id
                or selection.full_name != full_name
                or feature.file_path != file_path
                or selection.last_commit_sha != expected_commit
                or feature.revision != expected_revision
            ):
                self.db.rollback()
                return False
            old = feature.baseline_data or SCADABLE_BASELINE
            _stale_if_changed(self.db, old, baseline)
            feature.baseline_data = baseline
            feature.blob_sha = blob_sha
            feature.synced_commit_sha = expected_commit
            feature.last_synced_at = datetime.now(timezone.utc)
            feature.last_error = None
            feature.revision += 1
            selection.sync_status = "synced"
            self.db.commit()
            return True
        except Exception:
            self.db.rollback()
            raise

    def record_failure(
        self, *, installation_id: int, repository_id: int, file_path: str,
        expected_commit: str | None, expected_revision: int, message: str,
    ) -> bool:
        try:
            selection = self.db.get(GitHubRepositorySelection, 1, with_for_update=True, populate_existing=True)
            feature = _features(self.db, refresh=True)
            if (
                selection is None or feature is None
                or selection.installation_id != installation_id
                or selection.repository_id != repository_id
                or feature.file_path != file_path
                or selection.last_commit_sha != expected_commit
                or feature.revision != expected_revision
            ):
                self.db.rollback()
                return False
            feature.last_error = message[:500]
            feature.revision += 1
            selection.sync_status = "failed"
            self.db.commit()
            return True
        except Exception:
            self.db.rollback()
            raise
