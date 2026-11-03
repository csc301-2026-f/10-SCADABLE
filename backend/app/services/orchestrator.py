"""Research workflow orchestrator and asynchronous task runner.

Coordinates the end-to-end research lifecycle for Deliverable 2:
Add Competitor -> Trigger Crawler -> Diff Snapshot -> Invoke LLM -> Persist to DB
-> Compare SCADABLE against all competitors -> Persist overall SWOT.
"""

import asyncio
import logging
from collections.abc import Callable
from datetime import datetime
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
    NotFoundError,
    create_research_run,
    get_competitor,
    get_latest_swot,
    get_overall_swot,
    get_overall_swot_revision,
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
from app.services.crawler import CrawlerProtocol, get_default_crawler
from app.services.diff import has_content_changed
from app.services.llm import AnalyzerProtocol, get_default_analyzer

logger = logging.getLogger(__name__)


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


def _scores_by_company(raw_scores: Any) -> dict[str, list[dict[str, Any]]]:
    """Group compare() scores by company, dropping malformed or out-of-range items."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    if not isinstance(raw_scores, list):
        return grouped
    for score in raw_scores:
        if not isinstance(score, dict):
            continue
        name, feature, value = score.get("company_name"), score.get("feature"), score.get("score")
        if not isinstance(name, str) or not isinstance(feature, str) or not feature.strip():
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 10:
            continue
        grouped.setdefault(name, []).append({"feature": feature.strip(), "score": value})
    return grouped


async def refresh_overall_swot(
    db: Session,
    analyzer: AnalyzerProtocol,
    timeout_seconds: float = 90.0,
    only_if_missing: bool = False,
) -> None:
    """Compare SCADABLE against every researched competitor and save the overall SWOT.

    Uses each competitor's latest stored strengths and weaknesses, so it makes
    one LLM call and never re-crawls. The comparison also scores each competitor's
    features, including competitors whose analysis found no strengths or
    weaknesses; those scores are saved on the competitor's latest run, where the
    competitor profile reads them. If no researched competitors remain, the
    overall SWOT is cleared. Failed/empty comparisons leave historical rows
    stored but invalid; the API never serves them as current findings.
    """
    try:
        if only_if_missing and is_overall_swot_current(db) and get_overall_swot(db):
            return

        # Refreshes only observe the input revision. Changing competitor data,
        # not starting a comparison, is what invalidates published findings.
        revision = get_overall_swot_revision(db)
        profiles = []
        latest_runs: dict[str, ResearchRun] = {}
        for competitor in list_competitors(db, limit=500):
            latest_run = _get_latest_completed_run(db, competitor.id)
            if latest_run is None:
                continue
            # Opportunities and threats are SCADABLE-relative templates, not competitor facts
            claims = [
                {
                    "category": entry.category.value,
                    "content": entry.content,
                    "source_url": entry.source_url,
                }
                for entry in get_latest_swot(db, competitor.id)
                if entry.category in (SWOTCategory.STRENGTH, SWOTCategory.WEAKNESS)
            ]
            analysis = (
                latest_run.analysis_result
                if isinstance(latest_run.analysis_result, dict)
                else {}
            )
            features = analysis.get("features", [])
            # Still compare a competitor with no strengths/weaknesses so its features get scored
            if not claims and not features:
                continue
            latest_runs[competitor.name] = latest_run
            profiles.append({
                "company_name": competitor.name,
                "website_url": competitor.website_url,
                "summary": analysis.get("summary", ""),
                "features": features,
                "claims": claims,
            })

        if not profiles:
            # Nothing left to compare, so don't keep findings about deleted competitors
            publish_overall_swot(db, [], revision)
            return

        logger.info("Comparing SCADABLE against %d competitors", len(profiles))
        comparison = await asyncio.wait_for(
            analyzer.compare(get_scadable_baseline(), profiles),
            timeout=timeout_seconds,
        )
        overall_entries = comparison.get("swot_entries", [])
        scores_by_company = _scores_by_company(comparison.get("scores"))
        # Even when SWOT is empty, valid feature scores still matter. Both score
        # writes and publishing are guarded against newer competitor changes.
        scores_by_run = {
            run.id: scores_by_company[name]
            for name, run in latest_runs.items()
            if scores_by_company.get(name)
        }
        valid_entries = overall_entries if isinstance(overall_entries, list) and overall_entries else None
        if not save_comparison_scores_if_current(db, revision, scores_by_run, valid_entries):
            logger.warning("Overall comparison superseded by newer competitor data.")
        elif valid_entries is None:
            logger.warning("Overall SWOT comparison returned no findings; valid scores saved.")

    except Exception:
        db.rollback()
        logger.exception("Overall SWOT comparison failed; overall results remain stale.")


async def execute_research_run(
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
        9. Compare SCADABLE against all competitors and replace the overall SWOT
           (skipped on unchanged runs unless no overall SWOT exists yet).

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
        # Step 2: Execute web crawling with timeout protection
        logger.info("Executing crawler for %s (%s)", competitor.name, competitor.website_url)
        scraped_data = await asyncio.wait_for(
            active_crawler.crawl(competitor.name, competitor.website_url),
            timeout=timeout_seconds,
        )

        # Step 3: Persist scraped data snapshot
        save_scraped_data(db, run_id, scraped_data)

        # Step 4: Noise filter & snapshot diffing against PostgreSQL
        previous_run = _get_previous_completed_run(db, competitor.id, run_id)
        prev_scraped = previous_run.scraped_data if previous_run else None

        if previous_run and not has_content_changed(scraped_data, prev_scraped):
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
                "reason": "Content unchanged from previous snapshot",
                "reused_from_run_id": prev_analysis.get(
                    "reused_from_run_id", previous_run.id
                ),
            }
            for key in ("summary", "features", "scores"):
                if prev_analysis.get(key):
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
            await refresh_overall_swot(
                db, active_analyzer, timeout_seconds, only_if_missing=True
            )
            return completed_run

        # Step 5: Feed competitor Markdown + SCADABLE baseline profile to LLM
        baseline_profile = get_scadable_baseline()
        logger.info("Invoking LLM SWOT synthesis for %s", competitor.name)
        analysis_result = await asyncio.wait_for(
            active_analyzer.analyze(scraped_data, baseline_profile),
            timeout=timeout_seconds,
        )

        # Step 6: Save raw LLM analysis output
        save_analysis_result(db, run_id, analysis_result)

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
        await refresh_overall_swot(db, active_analyzer, timeout_seconds)
        return updated_run

    except asyncio.TimeoutError:
        error_msg = f"Research run timed out after {timeout_seconds} seconds."
        logger.error("Run %s failed: %s", run_id, error_msg)
        return update_research_run_status(
            db,
            run_id,
            ResearchStatus.FAILED,
            error_message=error_msg,
        )

    except Exception:
        logger.exception("Research run %s failed unexpectedly", run_id)
        return update_research_run_status(
            db,
            run_id,
            ResearchStatus.FAILED,
            error_message="Research failed due to an internal error.",
        )


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

