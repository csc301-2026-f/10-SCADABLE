"""Competitor endpoints: list, view, add, delete, and re-run research."""

from fastapi import APIRouter, BackgroundTasks, Depends, Path, Query, status
from sqlalchemy.orm import Session

from app.api import serializers
from app.api.errors import ConflictError
from app.api.schemas import (
    CompetitorChanges,
    CompetitorProfile,
    CompetitorSummary,
    CreateCompetitorInput,
    CreateCompetitorResult,
    ErrorResponse,
)
from app.db import repository as repo
from app.db.database import get_db
from app.db.models import ResearchStatus
from app.services import orchestrator
from app.services.account_research import get_account_research_key
from app.services.crawler import Crawl4AIAdapter
from app.services.llm import OpenAIAdapter

router = APIRouter(prefix="/competitors", tags=["competitors"])

CompetitorId = Path(ge=1, description="Competitor ID")
_NOT_FOUND = {404: {"model": ErrorResponse}}


def _get_competitor_or_404(db: Session, competitor_id: int):
    competitor = repo.get_competitor(db, competitor_id)
    if competitor is None:
        raise repo.NotFoundError(f"Competitor {competitor_id} not found.")
    return competitor


def _build_profile(db: Session, competitor) -> CompetitorProfile:
    return serializers.competitor_profile(
        competitor,
        repo.get_latest_research_run(db, competitor.id),
        repo.get_latest_swot(db, competitor.id),
    )


@router.get("", response_model=list[CompetitorSummary], summary="List competitors")
def list_competitors(
    skip: int = Query(0, ge=0, description="Number of competitors to skip."),
    limit: int = Query(100, ge=1, le=500, description="Maximum number to return."),
    db: Session = Depends(get_db),
) -> list[CompetitorSummary]:
    """Tracked competitors with their latest research status and last research time."""
    return [
        serializers.competitor_summary(c, repo.get_latest_research_run(db, c.id))
        for c in repo.list_competitors(db, skip=skip, limit=limit)
    ]


@router.get(
    "/{competitor_id}",
    response_model=CompetitorProfile,
    responses=_NOT_FOUND,
    summary="Get a competitor profile",
)
def get_competitor(
    competitor_id: int = CompetitorId,
    db: Session = Depends(get_db),
) -> CompetitorProfile:
    """Full profile: summary, latest SWOT findings, recent activity, open roles,
    conflicting data flagged on the competitor's website, and chart series
    (feature scores vs SCADABLE and weekly activity)."""
    return _build_profile(db, _get_competitor_or_404(db, competitor_id))


@router.get(
    "/{competitor_id}/changes",
    response_model=CompetitorChanges,
    responses=_NOT_FOUND,
    summary="What changed since the last run",
)
def get_competitor_changes(
    competitor_id: int = CompetitorId,
    db: Session = Depends(get_db),
) -> CompetitorChanges:
    """Compare the competitor's two most recent completed research runs: SWOT
    findings that were added or removed, and feature scores that went up or down.

    ``hasChanges`` is false when nothing changed or there is only one completed run
    (``previousRunId`` is then null). ``websiteChanged`` is false when the latest
    run found the website unchanged and reused the previous analysis.
    """
    return serializers.competitor_changes(_get_competitor_or_404(db, competitor_id))


@router.post(
    "",
    response_model=CreateCompetitorResult,
    status_code=status.HTTP_201_CREATED,
    responses={409: {"model": ErrorResponse}, 422: {"model": ErrorResponse}},
    summary="Add a competitor",
)
def create_competitor(
    body: CreateCompetitorInput,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    account_openai_key: str | None = Depends(get_account_research_key),
) -> CreateCompetitorResult:
    """Add a competitor and queue its first research run in the background.

    The first data source is stored as the competitor's website. Additional
    sources are validated but not stored yet (no DB column for them).
    Returns 409 if a competitor with the same website already exists.
    """
    competitor = repo.create_competitor(db, name=body.name, website_url=body.data_sources[0])
    # Account keys are forwarded only by an authenticated Next.js server
    # gateway. Without them, keep the existing deployment-key behavior.
    if account_openai_key is None:
        run = orchestrator.trigger_research_task(competitor.id, background_tasks, db)
    else:
        run = orchestrator.trigger_research_task(
            competitor.id, background_tasks, db,
            crawler=Crawl4AIAdapter(account_openai_key=account_openai_key),
            analyzer=OpenAIAdapter(account_openai_key=account_openai_key),
        )
    return CreateCompetitorResult(
        competitor=serializers.competitor_summary(competitor, run),
        queued_run_id=str(run.id),
    )


@router.delete(
    "/{competitor_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=_NOT_FOUND,
    summary="Delete a competitor",
)
def delete_competitor(
    background_tasks: BackgroundTasks,
    competitor_id: int = CompetitorId,
    db: Session = Depends(get_db),
) -> None:
    """Delete a competitor with its runs and findings, then refresh the overall SWOT
    in the background."""
    if not repo.delete_competitor(db, competitor_id):
        raise repo.NotFoundError(f"Competitor {competitor_id} not found.")
    # Rebuild the overall SWOT so it no longer mentions the deleted competitor.
    orchestrator.trigger_overall_swot_refresh(background_tasks)


@router.post(
    "/{competitor_id}/rerun",
    response_model=CompetitorProfile,
    status_code=status.HTTP_202_ACCEPTED,
    responses={**_NOT_FOUND, 409: {"model": ErrorResponse}},
    summary="Re-run research",
)
def rerun_research(
    background_tasks: BackgroundTasks,
    competitor_id: int = CompetitorId,
    db: Session = Depends(get_db),
    account_openai_key: str | None = Depends(get_account_research_key),
) -> CompetitorProfile:
    """Queue a new research run; returns the profile with status ``pending``.

    Returns 409 if a run for this competitor is already pending or running.
    """
    competitor = _get_competitor_or_404(db, competitor_id)
    latest = repo.get_latest_research_run(db, competitor.id)
    if latest is not None and latest.status in (ResearchStatus.PENDING, ResearchStatus.RUNNING):
        raise ConflictError(f"Research for {competitor.name} is already {latest.status.value}.")

    if account_openai_key is None:
        orchestrator.trigger_research_task(competitor.id, background_tasks, db)
    else:
        orchestrator.trigger_research_task(
            competitor.id, background_tasks, db,
            crawler=Crawl4AIAdapter(account_openai_key=account_openai_key),
            analyzer=OpenAIAdapter(account_openai_key=account_openai_key),
        )
    db.expire(competitor, ["research_runs"])
    return _build_profile(db, competitor)
