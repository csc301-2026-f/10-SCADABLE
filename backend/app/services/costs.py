"""Look up what a research run's OpenAI calls cost, as recorded in Langfuse.

The analyzer names each call "{prefix}:{step}" and the backend picks the prefix,
so run 42 for Vanta records "run-42:vanta:extraction", "run-42:vanta:analysis"
and "run-42:comparison". The Observations V2 API reads them back in real time,
but Langfuse can take a few minutes to ingest a call's cost, so callers wait
before looking it up.
"""

import asyncio
import logging
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 30.0
MAX_PAGES_PER_NAME = 10
DEFAULT_BASE_URL = "https://cloud.langfuse.com"  # the Langfuse SDK's default


@dataclass
class CallCost:
    """Calls recorded under one name and their total cost."""

    found: int
    # None when Langfuse has no price for at least one of the calls
    usd: float | None


def company_slug(company_name: str) -> str:
    """Lowercase, with anything but letters and digits as "-" ("Vanta Inc." -> "vanta-inc")."""
    return re.sub(r"[^a-z0-9]+", "-", company_name.lower()).strip("-") or "company"


def run_trace_prefix(run_id: int, company_name: str | None = None) -> str:
    """"run-42:vanta" for a competitor's own calls; "run-42" for the comparison of all of them."""
    if company_name is None:
        return f"run-{run_id}"
    return f"run-{run_id}:{company_slug(company_name)}"


def run_call_names(run_id: int, company_name: str) -> list[str]:
    competitor_prefix = run_trace_prefix(run_id, company_name)
    return [
        f"{competitor_prefix}:extraction",
        f"{competitor_prefix}:analysis",
        f"{run_trace_prefix(run_id)}:comparison",
    ]


def _langfuse_settings() -> Any | None:
    """Langfuse settings, or None when the package or keys are missing."""
    try:
        import langfuse  # noqa: F401
        from llm.config import Settings
        from scraper.environment import load_environment
    except ImportError:
        return None
    load_environment()
    settings = Settings()
    if not (settings.langfuse_public_key and settings.langfuse_secret_key):
        return None
    return settings


def is_cost_tracking_configured() -> bool:
    return _langfuse_settings() is not None


async def _cost_for_name(api: Any, name: str) -> CallCost:
    found = 0
    total = 0.0
    priced = True
    cursor = None
    seen: set[str] = set()
    for _ in range(MAX_PAGES_PER_NAME):
        page = await asyncio.wait_for(
            api.observations.get_many(
                name=name,
                type="GENERATION",
                fields="basic,usage",
                limit=100,
                cursor=cursor,
            ),
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        for observation in page.data:
            if observation.name != name:
                continue
            observation_id = getattr(observation, "id", None)
            if observation_id is not None:
                if observation_id in seen:
                    continue
                seen.add(observation_id)
            found += 1
            if observation.total_cost is None:
                priced = False
            else:
                total += float(observation.total_cost)
        # An empty page can still have a continuation cursor (Langfuse v2).
        cursor = page.meta.cursor
        if not cursor:
            break
    else:
        # Do not mistake a truncated lookup for a complete sum.
        priced = False
    return CallCost(found=found, usd=total if priced else None)


async def fetch_call_costs(names: list[str]) -> dict[str, CallCost] | None:
    """Cost of the calls recorded under each name; None if Langfuse isn't configured."""
    settings = _langfuse_settings()
    if settings is None:
        return None

    import httpx
    from langfuse.api import AsyncLangfuseAPI

    # A plain API client, not Langfuse(): Langfuse() shares one recorder per key
    # across the process, and shutting it down would stop the analyzer's calls
    # from being recorded.
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as http:
        api = AsyncLangfuseAPI(
            base_url=settings.langfuse_base_url or DEFAULT_BASE_URL,
            username=settings.langfuse_public_key,
            password=settings.langfuse_secret_key.get_secret_value(),
            x_langfuse_public_key=settings.langfuse_public_key,
            httpx_client=http,
        )
        return {name: await _cost_for_name(api, name) for name in names}


@dataclass
class DecisionsCost:
    """Measured scraper Decisions subtotal; None is unavailable, not free."""

    found: int
    usd: float | None
    complete: bool
    expected: int | None


def _observation_field(value: Any, field: str) -> Any:
    return value.get(field) if isinstance(value, dict) else getattr(value, field, None)


async def _fetch_decisions_from_api(
    api: Any,
    *,
    run_id: int,
    reference: dict[str, Any] | None,
    expected_requests: int | None,
    started_at: Any,
) -> DecisionsCost:
    """Read only this run's Decisions child generations, including failed retries.

    Trace + parent constrain a successful tracked crawl. For a failed crawl,
    metadata on child generations can recover paid calls by research_run_id.
    All queries are time-bounded and cursor-paginated; a truncated scan cannot
    become a complete zero-dollar result.
    """
    from datetime import datetime, timedelta, timezone

    now = datetime.now(timezone.utc)
    if isinstance(started_at, str):
        try:
            started_at = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        except ValueError:
            started_at = None
    if not isinstance(started_at, datetime):
        started_at = now
    if started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)
    lower = started_at - timedelta(minutes=3)
    upper = now + timedelta(minutes=2)
    # Use only known safe identifiers, never a broad unbounded trace scan.
    reference = reference if isinstance(reference, dict) else {}
    trace = reference.get("trace_id")
    parent = reference.get("observation_id")
    use_reference = (
        isinstance(trace, str) and len(trace) == 32
        and isinstance(parent, str) and len(parent) == 16
        and reference.get("research_run_id") in (None, str(run_id))
    )

    observations: dict[str, Any] = {}
    complete_scan = True
    # Query each name separately when metadata-searching failed/partial crawls.
    names = (None,) if use_reference else (
        "scraper.decisions.candidates", "scraper.decisions.content"
    )
    for name in names:
        cursor = None
        for _ in range(MAX_PAGES_PER_NAME):
            args = {
                "type": "GENERATION",
                "fields": "basic,usage,metadata",
                "from_start_time": lower,
                "to_start_time": upper,
                "limit": 100,
                "cursor": cursor,
            }
            if use_reference:
                args.update(trace_id=trace, parent_observation_id=parent)
            else:
                args["name"] = name
            page = await asyncio.wait_for(
                api.observations.get_many(**args), timeout=REQUEST_TIMEOUT_SECONDS
            )
            for obs in page.data:
                obs_name = _observation_field(obs, "name")
                if obs_name not in (
                    "scraper.decisions.candidates", "scraper.decisions.content"
                ):
                    continue
                meta = _observation_field(obs, "metadata") or {}
                if not isinstance(meta, dict) or meta.get("component") != "scraper_decisions":
                    continue
                if str(meta.get("research_run_id")) != str(run_id):
                    continue
                if use_reference and (
                    _observation_field(obs, "trace_id") != trace
                    or _observation_field(obs, "parent_observation_id") != parent
                ):
                    continue
                obs_id = _observation_field(obs, "id")
                if isinstance(obs_id, str):
                    observations[obs_id] = obs
            cursor = _observation_field(page.meta, "cursor")
            if not cursor:
                break
        else:
            complete_scan = False

    total = 0.0
    priced = True
    for obs in observations.values():
        price = _observation_field(obs, "total_cost")
        if price is None:
            priced = False
        else:
            total += float(price)
    found = len(observations)
    expected_met = expected_requests is not None and found >= expected_requests
    # An expected zero is valid evidence of no dispatched calls, while zero
    # observations with missing usage never proves a failed crawl was free.
    complete = complete_scan and priced and expected_met
    return DecisionsCost(
        found=found,
        usd=round(total, 9) if priced and complete_scan else None,
        complete=complete,
        expected=expected_requests,
    )


async def fetch_decisions_cost(
    run_id: int,
    reference: Any,
    *,
    expected_requests: int | None,
    started_at: Any,
) -> DecisionsCost:
    """Fetch Langfuse Decisions costs; no local budget estimate is used."""
    settings = _langfuse_settings()
    if settings is None:
        return DecisionsCost(0, None, False, expected_requests)

    import httpx
    from langfuse.api import AsyncLangfuseAPI

    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as http:
        api = AsyncLangfuseAPI(
            base_url=settings.langfuse_base_url or DEFAULT_BASE_URL,
            username=settings.langfuse_public_key,
            password=settings.langfuse_secret_key.get_secret_value(),
            x_langfuse_public_key=settings.langfuse_public_key,
            httpx_client=http,
        )
        return await _fetch_decisions_from_api(
            api, run_id=run_id,
            reference=reference,
            expected_requests=expected_requests,
            started_at=started_at,
        )
