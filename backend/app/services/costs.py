"""Look up what a research run's OpenAI calls cost, as recorded in Langfuse.

The analyzer records each call in Langfuse under "run-{id}:{step}". The
Observations V2 API reads them back in real time, but Langfuse can take a few
minutes to ingest a call's cost, so callers wait before looking it up.
"""

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

RUN_STEPS = ("extraction", "analysis", "comparison")
REQUEST_TIMEOUT_SECONDS = 30.0
MAX_PAGES_PER_NAME = 10
DEFAULT_BASE_URL = "https://cloud.langfuse.com"  # the Langfuse SDK's default


@dataclass
class CallCost:
    """Calls recorded under one name and their total cost."""

    found: int
    # None when Langfuse has no price for at least one of the calls
    usd: float | None


def run_call_names(run_id: int) -> list[str]:
    return [f"run-{run_id}:{step}" for step in RUN_STEPS]


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
            found += 1
            if observation.total_cost is None:
                priced = False
            else:
                total += observation.total_cost
        cursor = page.meta.cursor
        if not cursor or not page.data:
            break
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
