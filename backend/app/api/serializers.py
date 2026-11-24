"""Convert ORM rows into API response models."""

from collections.abc import Iterable
from datetime import timedelta
import os
from typing import Any
from urllib.parse import urlsplit

from app.api.schemas import (
    ActivityItem,
    AgentRun,
    ApiResearchStatus,
    ChartPoint,
    ChartSeries,
    CompetitorChanges,
    CompetitorProfile,
    CompetitorSummary,
    ConflictClaimSide,
    DataConflict,
    Finding,
    RunStep,
    ScoreChange,
    ScoreSeries,
)
from app.db.models import Competitor, ResearchRun, ResearchStatus, SWOTEntry
from app.services.crawler import CRAWL_FAILED_MESSAGE
from app.services.refresh_schedule import next_refresh_at
from app.services.open_roles import open_roles_from_analysis
import re
import unicodedata

_STATUS_MAP: dict[ResearchStatus, ApiResearchStatus] = {
    ResearchStatus.PENDING: "pending",
    ResearchStatus.RUNNING: "running",
    ResearchStatus.COMPLETED: "success",
    ResearchStatus.FAILED: "failed",
    ResearchStatus.CANCELLED: "cancelled",
}

NOT_RESEARCHED_SUMMARY = "Profile not researched yet."
ACTIVITY_WEEKS = 6

SAFE_ERROR_MESSAGE = "Research failed due to an internal error."

def safe_run_error(message: str | None) -> str:
    if message in (SAFE_ERROR_MESSAGE, CRAWL_FAILED_MESSAGE):
        return message

    if message and re.fullmatch(
        r"Research run timed out after \d+(?:\.\d+)? seconds\.",
        message,
    ):
        return message

    return SAFE_ERROR_MESSAGE


def api_status(status: ResearchStatus) -> ApiResearchStatus:
    return _STATUS_MAP[status]


def source_label(url: str | None) -> str:
    """Short human label for a source URL, e.g. ``vanta.com/pricing``."""
    if not url:
        return "No source"
    parts = urlsplit(url)
    host = (parts.hostname or "").removeprefix("www.")
    path = parts.path.rstrip("/")
    return f"{host}{path}" or url


def finding_from_entry(
    entry: SWOTEntry, competitor: Competitor | None,
    sources: list[dict[str, str | None]] | None = None,
) -> Finding:
    return Finding(
        id=str(entry.id),
        competitor_id=str(competitor.id) if competitor else "",
        competitor_name=competitor.name if competitor else "SCADABLE",
        category=entry.category.value,
        text=entry.content,
        source_url=entry.source_url or "",
        source_label=source_label(entry.source_url),
        detected_at=entry.created_at,
        sources=sources,
    )


def _latest_completed(runs: Iterable[ResearchRun]) -> ResearchRun | None:
    completed = [r for r in runs if r.status == ResearchStatus.COMPLETED]
    if not completed:
        return None
    return max(completed, key=lambda r: (r.completed_at or r.created_at, r.id))


def _analysis(run: ResearchRun | None) -> dict[str, Any]:
    return run.analysis_result if run and isinstance(run.analysis_result, dict) else {}


def _was_analyzed(analysis: dict[str, Any]) -> bool:
    """True if the LLM step saved a result. The cost lookup alone doesn't count:
    it also saves on runs that failed before analysis."""
    return any(key != "cost" for key in analysis)


def competitor_summary(
    competitor: Competitor, latest_run: ResearchRun | None
) -> CompetitorSummary:
    completed = _latest_completed(competitor.research_runs)
    change = None
    if _analysis(completed).get("skipped_llm"):
        change = "No changes detected since last research"
    elif completed is not None:
        change = "Research completed"
    if latest_run is not None and latest_run.status == ResearchStatus.FAILED:
        change = "Latest research run failed"

    return CompetitorSummary(
        id=str(competitor.id),
        name=competitor.name,
        website=competitor.website_url,
        research_status=api_status(latest_run.status) if latest_run else "pending",
        last_researched_at=completed.completed_at if completed else None,
        recent_change_summary=change,
    )


def competitor_profile(
    competitor: Competitor,
    latest_run: ResearchRun | None,
    swot_entries: list[SWOTEntry],
) -> CompetitorProfile:
    summary = competitor_summary(competitor, latest_run)
    completed = _latest_completed(competitor.research_runs)
    analysis = _analysis(completed)
    # Runs that skipped the LLM only point at the run whose analysis they reused.
    if analysis.get("reused_from_run_id"):
        reused = next(
            (r for r in competitor.research_runs if r.id == analysis["reused_from_run_id"]),
            None,
        )
        analysis = {**_analysis(reused), **analysis}
    text = analysis.get("summary")

    activity = [
        ActivityItem(
            id=str(run.id),
            text=_activity_text(run),
            detected_at=run.completed_at or run.started_at or run.created_at,
        )
        for run in sorted(competitor.research_runs, key=lambda r: r.id, reverse=True)[:10]
    ]

    return CompetitorProfile(
        **summary.model_dump(),
        summary=text or NOT_RESEARCHED_SUMMARY,
        open_roles=open_roles_from_analysis(analysis),
        next_refresh_at=(
            next_refresh_at(competitor.research_runs)
            if os.getenv("AUTO_REFRESH_ENABLED", "false").lower() == "true"
            else None
        ),
        findings=[finding_from_entry(e, competitor) for e in swot_entries],
        recent_activity=activity,
        conflicting_data=_conflicts(analysis),
        chart_series=ChartSeries(
            scores=_scores(analysis) or _previous_scores(competitor.research_runs),
            activity_over_time=_activity_over_time(competitor.research_runs),
        ),
    )


def _conflict_side(text: Any, url: Any) -> ConflictClaimSide | None:
    if not isinstance(text, str) or not text.strip():
        return None
    # Only http(s) links reach the frontend's <a href>; anything else is shown unlinked
    safe_url = (
        url.strip()
        if isinstance(url, str) and urlsplit(url.strip()).scheme in ("http", "https")
        else None
    )
    return ConflictClaimSide(
        text=text.strip(), source_label=source_label(safe_url), source_url=safe_url
    )


def _conflicts(analysis: dict[str, Any]) -> list[DataConflict]:
    """LLM-flagged contradictions on the competitor's website, for the profile table.

    The analyzer stores pairs (claim A vs claim B); pairs that share claim A (same
    text and same page) are grouped into one row whose ``conflicts_with`` lists
    every claim B. The same text on different pages stays separate so each row
    keeps its own citation.
    """
    stored = analysis.get("conflicting_data")
    pairs = stored.get("conflicts") if isinstance(stored, dict) else None
    rows: dict[tuple[str, str | None], DataConflict] = {}
    for pair in pairs if isinstance(pairs, list) else []:
        if not isinstance(pair, dict):
            continue
        claim = _conflict_side(pair.get("claim_a"), pair.get("claim_a_page_URL"))
        other = _conflict_side(pair.get("claim_b"), pair.get("claim_b_page_URL"))
        if claim is None or other is None or claim.text == other.text:
            continue
        key = (claim.text, claim.source_url)
        row = rows.get(key)
        if row is None:
            rows[key] = DataConflict(
                id=f"conflict-{len(rows) + 1}", claim=claim, conflicts_with=[other]
            )
        elif other not in row.conflicts_with:
            row.conflicts_with.append(other)
    return list(rows.values())


def _scores(analysis: dict[str, Any]) -> list[ScoreSeries]:
    """LLM feature scores (0-10 competition with SCADABLE) as chart bars.

    SCADABLE ratings are supplied by the comparison LLM, never made up in the API.
    Older comparisons lack ``scadable_score`` and retain a null chart value.
    """
    series = []
    for item in analysis.get("scores") or []:
        if not isinstance(item, dict):
            continue
        label, score = item.get("feature"), item.get("score")
        if not isinstance(label, str) or not label.strip():
            continue
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            continue
        baseline_score = item.get("scadable_score")
        if (isinstance(baseline_score, bool) or not isinstance(baseline_score, (int, float))
                or not 0 <= baseline_score <= 10):
            baseline_score = None
        series.append(
            ScoreSeries(label=label.strip(), competitor=min(max(score, 0), 10), scadable=baseline_score)
        )
    return series


def _previous_scores(runs: Iterable[ResearchRun]) -> list[ScoreSeries]:
    """Scores from the newest completed run that has valid ones.

    Covers a latest run whose comparison failed or returned nothing usable.
    """
    for run in _completed_newest_first(runs):
        if series := _scores(_analysis(run)):
            return series
    return []


def _completed_newest_first(runs: Iterable[ResearchRun]) -> list[ResearchRun]:
    return sorted(
        (r for r in runs if r.status == ResearchStatus.COMPLETED),
        key=lambda r: (r.completed_at or r.created_at, r.id),
        reverse=True,
    )


def _finding_key(entry: SWOTEntry) -> tuple[str, str]:
    """Same category and text (ignoring case, spacing, and a trailing period)."""
    text = " ".join(entry.content.split()).rstrip(".").casefold()
    return entry.category.value, text


def _score_key(label: str, competitor_name: str) -> str:
    """Normalize wording without guessing that two different features are equal.

    Ignore casing, punctuation and the optional 'Company offers/provides ...'
    introduction. Broader paraphrases are deliberately left unmatched rather
    than being reported as features added or removed.
    """
    normalized = " ".join(re.findall(r"\w+", unicodedata.normalize("NFKC", label).casefold()))
    company = " ".join(re.findall(r"\w+", competitor_name.casefold()))
    if company and normalized.startswith(company + " "):
        remainder = normalized[len(company) + 1:]
        for verb in ("offers ", "provides ", "supports ", "includes "):
            if remainder.startswith(verb):
                return remainder[len(verb):]
    return normalized


def _comparable_scores(
    scores: list[ScoreSeries], competitor_name: str
) -> dict[str, ScoreSeries]:
    """Only use unique feature keys; ambiguous duplicate keys can't be paired."""
    by_key: dict[str, ScoreSeries | None] = {}
    for score in scores:
        key = _score_key(score.label, competitor_name)
        if key in by_key:
            by_key[key] = None
        else:
            by_key[key] = score
    return {key: value for key, value in by_key.items() if value is not None}


def competitor_changes(competitor: Competitor) -> CompetitorChanges:
    """Compare the two most recent completed runs' extracted findings and
    confidently matched feature scores. Unmatched LLM labels are not proof
    that a feature was added or removed. Earlier valid scores remain reusable."""
    completed = _completed_newest_first(competitor.research_runs)
    current = completed[0] if completed else None
    previous = completed[1] if len(completed) > 1 else None
    if current is None or previous is None:
        return CompetitorChanges(
            competitor_id=str(competitor.id),
            current_run_id=str(current.id) if current else None,
            previous_run_id=None,
            current_at=current.completed_at if current else None,
            previous_at=None,
            website_changed=False,
            has_changes=False,
        )

    current_entries = {_finding_key(e): e for e in current.swot_entries}
    previous_entries = {_finding_key(e): e for e in previous.swot_entries}
    added = [
        finding_from_entry(e, competitor).model_copy(update={"is_new": True})
        for key, e in current_entries.items() if key not in previous_entries
    ]
    removed = [
        finding_from_entry(e, competitor)
        for key, e in previous_entries.items() if key not in current_entries
    ]

    now = _comparable_scores(_previous_scores(completed), competitor.name)
    before = _comparable_scores(_previous_scores(completed[1:]), competitor.name)
    score_changes = [
        ScoreChange(
            label=series.label,
            previous=before[key].competitor,
            current=series.competitor,
        )
        for key, series in now.items()
        if key in before and before[key].competitor != series.competitor
    ]

    return CompetitorChanges(
        competitor_id=str(competitor.id),
        current_run_id=str(current.id),
        previous_run_id=str(previous.id),
        current_at=current.completed_at,
        previous_at=previous.completed_at,
        website_changed=not _analysis(current).get("skipped_llm"),
        has_changes=bool(added or removed or score_changes),
        added_findings=added,
        removed_findings=removed,
        score_changes=score_changes,
    )


def _activity_over_time(runs: Iterable[ResearchRun]) -> list[ChartPoint]:
    """Weekly count of completed runs that found website changes.

    Covers the ``ACTIVITY_WEEKS`` weeks ending at the latest completed run.
    """
    completed = [
        (r.completed_at or r.created_at).date()
        for r in runs
        if r.status == ResearchStatus.COMPLETED
    ]
    if not completed:
        return []
    changed = [
        (r.completed_at or r.created_at).date()
        for r in runs
        if r.status == ResearchStatus.COMPLETED and not _analysis(r).get("skipped_llm")
    ]
    last = max(completed)
    last_week = last - timedelta(days=last.weekday())
    weeks = [last_week - timedelta(weeks=n) for n in reversed(range(ACTIVITY_WEEKS))]
    return [
        ChartPoint(
            label=f"{week:%b} {week.day}",
            value=sum(week <= day < week + timedelta(weeks=1) for day in changed),
        )
        for week in weeks
    ]


def _activity_text(run: ResearchRun) -> str:
    if run.status == ResearchStatus.COMPLETED:
        if _analysis(run).get("skipped_llm"):
            return "Research run completed; no website changes detected"
        return "Research run completed and findings updated"
    if run.status == ResearchStatus.FAILED:
        return f"Research run failed: {safe_run_error(run.error_message)}"
    if run.status == ResearchStatus.CANCELLED:
        return "Research run cancelled"
    if run.status == ResearchStatus.RUNNING:
        return "Research run in progress"
    return "Research run queued"


def _run_steps(run: ResearchRun) -> list[RunStep]:
    """Derive pipeline steps from what the run has persisted so far."""
    failed = run.status == ResearchStatus.FAILED
    analysis = _analysis(run)
    skipped = bool(analysis.get("skipped_llm"))
    done = run.status == ResearchStatus.COMPLETED

    def step(name: str, finished: bool, reached: bool, ok_text: str = "Success") -> RunStep:
        if finished:
            return RunStep(name=name, result=ok_text, status="success")
        if failed and reached:
            return RunStep(name=name, result="Failed", status="failed")
        if run.status == ResearchStatus.CANCELLED and reached:
            return RunStep(name=name, result="Cancelled", status="cancelled")
        if run.status == ResearchStatus.RUNNING and reached:
            return RunStep(name=name, result="In progress", status="running")
        return RunStep(name=name, result="Not started", status="pending")

    crawled = run.scraped_data is not None
    analyzed = _was_analyzed(analysis)
    return [
        step("Crawl competitor website", crawled, True),
        step(
            "Analyze with LLM",
            analyzed,
            crawled,
            "Skipped (content unchanged)" if skipped else "Success",
        ),
        step("Save SWOT findings", done, analyzed),
    ]


def agent_run(run: ResearchRun, competitor_name: str) -> AgentRun:
    steps = _run_steps(run)
    skipped = bool(_analysis(run).get("skipped_llm"))
    analyzed = _was_analyzed(_analysis(run))
    return AgentRun(
        id=str(run.id),
        competitor_id=str(run.competitor_id),
        competitor_name=competitor_name,
        status=api_status(run.status),
        step_count=len(steps),
        cache_hits=(1 if skipped else 0) if analyzed else None,
        cache_misses=(0 if skipped else 1) if analyzed else None,
        started_at=run.started_at or run.created_at,
        finished_at=run.completed_at,
        steps=steps,
        error_message=safe_run_error(run.error_message) if run.status == ResearchStatus.FAILED else None,
        cost_usd=_cost_usd(run),
    )


def _cost_usd(run: ResearchRun) -> float | None:
    """OpenAI cost looked up from Langfuse a few minutes after the run; None until then."""
    cost = _analysis(run).get("cost")
    usd = cost.get("usd") if isinstance(cost, dict) and cost.get("complete") is True else None
    if isinstance(usd, bool) or not isinstance(usd, (int, float)):
        return None
    return float(usd)
