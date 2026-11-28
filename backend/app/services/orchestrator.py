"""Research workflow orchestrator and asynchronous task runner.

Coordinates competitor research and the incremental SCADABLE comparison:
Add competitor -> crawl -> analyze -> store competitor data -> compare only
changed competitor -> aggregate versioned per-competitor SWOT contributions.
"""

import asyncio
import hashlib
import json
import logging
import os
import re
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from fastapi import BackgroundTasks
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.core.baseline import get_scadable_baseline
from app.db.database import SessionLocal
from app.db.models import (
    Competitor,
    ResearchRun,
    ResearchStatus,
    SWOTCategory,
    SWOTEntry,
)
from app.db.repository import (
    InvalidStatusTransitionError,
    NotFoundError,
    create_research_run,
    get_competitor,
    get_latest_swot,
    get_overall_swot,
    get_overall_swot_revision,
    get_overall_swot_contributions,
    is_overall_swot_current,
    mark_overall_swot_stale,
    publish_overall_swot,
    save_comparison_scores_if_current,
    get_research_run,
    list_competitors,
    save_analysis_result,
    save_scraped_data,
    save_swot_entries,
    update_research_run_status,
)
from app.services.costs import (
    fetch_decisions_cost,
    fetch_session_cost,
    is_cost_tracking_configured,
    run_session_id,
    run_trace_prefix,
)
from app.services.crawler import (
    CRAWL_FAILED_MESSAGE,
    CrawlerProtocol,
    CrawlFailedError,
    get_default_crawler,
)
from app.services.diff import has_content_changed
from app.services.llm import AnalyzerProtocol, get_default_analyzer

logger = logging.getLogger(__name__)

# Langfuse can take a few minutes to ingest a call's cost
COST_LOOKUP_DELAY_SECONDS = float(os.getenv("LANGFUSE_COST_DELAY_SECONDS", "300"))
COST_LOOKUP_ATTEMPTS = 2
# Increment whenever LLM prompts, extraction rules or analysis-input processing change.
# This belongs on the saved analysis, not the page-content hash.
ANALYSIS_POLICY_VERSION = os.getenv("ANALYSIS_POLICY_VERSION", "2026-11-06-scraper-v2")
# Bump when the overall comparison prompt or its interpretation changes. This
# invalidates saved SWOT contributions without recrawling competitor websites.
COMPARISON_POLICY_VERSION = "2026-11-23-weakness-evidence-v1"
# Extraction and comparison each make up to OPENAI_CALL_LIMIT answer and review
# calls (6 by default), so they need far longer than a crawl. Users can cancel.
DEFAULT_LLM_STEP_TIMEOUT_SECONDS = 300.0


class StepTimeout(TimeoutError):
    """A research step ran past its own limit, which may differ from the crawl's."""

    def __init__(self, seconds: float) -> None:
        super().__init__(f"Step timed out after {seconds:g} seconds")
        self.seconds = seconds


def _llm_step_timeout(env_name: str, timeout_seconds: float) -> float:
    """Seconds an AI step may take: the env value, else at least the default."""
    value = float(os.getenv(env_name, str(max(timeout_seconds, DEFAULT_LLM_STEP_TIMEOUT_SECONDS))))
    if value <= 0:
        raise ValueError(f"{env_name} must be positive")
    return value



def _get_previous_completed_run(
    db: Session,
    competitor_id: int,
    current_run_id: int,
) -> ResearchRun | None:
    """Retrieve the most recent successful run prior to the current run."""
    stmt = (
        select(ResearchRun)
        .where(
            ResearchRun.competitor_id == competitor_id,
            ResearchRun.id != current_run_id,
            ResearchRun.status == ResearchStatus.COMPLETED,
        )
        .order_by(ResearchRun.completed_at.desc(), ResearchRun.id.desc())
        .limit(1)
    )
    return db.scalars(stmt).first()


def _get_latest_completed_run(db: Session, competitor_id: int) -> ResearchRun | None:
    """Retrieve the competitor's most recent successful run."""
    stmt = (
        select(ResearchRun)
        .where(
            ResearchRun.competitor_id == competitor_id,
            ResearchRun.status == ResearchStatus.COMPLETED,
        )
        .order_by(ResearchRun.completed_at.desc(), ResearchRun.id.desc())
        .limit(1)
    )
    return db.scalars(stmt).first()


def _score_feature_key(feature: str) -> str:
    """Normalize feature names for high-confidence SCADABLE score pairing."""
    return " ".join(re.findall(r"[a-z0-9]+", feature.casefold()))


def _matching_scadable_score(feature: str, baseline: dict[str, float]) -> float | None:
    """Pair a competitor feature only with the same or very similar baseline feature.

    Comparison scores are per-company, not directly side-by-side pairs. Do not
    assign an unrelated SCADABLE score just to populate the chart.
    """
    key = _score_feature_key(feature)
    if key in baseline:
        return baseline[key]
    # Very conservative word containment: e.g. "Evidence collection" matches
    # "Continuous automated evidence collection from cloud providers".
    words = set(key.split())
    matches = []
    for baseline_key, value in baseline.items():
        baseline_words = set(baseline_key.split())
        overlap = words & baseline_words
        shorter = min(len(words), len(baseline_words))
        if shorter >= 2 and len(overlap) >= 2 and len(overlap) / shorter >= 0.8:
            matches.append(value)
    return matches[0] if len(matches) == 1 else None


def _scores_by_company(
    raw_scores: Any, feature_pairs: Any = None,
) -> dict[str, list[dict[str, Any]]]:
    """Convert independent company scores into chart-ready competitor score rows."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    if not isinstance(raw_scores, list):
        return grouped
    scadable: dict[str, float] = {}
    for item in raw_scores:
        if not isinstance(item, dict):
            continue
        name, feature, score = item.get("company_name"), item.get("feature"), item.get("score")
        if (isinstance(name, str) and name.casefold() == "scadable"
                and isinstance(feature, str) and feature.strip()
                and type(score) in (int, float) and 0 <= score <= 10):
            scadable[_score_feature_key(feature)] = score
    # Explicit LLM pairings take precedence; a null pairing intentionally
    # leaves the baseline score empty rather than inventing an equivalence.
    pairs = {}
    if isinstance(feature_pairs, list):
        for pair in feature_pairs:
            if not isinstance(pair, dict):
                continue
            company, competitor_feature = pair.get("company_name"), pair.get("competitor_feature")
            if isinstance(company, str) and isinstance(competitor_feature, str):
                pairs[(company, competitor_feature)] = pair.get("scadable_feature")
    for item in raw_scores:
        if not isinstance(item, dict):
            continue
        name, feature, score = item.get("company_name"), item.get("feature"), item.get("score")
        if (not isinstance(name, str) or name.casefold() == "scadable"
                or not isinstance(feature, str) or not feature.strip()
                or type(score) not in (int, float) or not 0 <= score <= 10):
            continue
        row = {"feature": feature.strip(), "score": score}
        # Preserve explicit paired scores from old test/mock adapters.
        explicit = item.get("scadable_score")
        if type(explicit) in (int, float) and 0 <= explicit <= 10:
            row["scadable_score"] = explicit
        elif isinstance(feature_pairs, list):
            selected = pairs.get((name, feature))
            if isinstance(selected, str) and _score_feature_key(selected) in scadable:
                row["scadable_score"] = scadable[_score_feature_key(selected)]
        else:
            # Legacy comparison/mock results lack explicit pairings.
            match = _matching_scadable_score(feature, scadable)
            if match is not None:
                row["scadable_score"] = match
        grouped.setdefault(name, []).append(row)
    return grouped


def _baseline_digest(baseline: dict[str, Any]) -> str:
    """Fingerprint the active baseline AND the comparison prompt policy.

    Kept in the existing digest column: old saved snapshots automatically
    become ineligible for reuse when the policy changes, without a migration.
    """
    canonical = json.dumps(
        {"baseline": baseline, "comparison_policy_version": COMPARISON_POLICY_VERSION},
        sort_keys=True, ensure_ascii=False, separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _combine_contributions(
    contributions: dict[int, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    """Stable, conservative aggregation; never fabricate or rewrite LLM claims.

    Identical content within a SWOT category is shown once; meaning-based
    merging stays with the LLM team to avoid incorrectly merging distinct facts.
    """
    result = []
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for competitor_id in sorted(contributions):
        for entry in contributions[competitor_id]:
            if not isinstance(entry, dict):
                continue
            category, content = entry.get("category"), entry.get("content")
            if category not in {item.value for item in SWOTCategory} or not isinstance(content, str) or not content.strip():
                continue
            key = (category, " ".join(content.split()).casefold())
            urls = entry.get("source_urls")
            if not isinstance(urls, list):
                urls = [entry.get("source_url")]
            if key not in by_key:
                item = {
                    "category": category,
                    "content": content.strip(),
                    "source_url": entry.get("source_url"),
                    "source_urls": [],
                    "uses_scadable_baseline": category in {"strength", "weakness"},
                }
                by_key[key] = item
                result.append(item)
            item = by_key[key]
            item["uses_scadable_baseline"] |= bool(entry.get("uses_scadable_baseline"))
            for url in urls:
                if isinstance(url, str) and url and url not in item["source_urls"]:
                    item["source_urls"].append(url)
    return result


async def refresh_overall_swot(
    db: Session,
    analyzer: AnalyzerProtocol,
    timeout_seconds: float = 90.0,
    only_if_missing: bool = False,
    trace_prefix: str | None = None,
    session_id: str | None = None,
    comparison_calls: list[str] | None = None,
) -> None:
    """Incrementally maintain SCADABLE-relative SWOT per researched competitor.

    session_id groups the comparison's paid calls under the run that started it.
    The paid calls each comparison reports are added to comparison_calls, so the
    run's cost is not treated as complete before those calls are priced.

    A newly changed competitor triggers an LLM comparison ONLY for itself.
    Existing snapshots for unrelated competitors are reused byte-for-byte.
    Initial rollout backfills missing snapshots once (one company at a time).
    GitHub baseline and comparison-prompt changes invalidate snapshots through
    their digest, without recrawling existing competitor research.
    After successful comparisons, scores, snapshots and the overview publish
    atomically under the existing input-revision guard.
    """
    try:
        if only_if_missing and is_overall_swot_current(db) and get_overall_swot(db):
            # A published overview may still use an older LLM prompt policy.
            # Do not let this short circuit prevent policy re-comparisons.
            active_digest = _baseline_digest(get_scadable_baseline(db))
            saved = get_overall_swot_contributions(db)
            if saved and all(row.baseline_digest == active_digest for row in saved.values()):
                return
        revision = get_overall_swot_revision(db)
        baseline = get_scadable_baseline(db)
        digest = _baseline_digest(baseline)
        snapshots = get_overall_swot_contributions(db)
        profiles: list[tuple[Competitor, ResearchRun, dict[str, Any]]] = []

        for competitor in list_competitors(db, limit=500):
            latest_run = _get_latest_completed_run(db, competitor.id)
            if latest_run is None:
                continue
            claims = [
                {"category": entry.category.value, "content": entry.content, "source_url": entry.source_url}
                for entry in get_latest_swot(db, competitor.id)
                if entry.category in (SWOTCategory.STRENGTH, SWOTCategory.WEAKNESS)
            ]
            analysis = latest_run.analysis_result if isinstance(latest_run.analysis_result, dict) else {}
            features = analysis.get("features", [])
            extracted = analysis.get("extracted_information")
            if not claims and not features and not (
                isinstance(extracted, dict) and extracted.get("claims_cited")
            ):
                continue
            profiles.append((competitor, latest_run, {
                "company_name": competitor.name,
                "website_url": competitor.website_url,
                "summary": analysis.get("summary", ""),
                "features": features,
                "claims": claims,
                "extracted_information": extracted,
            }))

        if not profiles:
            publish_overall_swot(db, [], revision)
            return

        comparison_timeout = _llm_step_timeout("OVERALL_SWOT_TIMEOUT_SECONDS", timeout_seconds)

        contributions: dict[int, list[dict[str, Any]]] = {}
        updates: dict[int, dict[str, Any]] = {}
        scores_by_run: dict[int, list[dict[str, Any]]] = {}
        competitor_swots_by_run: dict[int, list[dict[str, Any]]] = {}

        for competitor, latest_run, profile in profiles:
            snapshot = snapshots.get(competitor.id)
            if snapshot is not None and snapshot.baseline_digest == digest:
                if snapshot.research_run_id == latest_run.id:
                    contributions[competitor.id] = snapshot.entries
                    continue
                # An unchanged crawl is explicitly marked as reused. Move its
                # comparison forward without a new paid LLM request.
                analysis = latest_run.analysis_result if isinstance(latest_run.analysis_result, dict) else {}
                if analysis.get("skipped_llm") and isinstance(snapshot.entries, list):
                    contributions[competitor.id] = snapshot.entries
                    updates[competitor.id] = {
                        "research_run_id": latest_run.id, "baseline_digest": digest,
                        "entries": snapshot.entries,
                    }
                    continue

            logger.info("Comparing SCADABLE against %s only (timeout=%ss)", competitor.name, comparison_timeout)
            comparison = await asyncio.wait_for(
                analyzer.compare(
                    baseline, [profile], trace_prefix=trace_prefix, session_id=session_id
                ),
                timeout=comparison_timeout,
            )
            if comparison_calls is not None and isinstance(comparison, dict):
                comparison_calls.extend(
                    name for name in comparison.get("llm_calls", []) if isinstance(name, str)
                )
            grouped = _scores_by_company(
                comparison.get("scores") if isinstance(comparison, dict) else None,
                comparison.get("feature_pairs") if isinstance(comparison, dict) else None,
            )
            if grouped.get(competitor.name):
                scores_by_run[latest_run.id] = grouped[competitor.name]
            raw_entries = comparison.get("swot_entries") if isinstance(comparison, dict) else None
            if not isinstance(raw_entries, list) or not raw_entries:
                # Do not overwrite a valid snapshot with a blank/failed answer.
                # Leave the overview stale until comparison can succeed.
                logger.warning("No overall SWOT findings for %s; retaining old snapshot", competitor.name)
                if scores_by_run:
                    save_comparison_scores_if_current(db, revision, scores_by_run)
                return

            entries = []
            for entry in raw_entries:
                if not isinstance(entry, dict):
                    raise ValueError("LLM SWOT entry must be an object")
                category, content = entry.get("category"), entry.get("content")
                if category not in {item.value for item in SWOTCategory} or not isinstance(content, str) or not content.strip():
                    raise ValueError("LLM SWOT entry category or content is invalid")
                source = entry.get("source_url")
                if source is not None and not isinstance(source, str):
                    raise ValueError("LLM SWOT source_url must be text or null")
                source_urls = entry.get("source_urls")
                if source_urls is None:
                    source_urls = [source] if source else []
                if (not isinstance(source_urls, list)
                        or any(not isinstance(url, str) or not url.startswith(("https://", "http://"))
                               for url in source_urls)):
                    raise ValueError("LLM SWOT source_urls must be HTTP(S) URLs")
                entries.append({
                    "category": category, "content": content.strip(), "source_url": source,
                    "source_urls": list(dict.fromkeys(source_urls)),
                    "uses_scadable_baseline": bool(entry.get("uses_scadable_baseline"))
                    or category in {"strength", "weakness"},
                })
            contributions[competitor.id] = entries
            updates[competitor.id] = {
                "research_run_id": latest_run.id,
                "baseline_digest": digest,
                "entries": entries,
            }

            blocks = comparison.get("competitor_swots")
            if isinstance(blocks, list):
                for block in blocks:
                    if isinstance(block, dict) and block.get("company_name") == competitor.name and isinstance(block.get("swot_entries"), list):
                        competitor_swots_by_run[latest_run.id] = block["swot_entries"]

        aggregated = _combine_contributions(contributions)
        if not aggregated:
            logger.warning("No valid aggregate SWOT findings; prior overview remains stale")
            return
        if not updates and is_overall_swot_current(db):
            return
        if not save_comparison_scores_if_current(
            db, revision, scores_by_run, aggregated,
            competitor_swots_by_run=competitor_swots_by_run,
            contribution_updates=updates,
        ):
            logger.warning("Overall comparison superseded by newer competitor data")
    except Exception:
        db.rollback()
        logger.exception("Overall SWOT comparison failed; prior results remain stored and stale")


# Research jobs running in this process, by run ID, so a user can stop one.
# The backend runs as a single process, so every job is registered here.
_active_runs: dict[int, tuple[asyncio.AbstractEventLoop, asyncio.Task]] = {}


def request_run_cancel(run_id: int) -> bool:
    """Stop the run's job if this process is running it. Safe to call from any thread.

    Mark the run CANCELLED first: the job reads that status to tell a user's
    cancel apart from a server shutdown. Returns whether a job was found.
    """
    entry = _active_runs.get(run_id)
    if entry is None:
        return False
    loop, _ = entry
    loop.call_soon_threadsafe(_cancel_active_run, run_id)
    return True


def _cancel_active_run(run_id: int) -> None:
    # Look the job up again on its own loop. If the run finished in the
    # meantime, cancelling the task would stop its cost lookup instead.
    entry = _active_runs.get(run_id)
    if entry is not None:
        entry[1].cancel()


def _reload_run(db: Session, run_id: int) -> ResearchRun | None:
    """The run as committed by any session, e.g. after the cancel endpoint changed it."""
    db.rollback()
    db.expire_all()
    return get_research_run(db, run_id)


async def execute_research_run(
    db: Session,
    run_id: int,
    crawler: CrawlerProtocol | None = None,
    analyzer: AnalyzerProtocol | None = None,
    timeout_seconds: float = 90.0,
) -> ResearchRun:
    """Run the research pipeline for one run, letting a user stop it midway.

    While the pipeline runs, its job is registered so ``request_run_cancel`` can
    stop it. A stopped run keeps its CANCELLED status and earlier results stay
    untouched. Callers still record the cost of the calls made before the stop.
    """
    task = asyncio.current_task()
    if task is not None:
        _active_runs[run_id] = (asyncio.get_running_loop(), task)
    try:
        return await _run_research_steps(db, run_id, crawler, analyzer, timeout_seconds)
    except asyncio.CancelledError:
        run = _reload_run(db, run_id)
        if run is None or run.status != ResearchStatus.CANCELLED:
            raise  # A server shutdown, not a user's cancel
        if task is not None:
            task.uncancel()
        logger.info("Research run %s cancelled by user", run_id)
        return run
    except InvalidStatusTransitionError:
        # The user cancelled before the run started or while a step was
        # finishing, so it can no longer become RUNNING, COMPLETED or FAILED.
        run = _reload_run(db, run_id)
        if run is None or run.status != ResearchStatus.CANCELLED:
            raise
        logger.info("Research run %s cancelled by user", run_id)
        return run
    finally:
        entry = _active_runs.get(run_id)
        if entry is not None and entry[1] is task:
            del _active_runs[run_id]


async def _crawl_and_save_snapshot(
    db: Session, run_id: int, competitor: Competitor,
    crawler: CrawlerProtocol, timeout_seconds: float,
) -> dict[str, Any]:
    """Crawl with the per-step timeout and persist the exact snapshot for audit."""
    # Step 2: Execute web crawling with timeout protection
    logger.info("Executing crawler for %s (%s)", competitor.name, competitor.website_url)
    scraped_data = await asyncio.wait_for(
        crawler.crawl(
            competitor.name, competitor.website_url, research_run_id=run_id
        ),
        timeout=timeout_seconds,
    )

    # Step 3: Persist scraped data snapshot
    save_scraped_data(db, run_id, scraped_data)

    return scraped_data


def _reusable_analysis(previous_run: ResearchRun | None, scraped_data: dict[str, Any]) -> bool:
    """Only reuse a completed analysis for unchanged content and policy."""
    if previous_run is None:
        return False
    previous_analysis = previous_run.analysis_result
    return (
        isinstance(previous_analysis, dict)
        and not has_content_changed(scraped_data, previous_run.scraped_data)
        and previous_analysis.get("analysis_policy_version") == ANALYSIS_POLICY_VERSION
    )


async def _reuse_completed_analysis(
    db: Session, run_id: int, competitor: Competitor, previous_run: ResearchRun,
    active_analyzer: AnalyzerProtocol, timeout_seconds: float,
) -> ResearchRun:
    """Copy forward prior findings and refresh only a missing/stale comparison."""
    logger.info(
        "Scraped content unchanged for %s. Skipping LLM synthesis to conserve tokens.",
        competitor.name,
    )
    # Link previous analysis result and copy forward existing SWOT findings
    prev_analysis = (
        previous_run.analysis_result
        if isinstance(previous_run.analysis_result, dict)
        else {}
    )
    reused_payload: dict[str, Any] = {
        "skipped_llm": True,
        "analysis_policy_version": ANALYSIS_POLICY_VERSION,
        "reason": "Content unchanged from previous snapshot",
        "reused_from_run_id": prev_analysis.get(
            "reused_from_run_id", previous_run.id
        ),
    }
    for key in ("summary", "features", "scores", "extracted_information", "conflicting_data"):
        if key in prev_analysis:
            reused_payload[key] = prev_analysis[key]
    # A skipped refresh must retain role counts, including an explicit 0,
    # without making another paid LLM call.
    for key in ("job_postings", "open_roles"):
        if key in prev_analysis:
            reused_payload[key] = prev_analysis[key]

    save_analysis_result(db, run_id, reused_payload)

    # Re-persist the prior findings under this run for audit history
    prior_entries = [
        {
            "category": entry.category.value,
            "content": entry.content,
            "source_url": entry.source_url,
        }
        for entry in previous_run.swot_entries
    ]
    if prior_entries:
        save_swot_entries(db, run_id, prior_entries)

    completed_run = update_research_run_status(db, run_id, ResearchStatus.COMPLETED)
    # Nothing changed, so only compare if no overall SWOT exists yet
    await _refresh_overall_swot_for_run(
        db, run_id, active_analyzer, timeout_seconds, only_if_missing=True
    )
    return completed_run


async def _refresh_overall_swot_for_run(
    db: Session, run_id: int, analyzer: AnalyzerProtocol, timeout_seconds: float,
    only_if_missing: bool = False,
) -> None:
    """Refresh the overall SWOT, billing its comparisons to this run's session."""
    comparison_calls: list[str] = []
    await refresh_overall_swot(
        db,
        analyzer,
        timeout_seconds,
        only_if_missing=only_if_missing,
        trace_prefix=run_trace_prefix(run_id),
        session_id=run_session_id(run_id),
        comparison_calls=comparison_calls,
    )
    if comparison_calls:
        _expect_llm_calls(db, run_id, comparison_calls)


def _expect_llm_calls(db: Session, run_id: int, names: list[str]) -> None:
    """Add paid call steps to the run's llm_calls so cost lookups wait for them."""
    try:
        run = get_research_run(db, run_id)
        if run is None:
            return
        db.refresh(run)
        analysis = run.analysis_result if isinstance(run.analysis_result, dict) else {}
        made = [name for name in analysis.get("llm_calls", []) if isinstance(name, str)]
        save_analysis_result(db, run_id, {**analysis, "llm_calls": list(dict.fromkeys(made + names))})
    except Exception:
        # Cost bookkeeping must never fail research that already completed.
        db.rollback()
        logger.exception("Unable to record comparison calls for run %s", run_id)


async def _analyze_and_publish_research(
    db: Session, run_id: int, competitor: Competitor, scraped_data: dict[str, Any],
    active_analyzer: AnalyzerProtocol, timeout_seconds: float,
) -> ResearchRun:
    """Persist new analysis, complete the run and refresh the overall SWOT."""
    # Step 5: Feed competitor Markdown + SCADABLE baseline profile to LLM
    baseline_profile = get_scadable_baseline(db)
    logger.info("Invoking LLM SWOT synthesis for %s", competitor.name)
    analysis_timeout = _llm_step_timeout("ANALYSIS_TIMEOUT_SECONDS", timeout_seconds)
    try:
        analysis_result = await asyncio.wait_for(
            active_analyzer.analyze(
                scraped_data,
                baseline_profile,
                trace_prefix=run_trace_prefix(run_id, competitor.name),
                session_id=run_session_id(run_id),
            ),
            timeout=analysis_timeout,
        )
    except asyncio.TimeoutError as exc:
        raise StepTimeout(analysis_timeout) from exc

    # Step 6: Save raw LLM analysis output
    save_analysis_result(db, run_id, {
        **analysis_result,
        "analysis_policy_version": ANALYSIS_POLICY_VERSION,
    })

    # Step 7: Parse structured SWOT findings and persist with verified source citations
    raw_entries = analysis_result.get("swot_entries", [])
    if raw_entries:
        save_swot_entries(db, run_id, raw_entries)

    # A state-record failure must not turn successful research into failure.
    try:
        mark_overall_swot_stale(db)
    except Exception:
        db.rollback()
        logger.exception("Unable to mark overall SWOT stale for run %s", run_id)

    # Step 8: Mark task status as COMPLETED
    updated_run = update_research_run_status(db, run_id, ResearchStatus.COMPLETED)
    logger.info("Successfully completed research run %s for %s", run_id, competitor.name)

    # Step 9: Refresh SCADABLE's overall SWOT now that this competitor changed
    await _refresh_overall_swot_for_run(db, run_id, active_analyzer, timeout_seconds)
    return updated_run


async def _run_research_steps(
    db: Session,
    run_id: int,
    crawler: CrawlerProtocol | None = None,
    analyzer: AnalyzerProtocol | None = None,
    timeout_seconds: float = 90.0,
) -> ResearchRun:
    """Execute the end-to-end competitor research pipeline for a given run ID.

    Pipeline Steps:
        1. Transition state from PENDING to RUNNING.
        2. Execute Crawl4AI scraping routines with timeout guard.
        3. Save scraped content snapshot to the database.
        4. Diff new scrape against previous database snapshot (noise reduction).
        5. If unchanged, skip LLM synthesis and reuse existing SWOT findings.
        6. If changed, invoke LLM analysis comparing against SCADABLE baseline.
        7. Persist raw analysis JSON and structured SWOT entries with source URLs.
        8. Transition state to COMPLETED.
        9. Compare SCADABLE only against updated competitor and aggregate stored
           per-competitor findings (unless the baseline has changed).

    On Failure:
        Catches timeouts and exceptions, transitions run to FAILED with error
        message, and ensures previous database findings are preserved.
    """
    run = get_research_run(db, run_id)
    if run is None:
        raise NotFoundError(f"Research run {run_id} not found.")

    competitor = get_competitor(db, run.competitor_id)
    if competitor is None:
        raise NotFoundError(f"Competitor {run.competitor_id} not found.")

    # Step 1: Transition lifecycle state to RUNNING
    update_research_run_status(db, run_id, ResearchStatus.RUNNING)
    logger.info("Started research run %s for competitor %s", run_id, competitor.name)

    active_crawler = crawler or get_default_crawler()
    active_analyzer = analyzer or get_default_analyzer()

    try:
        scraped_data = await _crawl_and_save_snapshot(
            db, run_id, competitor, active_crawler, timeout_seconds
        )
        previous_run = _get_previous_completed_run(db, competitor.id, run_id)
        if previous_run is not None and _reusable_analysis(previous_run, scraped_data):
            return await _reuse_completed_analysis(
                db, run_id, competitor, previous_run, active_analyzer, timeout_seconds
            )
        return await _analyze_and_publish_research(
            db, run_id, competitor, scraped_data, active_analyzer, timeout_seconds
        )

    except asyncio.TimeoutError as exc:
        # Report the limit of the step that ran out (crawl or analysis).
        seconds = exc.seconds if isinstance(exc, StepTimeout) else timeout_seconds
        error_msg = f"Research run timed out after {seconds:g} seconds."
        logger.error("Run %s failed: %s", run_id, error_msg)
        return update_research_run_status(
            db,
            run_id,
            ResearchStatus.FAILED,
            error_message=error_msg,
        )

    except CrawlFailedError:
        # Details are logged by the crawler; the API only shows the safe message
        logger.error("Run %s failed: crawl of %s failed", run_id, competitor.website_url)
        return update_research_run_status(
            db,
            run_id,
            ResearchStatus.FAILED,
            error_message=CRAWL_FAILED_MESSAGE,
        )

    except InvalidStatusTransitionError:
        # The run was cancelled while a step finished. execute_research_run handles it.
        raise

    except Exception:
        logger.exception("Research run %s failed unexpectedly", run_id)
        return update_research_run_status(
            db,
            run_id,
            ResearchStatus.FAILED,
            error_message="Research failed due to an internal error.",
        )


async def record_run_cost(db: Session, run_id: int, final: bool = True) -> bool:
    """Combine independently measured LLM and Decisions costs for this run.

    Decisions may be charged even when analysis is skipped or the run fails.
    Every analysis call the run triggered (extraction, comparison, reviews and
    retries) is read from the run's Langfuse session; the comparison is billed
    to the run that started it, not to every competitor it read. Never use
    local budget estimates as measured costs. Incomplete data stays visibly
    incomplete.
    """
    run = get_research_run(db, run_id)
    if run is None:
        return True

    analysis = run.analysis_result if isinstance(run.analysis_result, dict) else {}
    scrape = run.scraped_data if isinstance(run.scraped_data, dict) else {}
    # Steps whose paid calls started, like "run-42:vanta:extraction" or "run-42:comparison"
    expected_steps = list(dict.fromkeys(
        name for name in analysis.get("llm_calls", []) if isinstance(name, str)
    ))
    started_at = run.started_at or run.created_at

    session = await fetch_session_cost(run_session_id(run_id), started_at=started_at)
    if session is None:
        return True

    usage = scrape.get("decision_usage")
    usage = usage if isinstance(usage, dict) else {}
    expected = usage.get("request_count")
    if type(expected) is not int or expected < 0:
        expected = None
    decisions = await fetch_decisions_cost(
        run_id,
        scrape.get("decision_tracking"),
        expected_requests=expected,
        started_at=started_at,
    )
    # Each step must have arrived, not just some call: extraction can be
    # ingested minutes before the comparison that followed it.
    missing = [step for step in expected_steps if not session.has_step(step)]
    pending = bool(missing or session.usd is None or not decisions.complete)
    if pending and not final:
        logger.info("Run %s cost telemetry incomplete; retrying later", run_id)
        return False

    llm_total = session.usd
    decisions_total = decisions.usd
    # A partial sum is useful for audit, but not a reliable dashboard total.
    complete = not pending
    total = (
        round(llm_total + decisions_total, 6)
        if complete and llm_total is not None and decisions_total is not None
        else None
    )
    db.refresh(run)
    latest = run.analysis_result if isinstance(run.analysis_result, dict) else {}
    save_analysis_result(db, run_id, {
        **latest,
        "cost": {
            "usd": total,
            "analysis_usd": round(llm_total, 6) if llm_total is not None else None,
            "analysis_calls": session.found,
            "analysis_missing_steps": missing,
            "decisions_usd": round(decisions_total, 6) if decisions_total is not None else None,
            "decisions_found": decisions.found,
            "decisions_expected": decisions.expected,
            "decisions_complete": decisions.complete,
            "complete": complete,
            "fetched_at": datetime.now(timezone.utc).isoformat(),
        },
    })
    return True


async def _record_cost_later(session_factory: sessionmaker[Session], run_id: int) -> None:
    """Wait for Langfuse to ingest the run's calls, then save their cost.

    A server restart during the wait loses the lookup; the run then has no cost.
    """
    if not is_cost_tracking_configured():
        return
    for attempt in range(COST_LOOKUP_ATTEMPTS):
        await asyncio.sleep(COST_LOOKUP_DELAY_SECONDS)
        final = attempt == COST_LOOKUP_ATTEMPTS - 1
        try:
            with session_factory() as db:
                if await record_run_cost(db, run_id, final=final):
                    return
        except Exception:
            logger.exception("Cost lookup for run %s failed", run_id)


def trigger_research_task(
    competitor_id: int,
    background_tasks: BackgroundTasks,
    db: Session,
    session_factory: sessionmaker[Session] = SessionLocal,
    crawler: CrawlerProtocol | None = None,
    analyzer: AnalyzerProtocol | None = None,
    timeout_seconds: float = 90.0,
) -> ResearchRun:
    """Trigger an asynchronous research run in the background.

    Enqueues the pipeline onto FastAPI BackgroundTasks and returns immediately
    with a newly created run in PENDING status.
    """
    # Create the run record in PENDING status
    run = create_research_run(db, competitor_id)

    async def _async_worker(target_run_id: int) -> None:
        with session_factory() as worker_db:
            await execute_research_run(
                db=worker_db,
                run_id=target_run_id,
                crawler=crawler,
                analyzer=analyzer,
                timeout_seconds=timeout_seconds,
            )
        # Also for failed runs: a timeout can still leave paid calls behind
        await _record_cost_later(session_factory, target_run_id)

    background_tasks.add_task(_async_worker, run.id)
    return run


def trigger_overall_swot_refresh(
    background_tasks: BackgroundTasks,
    session_factory: sessionmaker[Session] = SessionLocal,
    analyzer: AnalyzerProtocol | None = None,
    timeout_seconds: float = 90.0,
) -> None:
    """Refresh the overall SWOT in the background, e.g. after a competitor is deleted.

    Runs in its own session because the request's session closes once the
    response is sent.
    """

    async def _async_worker() -> None:
        with session_factory() as worker_db:
            await refresh_overall_swot(
                worker_db,
                analyzer or get_default_analyzer(),
                timeout_seconds,
            )

    background_tasks.add_task(_async_worker)

