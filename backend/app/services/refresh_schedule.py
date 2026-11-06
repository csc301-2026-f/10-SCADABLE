"""Pure refresh scheduling policy derived from persisted research history."""

import os
from datetime import datetime, timedelta, timezone

from app.db.models import ResearchRun, ResearchStatus

def _positive_number(name: str, default: str) -> float:
    value = float(os.getenv(name, default))
    if value <= 0:
        raise ValueError(f"{name} must be positive")
    return value


def refresh_interval() -> timedelta:
    return timedelta(days=_positive_number("COMPETITOR_REFRESH_DAYS", "7"))


def retry_interval() -> timedelta:
    return timedelta(minutes=_positive_number("COMPETITOR_REFRESH_RETRY_MINUTES", "60"))


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def next_refresh_at(
    runs: list[ResearchRun],
    interval: timedelta | None = None,
    retry: timedelta | None = None,
) -> datetime | None:
    """Next automatic attempt based on last success or latest failed attempt.

    Returns None while any research is queued/running or before the initial
    completed/failed attempt. Failures retry after a backoff; previously
    completed research remains available during that retry period.
    """
    if any(run.status in (ResearchStatus.PENDING, ResearchStatus.RUNNING) for run in runs):
        return None
    interval = interval if interval is not None else refresh_interval()
    retry = retry if retry is not None else retry_interval()
    successes = [r for r in runs if r.status == ResearchStatus.COMPLETED and r.completed_at]
    failures = [r for r in runs if r.status == ResearchStatus.FAILED and r.completed_at]
    last_success = max((_as_utc(r.completed_at) for r in successes), default=None)
    last_failure = max((_as_utc(r.completed_at) for r in failures), default=None)
    if last_failure is not None and (last_success is None or last_failure > last_success):
        return last_failure + retry
    return last_success + interval if last_success is not None else None

