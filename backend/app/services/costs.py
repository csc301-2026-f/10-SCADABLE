"""Look up what a research run cost, as recorded in Langfuse.

Every analysis call a run triggers, including the reviewer calls and retries in
extraction and comparison, is recorded in one Langfuse session: "run-42" for
run 42. Calls keep step names like "run-42:vanta:extraction-1" or
"run-42:comparison-validation-2", so the backend can check that every expected
step has arrived before trusting the total. Langfuse can take a few minutes to
ingest a call's cost, so callers wait before looking it up.

The scraper's Decisions calls are tracked separately (fetch_decisions_cost).
"""

import asyncio
import logging
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, AsyncIterator

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_SECONDS = 30.0
MAX_PAGES_PER_NAME = 10
DEFAULT_BASE_URL = "https://cloud.langfuse.com"  # the Langfuse SDK's default

# Only calls started around this run count. Run numbers repeat across
# deployments that share a Langfuse project (dev and prod both have a "run-42")
# and after a database reset, so the session name alone is not enough.
WINDOW_BEFORE = timedelta(minutes=3)
WINDOW_AFTER = timedelta(minutes=2)


@dataclass
class SessionCost:
    """Analysis calls recorded in one run's session and their total cost."""

    found: int
    # None when Langfuse has no price for at least one call or the scan was cut short
    usd: float | None
    names: list[str] = field(default_factory=list)

    def has_step(self, step: str) -> bool:
        """True once at least one call for this step ("run-42:comparison") has arrived."""
        return any(name == step or name.startswith(f"{step}-") for name in self.names)


def company_slug(company_name: str) -> str:
    """Lowercase, with anything but letters and digits as "-" ("Vanta Inc." -> "vanta-inc")."""
    return re.sub(r"[^a-z0-9]+", "-", company_name.lower()).strip("-") or "company"


def run_trace_prefix(run_id: int, company_name: str | None = None) -> str:
    """"run-42:vanta" for a competitor's own calls; "run-42" for the comparison of all of them."""
    if company_name is None:
        return f"run-{run_id}"
    return f"run-{run_id}:{company_slug(company_name)}"


def run_session_id(run_id: int) -> str:
    """The Langfuse session holding every analysis call run 42 triggers: "run-42"."""
    return f"run-{run_id}"


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


@asynccontextmanager
async def _langfuse_api(settings: Any) -> AsyncIterator[Any]:
    import httpx
    from langfuse.api import AsyncLangfuseAPI

    # A plain API client, not Langfuse(): Langfuse() shares one recorder per key
    # across the process, and shutting it down would stop the analyzer's calls
    # from being recorded.
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as http:
        yield AsyncLangfuseAPI(
            base_url=settings.langfuse_base_url or DEFAULT_BASE_URL,
            username=settings.langfuse_public_key,
            password=settings.langfuse_secret_key.get_secret_value(),
            x_langfuse_public_key=settings.langfuse_public_key,
            httpx_client=http,
        )


def _observation_field(value: Any, field: str) -> Any:
    return value.get(field) if isinstance(value, dict) else getattr(value, field, None)


def _time_window(started_at: Any) -> tuple[datetime, datetime]:
    """From shortly before the run started until shortly after now."""
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
    return started_at - WINDOW_BEFORE, now + WINDOW_AFTER


async def _session_cost_from_api(api: Any, session_id: str, started_at: Any) -> SessionCost:
    lower, upper = _time_window(started_at)
    names: list[str] = []
    total = 0.0
    priced = True
    cursor = None
    seen: set[str] = set()
    for _ in range(MAX_PAGES_PER_NAME):
        page = await asyncio.wait_for(
            api.observations.get_many(
                session_id=session_id,
                type="GENERATION",
                fields="basic,usage",
                from_start_time=lower,
                to_start_time=upper,
                limit=100,
                cursor=cursor,
            ),
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        for observation in page.data:
            observed_session = _observation_field(observation, "session_id")
            if observed_session is not None and observed_session != session_id:
                continue
            name = _observation_field(observation, "name") or ""
            if name.startswith("scraper.decisions"):
                continue  # counted by fetch_decisions_cost
            observation_id = _observation_field(observation, "id")
            if observation_id is not None:
                if observation_id in seen:
                    continue
                seen.add(observation_id)
            names.append(name)
            price = _observation_field(observation, "total_cost")
            if price is None:
                priced = False
            else:
                total += float(price)
        # An empty page can still have a continuation cursor (Langfuse v2).
        cursor = _observation_field(page.meta, "cursor")
        if not cursor:
            break
    else:
        # Do not mistake a truncated lookup for a complete sum.
        priced = False
    return SessionCost(found=len(names), usd=round(total, 9) if priced else None, names=names)


async def fetch_session_cost(session_id: str, *, started_at: Any) -> SessionCost | None:
    """Cost of every analysis call in the session; None if Langfuse isn't configured."""
    settings = _langfuse_settings()
    if settings is None:
        return None
    async with _langfuse_api(settings) as api:
        return await _session_cost_from_api(api, session_id, started_at)


@dataclass
class DecisionsCost:
    """Measured scraper Decisions subtotal; None is unavailable, not free."""

    found: int
    usd: float | None
    complete: bool
    expected: int | None


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
    lower, upper = _time_window(started_at)
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

    async with _langfuse_api(settings) as api:
        return await _fetch_decisions_from_api(
            api, run_id=run_id,
            reference=reference,
            expected_requests=expected_requests,
            started_at=started_at,
        )
