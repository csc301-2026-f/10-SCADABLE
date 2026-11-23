"""SWOT endpoints."""

from fastapi import APIRouter, Depends, Query
from urllib.parse import urlsplit
from sqlalchemy.orm import Session

from app.api import serializers
from app.api.schemas import ErrorResponse, Finding, SwotOverview
from app.db import repository as repo
from app.db.database import get_db

router = APIRouter(prefix="/swot", tags=["swot"])

_BUCKETS = {
    "strength": "strengths",
    "weakness": "weaknesses",
    "opportunity": "opportunities",
    "threat": "threats",
}


def _group(findings: list[Finding], is_stale: bool = False) -> SwotOverview:
    buckets: dict[str, list[Finding]] = {name: [] for name in _BUCKETS.values()}
    for finding in findings:
        buckets[_BUCKETS[finding.category]].append(finding)
    return SwotOverview(
        is_stale=is_stale,
        updated_at=max((f.detected_at for f in findings), default=None),
        **buckets,
    )


def _citation_sources(db: Session) -> dict[tuple[str, str], list[dict[str, str | None]]]:
    """Assemble evidence for published overall findings from versioned JSON snapshots.

    No migration is required: the existing per-competitor contribution JSON
    already stores the complete citation list after comparisons are refreshed.
    Earlier snapshots with only source_url remain readable.
    """
    grouped: dict[tuple[str, str], list[dict[str, str | None]]] = {}
    for _, snapshot in sorted(repo.get_overall_swot_contributions(db).items()):
        for entry in snapshot.entries or []:
            if not isinstance(entry, dict):
                continue
            category, content = entry.get("category"), entry.get("content")
            if not isinstance(category, str) or not isinstance(content, str):
                continue
            key = (category, " ".join(content.split()).casefold())
            sources = grouped.setdefault(key, [])
            uses_baseline = category in {"strength", "weakness"} or bool(entry.get("uses_scadable_baseline"))
            if uses_baseline and not any(source["kind"] == "baseline" for source in sources):
                sources.insert(0, {"kind": "baseline", "label": "SCADABLE baseline (configured)", "url": None})
            urls = entry.get("source_urls")
            if not isinstance(urls, list):
                urls = [entry.get("source_url")]
            for url in urls:
                if not isinstance(url, str) or not url:
                    continue
                parsed = urlsplit(url)
                if parsed.scheme not in {"https", "http"} or not parsed.hostname:
                    continue
                if parsed.hostname.removeprefix("www.") == "scadable.com":
                    # A configured baseline homepage is not a verified crawl.
                    continue
                if any(source["url"] == url for source in sources):
                    continue
                sources.append({"kind": "competitor", "label": serializers.source_label(url), "url": url})
    return grouped


def _overview(db: Session) -> SwotOverview:
    """Return only published current overall findings, never stale fallback data."""
    if not repo.is_overall_swot_current(db):
        # An empty new installation has no outdated findings to warn about.
        has_prior_data = bool(repo.get_overall_swot(db) or repo.has_completed_research(db))
        return _group([], is_stale=has_prior_data)
    evidence = _citation_sources(db)
    findings = []
    for entry in repo.get_overall_swot(db):
        key = (entry.category.value, " ".join(entry.content.split()).casefold())
        sources = evidence.get(key)
        if sources is None:
            # A published row may predate the per-competitor JSON snapshots.
            sources = []
            if entry.category.value in {"strength", "weakness"}:
                sources.append({"kind": "baseline", "label": "SCADABLE baseline (configured)", "url": None})
            if entry.source_url:
                parsed = urlsplit(entry.source_url)
                if (parsed.scheme in {"https", "http"} and parsed.hostname
                        and parsed.hostname.removeprefix("www.") != "scadable.com"):
                    sources.append({"kind": "competitor", "label": serializers.source_label(entry.source_url), "url": entry.source_url})
        findings.append(serializers.finding_from_entry(entry, None, sources=sources))
    return _group(findings)


@router.get(
    "",
    response_model=SwotOverview,
    responses={404: {"model": ErrorResponse}},
    summary="Get a SWOT analysis",
)
def get_swot(
    competitor_id: int | None = Query(
        None, alias="competitorId", ge=1, description="Omit for SCADABLE's overall SWOT."
    ),
    db: Session = Depends(get_db),
) -> SwotOverview:
    """SWOT grouped by category.

    With ``competitorId``: that competitor's latest SWOT. Without: the overview.
    """
    if competitor_id is None:
        return _overview(db)

    competitor = repo.get_competitor(db, competitor_id)
    if competitor is None:
        raise repo.NotFoundError(f"Competitor {competitor_id} not found.")
    return _group(
        [
            serializers.finding_from_entry(entry, competitor)
            for entry in repo.get_latest_swot(db, competitor.id)
        ]
    )


@router.get("/overview", response_model=SwotOverview, summary="Get the overall SWOT")
def get_swot_overview(db: Session = Depends(get_db)) -> SwotOverview:
    """SCADABLE's overall SWOT against all researched competitors (used by the
    frontend overview page). ``isStale`` is true while a newer comparison is pending
    or after one failed; outdated findings are never returned."""
    return _overview(db)
