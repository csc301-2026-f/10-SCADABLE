
import re
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import (
    Competitor,
    OverallSwotState,
    ResearchRun,
    ResearchStatus,
    SWOTCategory,
    SWOTEntry,
    SWOTScope,
)


class DuplicateCompetitorError(Exception):
    pass


class NotFoundError(Exception):
    pass


class InvalidStatusTransitionError(Exception):
    pass


_ALLOWED_TRANSITIONS = {
    ResearchStatus.PENDING: {ResearchStatus.RUNNING, ResearchStatus.FAILED},
    ResearchStatus.RUNNING: {ResearchStatus.COMPLETED, ResearchStatus.FAILED},
    ResearchStatus.COMPLETED: set(),
    ResearchStatus.FAILED: {ResearchStatus.PENDING},
}

_OPAQUE_SCHEME = re.compile(
    r"^(mailto|javascript|data|tel|sms|file|blob|about):",
    re.IGNORECASE,
)

_EXPLICIT_SCHEME = re.compile(r"^([a-zA-Z][a-zA-Z0-9+.\-]*)://")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _finish(db: Session, commit: bool, *objects: Any) -> None:
    try:
        if commit:
            db.commit()
        else:
            db.flush()
    except Exception:
        if commit:
            db.rollback()
        raise

    for obj in objects:
        db.refresh(obj)


def clean_url(url: str) -> str:
    raw = url.strip()

    if not raw:
        raise ValueError("Website URL cannot be empty.")

    if _OPAQUE_SCHEME.match(raw):
        raise ValueError("Website URL must use HTTP or HTTPS.")

    match = _EXPLICIT_SCHEME.match(raw)

    if match:
        if match.group(1).lower() not in {"http", "https"}:
            raise ValueError("Website URL must use HTTP or HTTPS.")
    else:
        raw = f"https://{raw}"

    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Invalid website URL.") from exc

    if parsed.scheme.lower() not in {"http", "https"}:
        raise ValueError("Website URL must use HTTP or HTTPS.")

    if not parsed.hostname:
        raise ValueError("Invalid website URL.")

    if parsed.username is not None or parsed.password is not None:
        raise ValueError("Website URL cannot contain credentials.")

    if any(char.isspace() for char in parsed.netloc):
        raise ValueError("Website URL contains invalid whitespace.")

    return raw


def normalize_url(url: str) -> str:
    parsed = urlsplit(clean_url(url))

    host = parsed.hostname.lower().rstrip(".")

    if host.startswith("www."):
        host = host[4:]

    port = parsed.port

    if port is not None and port not in (80, 443):
        host = f"{host}:{port}"

    return host


def _validated_entries(
    entries: Iterable[Mapping[str, Any]],
) -> list[tuple[SWOTCategory, str, str | None]]:
    result = []

    for entry in entries:
        category = SWOTCategory(entry["category"])
        content = entry["content"]

        if not isinstance(content, str) or not content.strip():
            raise ValueError("SWOT entry content must be a non-empty string.")

        source_url = entry.get("source_url")

        if source_url is not None and not isinstance(source_url, str):
            raise ValueError("source_url must be a string or None.")

        result.append((category, content.strip(), source_url))

    return result


def create_competitor(
    db: Session,
    name: str,
    website_url: str,
    commit: bool = True,
) -> Competitor:
    name = name.strip()

    if not name:
        raise ValueError("Competitor name cannot be empty.")

    competitor = Competitor(
        name=name,
        website_url=clean_url(website_url),
        normalized_url=normalize_url(website_url),
    )

    try:
        with db.begin_nested():
            db.add(competitor)
            db.flush()
    except IntegrityError as exc:
        original = exc.orig

        sqlstate = getattr(original, "sqlstate", None)
        constraint = getattr(
            getattr(original, "diag", None),
            "constraint_name",
            None,
        )

        if (
            sqlstate == "23505"
            and constraint == "uq_competitors_normalized_url"
        ):
            raise DuplicateCompetitorError(
                f"Competitor website '{website_url}' already exists."
            ) from exc

        raise

    # Unresearched competitors do not affect a comparison of completed runs.
    _finish(db, commit, competitor)
    return competitor


def get_competitor(
    db: Session,
    competitor_id: int,
) -> Competitor | None:
    return db.get(Competitor, competitor_id)


def get_competitor_by_url(
    db: Session,
    website_url: str,
) -> Competitor | None:
    stmt = select(Competitor).where(
        Competitor.normalized_url == normalize_url(website_url)
    )
    return db.scalars(stmt).first()


def list_competitors(
    db: Session,
    skip: int = 0,
    limit: int = 100,
) -> list[Competitor]:
    if skip < 0 or limit < 1:
        raise ValueError("Invalid pagination parameters.")

    stmt = (
        select(Competitor)
        .order_by(Competitor.created_at.desc(), Competitor.id.desc())
        .offset(skip)
        .limit(limit)
    )

    return list(db.scalars(stmt).all())


def delete_competitor(
    db: Session,
    competitor_id: int,
    commit: bool = True,
) -> bool:
    competitor = db.get(Competitor, competitor_id)

    if competitor is None:
        return False

    had_completed_research = db.scalar(select(ResearchRun.id).where(
        ResearchRun.competitor_id == competitor_id,
        ResearchRun.status == ResearchStatus.COMPLETED,
    ).limit(1)) is not None
    db.delete(competitor)
    if had_completed_research:
        mark_overall_swot_stale(db, commit=False)
    _finish(db, commit)

    return True


def create_research_run(
    db: Session,
    competitor_id: int,
    commit: bool = True,
) -> ResearchRun:
    if db.get(Competitor, competitor_id) is None:
        raise NotFoundError(f"Competitor {competitor_id} does not exist.")

    run = ResearchRun(
        competitor_id=competitor_id,
        status=ResearchStatus.PENDING,
    )

    db.add(run)
    _finish(db, commit, run)

    return run


def get_research_run(
    db: Session,
    run_id: int,
) -> ResearchRun | None:
    return db.get(ResearchRun, run_id)


def get_latest_research_run(
    db: Session,
    competitor_id: int,
) -> ResearchRun | None:
    stmt = (
        select(ResearchRun)
        .where(ResearchRun.competitor_id == competitor_id)
        .order_by(ResearchRun.created_at.desc(), ResearchRun.id.desc())
        .limit(1)
    )

    return db.scalars(stmt).first()


def update_research_run_status(
    db: Session,
    run_id: int,
    status: ResearchStatus | str,
    error_message: str | None = None,
    commit: bool = True,
) -> ResearchRun:
    run = db.get(ResearchRun, run_id, with_for_update=True)

    if run is None:
        raise NotFoundError(f"Research run {run_id} does not exist.")

    new_status = ResearchStatus(status)

    if new_status not in _ALLOWED_TRANSITIONS[run.status]:
        raise InvalidStatusTransitionError(
            f"Cannot change research run {run_id} "
            f"from {run.status.value} to {new_status.value}."
        )

    run.status = new_status
    now = _utcnow()

    if new_status == ResearchStatus.PENDING:
        db.execute(
            delete(SWOTEntry).where(SWOTEntry.research_run_id == run.id)
        )
        db.expire(run, ["swot_entries"])

        run.started_at = None
        run.completed_at = None
        run.error_message = None
        run.scraped_data = None
        run.analysis_result = None

    elif new_status == ResearchStatus.RUNNING:
        run.started_at = now
        run.completed_at = None
        run.error_message = None

    elif new_status == ResearchStatus.COMPLETED:
        run.completed_at = now
        run.error_message = None

    elif new_status == ResearchStatus.FAILED:
        run.completed_at = now
        run.error_message = error_message or "Research run failed."

    _finish(db, commit, run)
    return run


def save_scraped_data(
    db: Session,
    run_id: int,
    scraped_data: dict[str, Any],
    commit: bool = True,
) -> ResearchRun:
    run = db.get(ResearchRun, run_id)

    if run is None:
        raise NotFoundError(f"Research run {run_id} does not exist.")

    run.scraped_data = scraped_data
    _finish(db, commit, run)

    return run


def save_analysis_result(
    db: Session,
    run_id: int,
    analysis_result: dict[str, Any],
    commit: bool = True,
) -> ResearchRun:
    run = db.get(ResearchRun, run_id)

    if run is None:
        raise NotFoundError(f"Research run {run_id} does not exist.")

    run.analysis_result = analysis_result
    _finish(db, commit, run)

    return run


def save_swot_entries(
    db: Session,
    research_run_id: int,
    entries: Iterable[Mapping[str, Any]],
    commit: bool = True,
) -> list[SWOTEntry]:
    run = db.get(ResearchRun, research_run_id)

    if run is None:
        raise NotFoundError(
            f"Research run {research_run_id} does not exist."
        )

    validated = _validated_entries(entries)

    db.execute(
        delete(SWOTEntry).where(
            SWOTEntry.research_run_id == research_run_id
        )
    )
    db.expire(run, ["swot_entries"])

    new_entries = [
        SWOTEntry(
            scope=SWOTScope.COMPETITOR,
            competitor_id=run.competitor_id,
            research_run_id=run.id,
            category=category,
            content=content,
            source_url=source_url,
        )
        for category, content, source_url in validated
    ]

    db.add_all(new_entries)
    _finish(db, commit, *new_entries)

    return new_entries


def get_latest_swot(
    db: Session,
    competitor_id: int,
) -> list[SWOTEntry]:
    latest_run = (
        select(ResearchRun.id)
        .where(
            ResearchRun.competitor_id == competitor_id,
            ResearchRun.status == ResearchStatus.COMPLETED,
        )
        .order_by(
            ResearchRun.completed_at.desc(),
            ResearchRun.id.desc(),
        )
        .limit(1)
    )

    run_id = db.scalars(latest_run).first()

    if run_id is None:
        return []

    stmt = (
        select(SWOTEntry)
        .where(
            SWOTEntry.scope == SWOTScope.COMPETITOR,
            SWOTEntry.research_run_id == run_id,
        )
        .order_by(SWOTEntry.id)
    )

    return list(db.scalars(stmt).all())


def replace_overall_swot(
    db: Session,
    entries: Iterable[Mapping[str, Any]],
    commit: bool = True,
) -> list[SWOTEntry]:
    validated = _validated_entries(entries)

    db.execute(
        delete(SWOTEntry).where(
            SWOTEntry.scope == SWOTScope.OVERALL
        )
    )

    new_entries = [
        SWOTEntry(
            scope=SWOTScope.OVERALL,
            competitor_id=None,
            research_run_id=None,
            category=category,
            content=content,
            source_url=source_url,
        )
        for category, content, source_url in validated
    ]

    db.add_all(new_entries)
    _finish(db, commit, *new_entries)

    return new_entries


def get_overall_swot(db: Session) -> list[SWOTEntry]:
    stmt = (
        select(SWOTEntry)
        .where(SWOTEntry.scope == SWOTScope.OVERALL)
        .order_by(SWOTEntry.id)
    )

    return list(db.scalars(stmt).all())


# A single state row tracks which competitor-data revision produced the overall
# SWOT. The upsert is safe if multiple workers initialize at the same time.
def ensure_overall_swot_state(db: Session) -> None:
    db.execute(text(
        "INSERT INTO overall_swot_state (id, input_revision, published_revision) "
        "VALUES (1, 0, -1) ON CONFLICT (id) DO NOTHING"
    ))


def get_overall_swot_revision(db: Session) -> int:
    """Read, but never advance, the current input revision for a refresh."""
    ensure_overall_swot_state(db)
    # Query a scalar, not a possibly cached ORM state object.
    return db.scalar(select(OverallSwotState.input_revision).where(OverallSwotState.id == 1))


def is_overall_swot_current(db: Session) -> bool:
    state = db.get(OverallSwotState, 1)
    return state is not None and state.published_revision == state.input_revision


def has_completed_research(db: Session) -> bool:
    """An empty/new app is not an outdated comparison."""
    return db.scalar(select(ResearchRun.id).where(
        ResearchRun.status == ResearchStatus.COMPLETED
    ).limit(1)) is not None


def mark_overall_swot_stale(db: Session, commit: bool = True) -> int:
    """Advance revision only when completed competitor input actually changes."""
    ensure_overall_swot_state(db)
    state = db.get(OverallSwotState, 1, with_for_update=True)
    state.input_revision += 1
    revision = state.input_revision
    _finish(db, commit)
    return revision


def publish_overall_swot(
    db: Session,
    entries: Iterable[Mapping[str, Any]],
    expected_revision: int,
) -> bool:
    """Atomically publish findings only if inputs have not changed meanwhile.

    Also commits any pending per-competitor feature score updates. If a newer
    research run or deletion invalidated the revision, discard this comparison.
    """
    ensure_overall_swot_state(db)
    state = db.get(OverallSwotState, 1, with_for_update=True)
    if state.input_revision != expected_revision:
        db.rollback()
        return False

    replace_overall_swot(db, entries, commit=False)
    state.published_revision = expected_revision
    _finish(db, True)
    return True


def save_comparison_scores_if_current(
    db: Session,
    expected_revision: int,
    scores_by_run: Mapping[int, list[dict[str, Any]]],
    overall_entries: Iterable[Mapping[str, Any]] | None = None,
) -> bool:
    """Save only valid score updates on a matching revision.

    If SWOT entries are supplied, publish them and scores atomically; a None
    overall_entries value means an empty/failed SWOT result, so scores may be
    saved but previously published SWOT remains stale.
    """
    ensure_overall_swot_state(db)
    state = db.get(OverallSwotState, 1, with_for_update=True)
    if state.input_revision != expected_revision:
        db.rollback()
        return False

    for run_id, scores in scores_by_run.items():
        run = db.get(ResearchRun, run_id)
        if run is None or not scores:
            continue
        existing = run.analysis_result if isinstance(run.analysis_result, dict) else {}
        run.analysis_result = {**existing, "scores": scores}

    if overall_entries is not None:
        replace_overall_swot(db, overall_entries, commit=False)
        state.published_revision = expected_revision

    _finish(db, True)
    return True
