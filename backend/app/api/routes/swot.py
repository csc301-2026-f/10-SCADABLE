"""SWOT endpoints."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api import serializers
from app.api.schemas import ErrorResponse, Finding, SwotOverview
from app.db import repository as repo
from app.db.database import get_db

router = APIRouter(prefix="/swot", tags=["swot"])

_BUCKETS = {
    "strength": "strengths",
    "weakness": "weaknesses",
    "opportunity": "opportunities",
    "threat": "threats",
}


def _group(findings: list[Finding], is_stale: bool = False) -> SwotOverview:
    buckets: dict[str, list[Finding]] = {name: [] for name in _BUCKETS.values()}
    for finding in findings:
        buckets[_BUCKETS[finding.category]].append(finding)
    return SwotOverview(
        is_stale=is_stale,
        updated_at=max((f.detected_at for f in findings), default=None),
        **buckets,
    )


def _overview(db: Session) -> SwotOverview:
    """Return only published current overall findings, never stale fallback data."""
    if not repo.is_overall_swot_current(db):
        # An empty new installation has no outdated findings to warn about.
        has_prior_data = bool(repo.get_overall_swot(db) or repo.has_completed_research(db))
        return _group([], is_stale=has_prior_data)
    return _group([
        serializers.finding_from_entry(entry, None)
        for entry in repo.get_overall_swot(db)
    ])


@router.get("", response_model=SwotOverview, responses={404: {"model": ErrorResponse}})
def get_swot(
    competitor_id: int | None = Query(None, alias="competitorId", ge=1),
    db: Session = Depends(get_db),
) -> SwotOverview:
    """SWOT grouped by category.

    With ``competitorId``: that competitor's latest SWOT. Without: the overview.
    """
    if competitor_id is None:
        return _overview(db)

    competitor = repo.get_competitor(db, competitor_id)
    if competitor is None:
        raise repo.NotFoundError(f"Competitor {competitor_id} not found.")
    return _group(
        [
            serializers.finding_from_entry(entry, competitor)
            for entry in repo.get_latest_swot(db, competitor.id)
        ]
    )


@router.get("/overview", response_model=SwotOverview)
def get_swot_overview(db: Session = Depends(get_db)) -> SwotOverview:
    """SCADABLE's overall SWOT (used by the frontend overview page)."""
    return _overview(db)
