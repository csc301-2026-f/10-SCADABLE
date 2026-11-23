"""Research run endpoints: run history, run detail, and cancelling a run."""

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from app.api import serializers
from app.api.errors import ConflictError
from app.api.schemas import AgentRun, ErrorResponse
from app.db import repository as repo
from app.db.database import get_db
from app.db.models import ResearchRun, ResearchStatus
from app.services import orchestrator

router = APIRouter(prefix="/runs", tags=["runs"])


def query_runs(db: Session, competitor_id: int | None, limit: int) -> list[ResearchRun]:
    """Most recent runs first, optionally for one competitor (read-only)."""
    stmt = (
        select(ResearchRun)
        .options(selectinload(ResearchRun.competitor))
        .order_by(ResearchRun.created_at.desc(), ResearchRun.id.desc())
        .limit(limit)
    )
    if competitor_id is not None:
        stmt = stmt.where(ResearchRun.competitor_id == competitor_id)
    return list(db.scalars(stmt).all())


@router.get("", response_model=list[AgentRun], summary="List research runs")
def list_runs(
    competitor_id: int | None = Query(
        None, alias="competitorId", ge=1, description="Only runs for this competitor."
    ),
    limit: int = Query(100, ge=1, le=500, description="Maximum number to return."),
    db: Session = Depends(get_db),
) -> list[AgentRun]:
    """Research run history, newest first, with pipeline steps, status, and cost."""
    return [
        serializers.agent_run(run, run.competitor.name)
        for run in query_runs(db, competitor_id, limit)
    ]


@router.get(
    "/{run_id}",
    response_model=AgentRun,
    responses={404: {"model": ErrorResponse}},
    summary="Get a research run",
)
def get_run(
    run_id: int = Path(ge=1, description="Research run ID"),
    db: Session = Depends(get_db),
) -> AgentRun:
    """One research run: crawl, analyze, and save steps, status, cost, and any error."""
    run =repo.get_research_run(db, run_id)
    if run is None:
        raise repo.NotFoundError(f"Research run {run_id} not found.")
    return serializers.agent_run(run, run.competitor.name)


@router.post(
    "/{run_id}/cancel",
    response_model=AgentRun,
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
    summary="Cancel a research run",
)
def cancel_run(
    run_id: int = Path(ge=1, description="Research run ID"),
    db: Session = Depends(get_db),
) -> AgentRun:
    """Stop a queued or running research run.

    Earlier results stay, and the cost of calls already made is still recorded.
    A run stuck as running after a server restart can be cancelled too.
    Returns 409 if the run already finished.
    """
    run = repo.get_research_run(db, run_id)
    if run is None:
        raise repo.NotFoundError(f"Research run {run_id} not found.")
    if run.status not in (ResearchStatus.PENDING, ResearchStatus.RUNNING):
        raise ConflictError(f"Research run {run_id} already finished.")
    # Saved before stopping the job, which reads it to know a user cancelled.
    run = repo.update_research_run_status(db, run_id, ResearchStatus.CANCELLED)
    orchestrator.request_run_cancel(run_id)
    return serializers.agent_run(run, run.competitor.name)
