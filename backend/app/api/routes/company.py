"""SCADABLE company profile, including last validated GitHub sync metadata."""

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.schemas import CompanyProfile, GithubStatus
from app.core.baseline import get_scadable_baseline
from app.db.database import get_db

router = APIRouter(prefix="/company", tags=["company"])


@router.get("", response_model=CompanyProfile)
def get_company_profile(db: Session = Depends(get_db)) -> CompanyProfile:
    baseline = get_scadable_baseline(db)
    frameworks = baseline.get("supported_frameworks", [])
    offerings = list(baseline.get("core_capabilities", []))
    if frameworks:
        offerings.insert(0, "Frameworks: " + ", ".join(frameworks))
    github = GithubStatus(
        connected=False,
        repository=None,
        tracked_file=None,
        status_label="Static profile (GitHub sync not connected yet)",
        last_synced_at=None,
        recent_change=None,
    )
    if hasattr(db, "get"):
        from app.db.github_store import DatabaseGitHubSelectionStore
        selected = DatabaseGitHubSelectionStore(db).get_selected()
        if selected is not None:
            labels = {
                "not_synced": "GitHub repository selected; not synced yet",
                "pending": "GitHub update pending; using last valid profile",
                "synced": "GitHub features synced",
                "failed": "GitHub sync failed; using last valid profile",
            }
            github = GithubStatus(
                connected=selected.last_synced_at is not None,
                repository=selected.full_name,
                tracked_file=selected.file_path,
                status_label=labels.get(selected.sync_status, "GitHub integration configured"),
                last_synced_at=selected.last_synced_at,
                recent_change=(
                    f"Feature file revision {selected.file_sha[:7]}"
                    if selected.file_sha else None
                ),
            )
    return CompanyProfile(
        offerings=offerings,
        positioning_notes=list(baseline.get("differentiators", [])),
        github=github,
    )
