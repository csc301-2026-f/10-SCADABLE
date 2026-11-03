"""Optional, metadata-only Decisions telemetry, independent of collection policy."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from dataclasses import dataclass, field
from typing import Any, Literal
from uuid import uuid4

from .decision_models import PROMPT_VERSION
from .types import ContractModel, UtcTimestamp, utc_now

logger = logging.getLogger(__name__)
FLUSH_TIMEOUT_SECONDS = 2.0


def _valid_id(value: str, length: int) -> bool:
    return (
        isinstance(value, str)
        and len(value) == length
        and set(value) <= set("0123456789abcdef")
        and value != "0" * length
    )


@dataclass(frozen=True)
class DecisionTrackingContext:
    """Caller-owned correlation; no credentials or scraped content."""

    crawl_id: str = field(default_factory=lambda: uuid4().hex)
    research_run_id: str | None = None
    trace_id: str | None = None
    parent_observation_id: str | None = None


class DecisionTrackingReference(ContractModel):
    """References identify created observations, not confirmed server ingestion."""

    schema_version: Literal["1.0"] = "1.0"
    crawl_id: str
    trace_id: str
    observation_id: str
    model: str
    started_at: UtcTimestamp
    finished_at: UtcTimestamp | None = None
    research_run_id: str | None = None


class DecisionTracker:
    def __init__(
        self, *, mode: str, model: str, context: DecisionTrackingContext | None = None
    ):
        self.context = context if context is not None else DecisionTrackingContext()
        self.mode = mode
        self.model = model
        self.reference: DecisionTrackingReference | None = None
        self._client: Any = None
        self._parent: Any = None
        self._warned = False
        self._finished = False
        self._flushed = False

    def _warn(self, message: str) -> None:
        if not self._warned:
            logger.warning(message)
            self._warned = True

    def _metadata(self) -> dict[str, str]:
        values = {
            "component": "scraper_decisions",
            "endpoint": "/v1/decisions",
            "model": self.model,
            "prompt_version": PROMPT_VERSION,
            "crawl_id": self.context.crawl_id,
        }
        if self.context.research_run_id is not None:
            values["research_run_id"] = self.context.research_run_id
        return values

    def start(self) -> None:
        public = os.environ.get("LANGFUSE_PUBLIC_KEY", "").strip()
        secret = os.environ.get("LANGFUSE_SECRET_KEY", "").strip()
        if self.mode != "llm" or not public or not secret:
            return
        try:
            from langfuse import Langfuse
        except ImportError:
            self._warn("Decisions tracking unavailable; install scraper[langfuse].")
            return
        except Exception:
            self._warn("Decisions tracking initialization failed; crawling continues.")
            return
        try:
            if self.context.trace_id is not None and not _valid_id(self.context.trace_id, 32):
                raise ValueError("Invalid trace identifier")
            if self.context.parent_observation_id is not None and (
                self.context.trace_id is None
                or not _valid_id(self.context.parent_observation_id, 16)
            ):
                raise ValueError("Parent observation requires a valid trace")
            options = {"public_key": public, "secret_key": secret}
            base_url = os.environ.get("LANGFUSE_BASE_URL", "").strip()
            if base_url:
                options["base_url"] = base_url
            self._client = Langfuse(**options)
            trace_context = {
                "trace_id": self.context.trace_id
                or hashlib.sha256(self.context.crawl_id.encode()).hexdigest()[:32]
            }
            if self.context.parent_observation_id is not None:
                trace_context["parent_span_id"] = self.context.parent_observation_id
            started = utc_now()
            self._parent = self._client.start_observation(
                as_type="span", name="scraper.decisions", trace_context=trace_context,
                metadata=self._metadata(),
            )
            if not _valid_id(self._parent.trace_id, 32) or not _valid_id(self._parent.id, 16):
                raise ValueError("SDK did not create a recording observation")
            self.reference = DecisionTrackingReference(
                crawl_id=self.context.crawl_id, trace_id=self._parent.trace_id,
                observation_id=self._parent.id, model=self.model, started_at=started,
                research_run_id=self.context.research_run_id,
            )
        except Exception:
            self._warn("Decisions tracking initialization failed; crawling continues.")

    def start_attempt(self, *, operation: str, attempt: int, sequence: int) -> Any:
        if self.reference is None:
            return None
        try:
            return self._client.start_observation(
                as_type="generation", name=f"scraper.decisions.{operation}", model=self.model,
                trace_context={
                    "trace_id": self.reference.trace_id,
                    "parent_span_id": self.reference.observation_id,
                },
                metadata={
                    **self._metadata(), "operation": operation, "attempt": attempt,
                    "request_sequence": sequence,
                },
            )
        except Exception:
            self._warn("Decisions tracking observation failed; crawling continues.")
            return None

    def finish_attempt(
        self, observation: Any, *, outcome: str, input_tokens: int | None = None
    ) -> None:
        if observation is None:
            return
        try:
            values: dict[str, Any] = {
                "level": "DEFAULT" if outcome == "success" else "ERROR",
                "status_message": outcome,
            }
            if type(input_tokens) is int and input_tokens >= 0:
                values["usage_details"] = {"input": input_tokens}
            observation.update(**values)
        except Exception:
            self._warn("Decisions tracking update failed; crawling continues.")
        finally:
            self._end(observation)

    def _end(self, observation: Any) -> None:
        try:
            observation.end()
        except Exception:
            self._warn("Decisions tracking cleanup failed; crawling continues.")

    def finish(self) -> None:
        if self._finished:
            return
        self._finished = True
        if self._parent is not None:
            self._end(self._parent)
            self._parent = None
        if self.reference is not None:
            self.reference = self.reference.model_copy(update={"finished_at": utc_now()})

    async def aclose(self) -> None:
        self.finish()
        if self._client is None or self._flushed:
            return
        self._flushed = True
        try:
            # A timed-out SDK flush can finish in its worker; never block the event loop.
            await asyncio.wait_for(
                asyncio.to_thread(self._client.flush), timeout=FLUSH_TIMEOUT_SECONDS
            )
        except Exception:
            self._warn("Decisions tracking flush failed or timed out; crawling continues.")
