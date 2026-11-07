"""Research workflow orchestrator and asynchronous task runner.

Coordinates the end-to-end research lifecycle for Deliverable 2:
Add Competitor -> Trigger Crawler -> Diff Snapshot -> Invoke LLM -> Persist to DB
-> Compare SCADABLE against all competitors -> Persist overall SWOT.
"""

import asyncio
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
from app.services.costs import (
    fetch_call_costs,
    fetch_decisions_cost,
    is_cost_tracking_configured,
    run_call_names,
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


async def refresh_overall_swot(
    db: Session,
    analyzer: AnalyzerProtocol,
    timeout_seconds: float = 90.0,
    only_if_missing: bool = False,
    trace_prefix: str | None = None,
) -> None:
    """Compare SCADABLE against every researched competitor and save the overall SWOT.

    trace_prefix names the comparison's OpenAI call for cost lookups ("run-42").

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
            # The two-stage extractor may return facts other than SWOT claims
            # or features; keep those competitors in the comparison as well.
            extracted = analysis.get("extracted_information")
            if not claims and not features and not (
                isinstance(extracted, dict) and extracted.get("claims_cited")
            ):
                continue
            latest_runs[competitor.name] = latest_run
            profiles.append({
                "company_name": competitor.name,
                "website_url": competitor.website_url,
                "summary": analysis.get("summary", ""),
                "features": features,
                "claims": claims,
                "extracted_information": extracted,
            })

        if not profiles:
            # Nothing left to compare, so don't keep findings about deleted competitors
            publish_overall_swot(db, [], revision)
            return

        # Overall comparisons grow with every researched competitor and can
        # take longer than one company's crawl/analysis. Allow the comparison
        # deadline to be configured separately on Railway.
        comparison_timeout = float(
            os.getenv("OVERALL_SWOT_TIMEOUT_SECONDS", str(timeout_seconds))
        )
        if comparison_timeout <= 0:
            raise ValueError("OVERALL_SWOT_TIMEOUT_SECONDS must be positive")
        logger.info(
            "Comparing SCADABLE against %d competitors (timeout=%ss)",
            len(profiles), comparison_timeout,
        )
        comparison = await asyncio.wait_for(
            analyzer.compare(get_scadable_baseline(), profiles, trace_prefix=trace_prefix),
            timeout=comparison_timeout,
        )
        overall_entries = comparison.get("swot_entries", [])
        scores_by_company = _scores_by_company(
            comparison.get("scores"), comparison.get("feature_pairs")
        )
        # Even when SWOT is empty, valid feature scores still matter. Both score
        # writes and publishing are guarded against newer competitor changes.
        scores_by_run = {
            run.id: scores_by_company[name]
            for name, run in latest_runs.items()
            if scores_by_company.get(name)
        }
        valid_entries = overall_entries if isinstance(overall_entries, list) and overall_entries else None
        competitor_swots_by_run = {}
        blocks = comparison.get("competitor_swots")
        if isinstance(blocks, list):
            for block in blocks:
                if not isinstance(block, dict):
                    continue
                name, entries = block.get("company_name"), block.get("swot_entries")
                if name in latest_runs and isinstance(entries, list):
                    competitor_swots_by_run[latest_runs[name].id] = entries
        if not save_comparison_scores_if_current(
            db, revision, scores_by_run, valid_entries,
            competitor_swots_by_run=competitor_swots_by_run,
        ):
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
            active_crawler.crawl(
                competitor.name, competitor.website_url, research_run_id=run_id
            ),
            timeout=timeout_seconds,
        )

        # Step 3: Persist scraped data snapshot
        save_scraped_data(db, run_id, scraped_data)

        # Step 4: Noise filter & snapshot diffing against PostgreSQL
        previous_run = _get_previous_completed_run(db, competitor.id, run_id)
        prev_scraped = previous_run.scraped_data if previous_run else None

        previous_analysis = (
            previous_run.analysis_result
            if previous_run and isinstance(previous_run.analysis_result, dict)
            else {}
        )
        if (previous_run
                and not has_content_changed(scraped_data, prev_scraped)
                and previous_analysis.get("analysis_policy_version") == ANALYSIS_POLICY_VERSION):
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
            await refresh_overall_swot(
                db,
                active_analyzer,
                timeout_seconds,
                only_if_missing=True,
                trace_prefix=run_trace_prefix(run_id),
            )
            return completed_run

        # Step 5: Feed competitor Markdown + SCADABLE baseline profile to LLM
        baseline_profile = get_scadable_baseline()
        logger.info("Invoking LLM SWOT synthesis for %s", competitor.name)
        analysis_result = await asyncio.wait_for(
            active_analyzer.analyze(
                scraped_data,
                baseline_profile,
                trace_prefix=run_trace_prefix(run_id, competitor.name),
            ),
            timeout=timeout_seconds,
        )

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
        await refresh_overall_swot(
            db, active_analyzer, timeout_seconds, trace_prefix=run_trace_prefix(run_id)
        )
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

    except CrawlFailedError:
        # Details are logged by the crawler; the API only shows the safe message
        logger.error("Run %s failed: crawl of %s failed", run_id, competitor.website_url)
        return update_research_run_status(
            db,
            run_id,
            ResearchStatus.FAILED,
            error_message=CRAWL_FAILED_MESSAGE,
        )

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
    Comparison is attributed to the research run that initiated it (not to
    every competitor the comparison read). Never use local budget estimates
    as measured Langfuse costs. Incomplete data stays visibly incomplete.
    """
    run = get_research_run(db, run_id)
    if run is None:
        return True

    analysis = run.analysis_result if isinstance(run.analysis_result, dict) else {}
    scrape = run.scraped_data if isinstance(run.scraped_data, dict) else {}
    made = [name for name in analysis.get("llm_calls", []) if isinstance(name, str)]
    names = list(dict.fromkeys(made + run_call_names(run_id, run.competitor.name)))

    costs = await fetch_call_costs(names)
    if costs is None:
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
        started_at=run.started_at or run.created_at,
    )
    missing = [name for name in made if name not in costs or costs[name].found == 0]
    found = {name: item for name, item in costs.items() if item.found}
    unpriced = [name for name, item in found.items() if item.usd is None]
    pending = bool(missing or unpriced or not decisions.complete)
    if pending and not final:
        logger.info("Run %s cost telemetry incomplete; retrying later", run_id)
        return False

    llm_total = sum(item.usd for item in found.values() if item.usd is not None)
    decisions_total = decisions.usd
    # A partial sum is useful for audit, but not a reliable dashboard total.
    complete = not pending
    total = round(llm_total + decisions_total, 6) if complete and decisions_total is not None else None
    db.refresh(run)
    latest = run.analysis_result if isinstance(run.analysis_result, dict) else {}
    save_analysis_result(db, run_id, {
        **latest,
        "cost": {
            "usd": total,
            "analysis_usd": round(llm_total, 6),
            "decisions_usd": round(decisions_total, 6) if decisions_total is not None else None,
            "decisions_found": decisions.found,
            "decisions_expected": decisions.expected,
            "decisions_complete": decisions.complete,
            "calls": {name: item.usd for name, item in found.items()},
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

