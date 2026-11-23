"""Durable, bounded recovery of Langfuse costs after worker restarts.

The original five-minute post-run lookup remains unchanged. This worker only
revisits terminal research runs whose cost is absent/incomplete after that
lookup should have finished. Attempts are recorded on the run's existing JSON
field, so retries survive deploys without requiring another schema migration.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.database import SessionLocal
from app.db.models import ResearchRun, ResearchStatus
from app.services.costs import is_cost_tracking_configured
from app.services.orchestrator import record_run_cost

logger = logging.getLogger(__name__)

MAX_RECOVERY_ATTEMPTS = 6
# The original post-run attempts run around T+5 and T+10 minutes. Recovery
# starts later to avoid racing the ordinary in-memory task.
MIN_RUN_AGE = timedelta(minutes=12)
MAX_RUN_AGE = timedelta(days=7)


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def claim_missing_cost_runs(
    db: Session,
    *,
    now: datetime | None = None,
    batch_size: int = 5,
) -> list[int]:
    """Claim bounded retries under DB row locks and commit attempts before I/O.

    On PostgreSQL SKIP LOCKED avoids two workers processing the same run.
    Completed costs and runs outside the recovery window are never retried.
    """
    if batch_size < 1:
        raise ValueError("batch_size must be positive")
    now = _utc(now or datetime.now(timezone.utc))
    ids: list[int] = []
    try:
        candidates = db.scalars(
            select(ResearchRun)
            .where(
                # A cancelled run may have made paid calls before it stopped.
                ResearchRun.status.in_([
                    ResearchStatus.COMPLETED, ResearchStatus.FAILED, ResearchStatus.CANCELLED,
                ]),
                ResearchRun.completed_at <= now - MIN_RUN_AGE,
                ResearchRun.completed_at >= now - MAX_RUN_AGE,
            )
            .order_by(ResearchRun.completed_at.desc(), ResearchRun.id.desc())
            .limit(200)
            .with_for_update(skip_locked=True)
        ).all()
        for run in candidates:
            analysis = run.analysis_result if isinstance(run.analysis_result, dict) else {}
            saved = analysis.get("cost")
            if isinstance(saved, dict) and saved.get("complete") is True:
                continue
            recovery = analysis.get("cost_recovery")
            recovery = recovery if isinstance(recovery, dict) else {}
            attempts = recovery.get("attempts", 0)
            if type(attempts) is not int or attempts < 0:
                attempts = 0
            if attempts >= MAX_RECOVERY_ATTEMPTS:
                continue
            if recovery.get("last_attempt_at"):
                try:
                    last = _utc(datetime.fromisoformat(recovery["last_attempt_at"]))
                except (ValueError, TypeError):
                    last = now
                # After the first attempt: 15, 30, 60, 120, 240 minutes.
                wait = timedelta(minutes=15 * (2 ** max(0, attempts - 1)))
                if now - last < wait:
                    continue
            run.analysis_result = {
                **analysis,
                "cost_recovery": {
                    "attempts": attempts + 1,
                    "last_attempt_at": now.isoformat(),
                },
            }
            ids.append(run.id)
            if len(ids) >= batch_size:
                break
        db.commit()
    except Exception:
        db.rollback()
        raise
    return ids


async def recover_missing_costs_once(
    session_factory: sessionmaker[Session] = SessionLocal,
    *,
    now: datetime | None = None,
) -> list[int]:
    """Attempt eligible cost lookups once; preserve partial telemetry if incomplete."""
    if not is_cost_tracking_configured():
        return []
    with session_factory() as db:
        ids = claim_missing_cost_runs(db, now=now)
    for run_id in ids:
        try:
            with session_factory() as db:
                await record_run_cost(db, run_id, final=True)
        except Exception:
            logger.exception("Cost recovery for research run %s failed", run_id)
    return ids


async def run_cost_recovery_worker(
    session_factory: sessionmaker[Session] = SessionLocal,
    poll_seconds: float | None = None,
) -> None:
    """Background poller; disabled when Langfuse isn't configured."""
    if poll_seconds is None:
        poll_seconds = float(os.getenv("LANGFUSE_COST_RECOVERY_POLL_SECONDS", "300"))
    if poll_seconds <= 0:
        raise ValueError("LANGFUSE_COST_RECOVERY_POLL_SECONDS must be positive")
    while True:
        try:
            await recover_missing_costs_once(session_factory)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Cost recovery tick failed")
        await asyncio.sleep(poll_seconds)
