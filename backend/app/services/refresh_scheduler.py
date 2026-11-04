"""Database-coordinated periodic competitor refreshes.

Schedule is derived from persisted research-run timestamps (no new DB column).
PostgreSQL row locks make claims safe across multiple API workers/replicas. A
restarted worker recovers abandoned pending/running jobs after the stale limit.
"""

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.database import SessionLocal
from app.db.models import Competitor, ResearchRun, ResearchStatus
from app.db.repository import ResearchAlreadyActiveError, create_research_run
from app.services.orchestrator import execute_research_run, _record_cost_later

from app.services.refresh_schedule import (
    _as_utc, next_refresh_at, refresh_interval, retry_interval,
)

logger = logging.getLogger(__name__)


def claim_due_refreshes(
    db: Session,
    now: datetime | None = None,
    interval: timedelta | None = None,
    retry: timedelta | None = None,
    stale_after: timedelta | None = None,
    batch_size: int = 5,
) -> list[int]:
    """Atomically queue due runs. Returns run IDs after committing claims.

    Locks the competitor rows before checking run status and creating the run;
    manual reruns acquire the same lock via create_research_run. On PostgreSQL
    SKIP LOCKED prevents two workers from claiming the same competitor.
    """
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    now = _as_utc(now or datetime.now(timezone.utc))
    interval = interval if interval is not None else refresh_interval()
    retry = retry if retry is not None else retry_interval()
    stale_after = stale_after if stale_after is not None else timedelta(minutes=30)
    claimed: list[int] = []
    try:
        # Lock first, then check freshness inside the same transaction.
        competitors = db.scalars(
            select(Competitor).order_by(Competitor.id).with_for_update(skip_locked=True)
        ).all()
        for competitor in competitors:
            runs = list(db.scalars(
                select(ResearchRun).where(ResearchRun.competitor_id == competitor.id)
            ).all())
            active = [r for r in runs if r.status in (ResearchStatus.PENDING, ResearchStatus.RUNNING)]
            if active:
                # A worker can be terminated by a deployment or process restart.
                # Reap abandoned jobs only after a generous safety window.
                if any(now - _as_utc(r.started_at or r.created_at) < stale_after for r in active):
                    continue
                for run in active:
                    run.status = ResearchStatus.FAILED
                    run.completed_at = now
                    run.error_message = "Research run interrupted; retry scheduled."
                db.flush()
                # The recovered run gets the same retry backoff as any failure.
                continue
            due = next_refresh_at(runs, interval=interval, retry=retry)
            if due is None or due > now:
                continue
            try:
                new_run = create_research_run(db, competitor.id, commit=False)
            except ResearchAlreadyActiveError:
                continue
            claimed.append(new_run.id)
            if len(claimed) >= batch_size:
                break
        db.commit()
    except Exception:
        db.rollback()
        raise
    return claimed


async def _run_scheduled_research(session_factory: sessionmaker[Session], run_id: int) -> None:
    try:
        with session_factory() as db:
            await execute_research_run(db, run_id)
        await _record_cost_later(session_factory, run_id)
    except Exception:
        logger.exception("Scheduled refresh run %s failed unexpectedly", run_id)
        # The next scheduler pass can recover this job if it remains active.


async def run_scheduler(
    session_factory: sessionmaker[Session] = SessionLocal,
    poll_seconds: float = 60,
) -> None:
    """Polling loop; activated explicitly via AUTO_REFRESH_ENABLED=true."""
    workers: set[asyncio.Task] = set()
    try:
        while True:
            try:
                with session_factory() as db:
                    run_ids = claim_due_refreshes(db)
                for run_id in run_ids:
                    task = asyncio.create_task(_run_scheduled_research(session_factory, run_id))
                    workers.add(task)
                    task.add_done_callback(workers.discard)
            except Exception:
                logger.exception("Competitor refresh scheduler tick failed; retrying")
            await asyncio.sleep(poll_seconds)
    finally:
        for task in workers:
            task.cancel()
        if workers:
            await asyncio.gather(*workers, return_exceptions=True)
