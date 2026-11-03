"""Explicitly owned OpenAI Decisions adapter for adaptive crawl selection."""

from __future__ import annotations

import asyncio
import logging
import os
import random
import time
from collections import Counter
from collections.abc import Awaitable, Callable, Sequence
from typing import TYPE_CHECKING, Annotated, Literal

from openai import APIConnectionError, APIError, APIStatusError, APITimeoutError, AsyncOpenAI
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .config import SelectionConfig
from .decision_budget import MAX_REQUEST_BYTES, DecisionBudget, request_size
from .decision_models import (
    CandidateScore,
    CollectedExcerpt,
    DecisionCandidate,
    DecisionFallback,
    DecisionTraceRecord,
    DecisionUsageSummary,
    PageAssessment,
    UnitScore,
)
from .decision_prompts import candidate_batches, candidate_request, content_request
from .decision_tracking import DecisionTracker, DecisionTrackingContext, DecisionTrackingReference
from .reliability import TRANSIENT_STATUSES, parse_retry_after
from .types import PageCategory

if TYPE_CHECKING:
    from .models import FetchedPage

logger = logging.getLogger(__name__)


class DecisionAccessError(ValueError):
    """Missing credentials or rejected authentication/access; never silently downgraded."""


def validate_decision_credentials(settings: SelectionConfig) -> None:
    """Call before starting browser/network work when composing the service."""
    if settings.mode == "llm" and not os.environ.get("OPENAI_API_KEY", "").strip():
        raise DecisionAccessError(
            "LLM selection requires OPENAI_API_KEY. Add it to the loaded .env file "
            "(or process environment), or choose "
            "selection.mode='structural' for API-free crawling."
        )


class _Answer(BaseModel):
    model_config = ConfigDict(extra="ignore", allow_inf_nan=False)
    name: str


class _ScoreAnswer(_Answer):
    type: Literal["score"]
    score: Annotated[float, Field(strict=True, ge=0, le=3)]
    confidence: UnitScore


class _ChoiceAnswer(_Answer):
    type: Literal["choice"]
    choice: PageCategory
    confidence: UnitScore


class _RefusalAnswer(_Answer):
    type: Literal["refusal"]


def _validate_answers(payload: dict, answers: list) -> dict[str, dict]:
    counts = Counter(
        item.get("name")
        for item in answers
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    )
    by_name = {
        item.get("name"): item
        for item in answers
        if isinstance(item, dict) and isinstance(item.get("name"), str)
    }
    validated = {}
    for question in payload["questions"]:
        name = question["name"]
        raw = by_name.get(name, {})
        model = (
            _RefusalAnswer
            if raw.get("type") == "refusal"
            else (_ScoreAnswer if question["type"] == "score" else _ChoiceAnswer)
        )
        try:
            if counts[name] != 1:
                raise ValueError("Missing or duplicate answer")
            validated[name] = model.model_validate(raw).model_dump(mode="json")
        except (ValidationError, ValueError):
            validated[name] = {"name": name, "type": "invalid"}
    return validated


class OpenAIDecisionProvider:
    """One competitor's budget/state, optionally sharing a run-wide request semaphore.

    Tests may inject the SDK client factory and sleeper. Production creates its own
    AsyncOpenAI client and closes it on normal exit, errors, and cancellation.
    """

    def __init__(
        self,
        settings: SelectionConfig,
        *,
        request_limiter: asyncio.Semaphore | None = None,
        client_factory: Callable = AsyncOpenAI,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        tracking_context: DecisionTrackingContext | None = None,
    ):
        self.settings = settings
        self._limiter = request_limiter or asyncio.Semaphore(settings.max_concurrency)
        self._factory = client_factory
        self._sleep = sleep
        self._budget = DecisionBudget(settings.budget_usd)
        self._effective_mode = settings.mode
        self._fallback_events: list[DecisionFallback] = []
        self._trace: list[DecisionTraceRecord] = []
        self._client = None
        self._opened = False
        self._used = False
        self._fatal: DecisionAccessError | None = None
        self._operation_lock = asyncio.Lock()
        self._tracker = DecisionTracker(
            mode=settings.mode, model=settings.model, context=tracking_context
        )

    async def __aenter__(self):
        if self._used:
            raise RuntimeError("A decision provider belongs to one competitor crawl only")
        validate_decision_credentials(self.settings)
        if self.settings.mode == "llm":
            self._client = self._factory(
                api_key=os.environ["OPENAI_API_KEY"].strip(),
                max_retries=0,
                timeout=self.settings.timeout_seconds,
            )
        self._tracker.start()
        self._used = self._opened = True
        return self

    async def __aexit__(self, *_):
        try:
            if self._client is not None:
                await self._client.close()
        finally:
            self._client = None
            self._opened = False
            await self._tracker.aclose()

    @property
    def decision_tracking(self) -> DecisionTrackingReference | None:
        return self._tracker.reference

    @property
    def summary(self) -> DecisionUsageSummary:
        return DecisionUsageSummary(
            requested_mode=self.settings.mode,
            effective_mode=self._effective_mode,
            budget_usd=self.settings.budget_usd,
            request_count=self._budget.request_count,
            reported_input_tokens=self._budget.reported_input_tokens,
            uncertain_input_tokens=self._budget.uncertain_input_tokens,
            reserved_input_tokens=self._budget.reserved_tokens,
            estimated_cost_usd=self._budget.estimated_cost_usd,
            fallback_events=[item.model_copy(deep=True) for item in self._fallback_events],
        )

    @property
    def trace_records(self) -> list[DecisionTraceRecord]:
        return [item.model_copy(deep=True) for item in self._trace]

    def _check_open(self):
        if not self._opened:
            raise RuntimeError("Use the decision provider as an async context manager")
        if self._fatal:
            raise self._fatal

    def _fallback(self, reason: str) -> None:
        if self._effective_mode == "llm":
            self._effective_mode = "structural"
            self._fallback_events.append(DecisionFallback(reason=reason))
            logger.warning("Decision provider switched to structural selection: %s", reason)

    def _record(self, **values):
        if self.settings.trace_enabled:
            self._trace.append(DecisionTraceRecord(**values))

    async def _request(self, payload: dict, operation: str) -> dict[str, dict] | None:
        # Serializing one competitor's calls keeps fallback transitions and budget
        # reservations consistent; different competitors share only _limiter.
        async with self._operation_lock:
            self._check_open()
            if self._effective_mode == "structural":
                return None
            if request_size(payload) > MAX_REQUEST_BYTES:
                self._fallback("request_too_large")
                return None
            for attempt in range(1, self.settings.max_retries + 2):
                retry = False
                delay = 0.0
                async with self._limiter:
                    reservation = self._budget.reserve(payload)
                    if reservation is None:
                        self._fallback("budget_exhausted")
                        return None
                    started = time.monotonic()
                    common = {"operation": operation, "attempt": attempt, "request": payload}
                    observation = self._tracker.start_attempt(
                        operation=operation, attempt=attempt, sequence=self._budget.request_count
                    )
                    outcome = "invalid_response"
                    reported_tokens = None
                    try:
                        async with asyncio.timeout(self.settings.timeout_seconds):
                            response = await self._client.decisions.create(**payload)
                    except BaseException as error:
                        self._budget.settle(reservation, reported_input_tokens=None)
                        status = error.status_code if isinstance(error, APIStatusError) else None
                        code = (
                            f"http_{status}"
                            if status
                            else (
                                "cancelled"
                                if isinstance(error, asyncio.CancelledError)
                                else "api_error"
                            )
                        )
                        outcome = (
                            "timeout"
                            if isinstance(error, (TimeoutError, APITimeoutError))
                            else code
                        )
                        self._record(
                            **common,
                            duration_seconds=time.monotonic() - started,
                            uncertain_input_tokens=reservation.tokens,
                            error_code=code,
                        )
                        if isinstance(error, asyncio.CancelledError):
                            raise
                        if status in (401, 403):
                            self._fatal = DecisionAccessError(
                                f"OpenAI Decisions access failed (HTTP {status}). Check "
                                "OPENAI_API_KEY and model/project access."
                            )
                            raise self._fatal from None
                        if not isinstance(error, (APIError, TimeoutError, OSError)):
                            raise
                        retry = status in TRANSIENT_STATUSES or isinstance(
                            error, (APIConnectionError, TimeoutError, ConnectionError)
                        )
                        after = parse_retry_after(
                            error.response.headers.get("retry-after")
                            if isinstance(error, APIStatusError)
                            else None
                        )
                        retry &= attempt <= self.settings.max_retries
                        retry &= after is None or after <= 30
                        if retry:
                            delay = max(
                                random.uniform(0.5, 1.0) * min(30, 2 ** min(attempt - 1, 5)),
                                after or 0,
                            )
                    else:
                        data = (
                            response.model_dump() if isinstance(response, BaseModel) else response
                        )
                        usage = data.get("usage") if isinstance(data, dict) else None
                        tokens = usage.get("input_tokens") if isinstance(usage, dict) else None
                        valid_usage = type(tokens) is int and tokens >= 0
                        reported_tokens = tokens if valid_usage else None
                        self._budget.settle(
                            reservation, reported_input_tokens=tokens if valid_usage else None
                        )
                        raw_answers = data.get("answers") if isinstance(data, dict) else None
                        answers = (
                            _validate_answers(payload, raw_answers)
                            if isinstance(raw_answers, list)
                            else {}
                        )
                        valid = valid_usage and isinstance(raw_answers, list)
                        outcome = (
                            "success"
                            if valid and all(a["type"] != "invalid" for a in answers.values())
                            else "invalid_response"
                        )
                        self._record(
                            **common,
                            duration_seconds=time.monotonic() - started,
                            answers=list(answers.values()),
                            reported_input_tokens=tokens if valid_usage else None,
                            uncertain_input_tokens=0 if valid_usage else reservation.tokens,
                            error_code=None if valid else "invalid_response",
                        )
                        if not valid:
                            self._fallback("invalid_response")
                            return None
                        return answers
                    finally:
                        self._tracker.finish_attempt(
                            observation, outcome=outcome, input_tokens=reported_tokens
                        )
                # Capacity is released before waiting, and every retry reserves anew.
                if retry:
                    await self._sleep(delay)
                else:
                    self._fallback("api_unavailable")
                    return None
        return None

    async def score_candidates(
        self, candidates: Sequence[DecisionCandidate]
    ) -> list[CandidateScore]:
        self._check_open()
        ids = [item.candidate_id for item in candidates]
        if len(ids) != len(set(ids)):
            raise ValueError("Decision candidate IDs must be unique")
        result = []
        for batch in candidate_batches(candidates, self.settings):
            answers = await self._request(candidate_request(batch, self.settings), "candidates")
            for index, candidate in enumerate(batch):
                answer = answers.get(f"candidate_{index}", {}) if answers is not None else {}
                kind = answer.get("type")
                result.append(
                    CandidateScore(
                        candidate_id=candidate.candidate_id,
                        score=answer["score"] / 3 if kind == "score" else None,
                        confidence=answer.get("confidence") if kind == "score" else None,
                        status="scored"
                        if kind == "score"
                        else (
                            "refused"
                            if kind == "refusal"
                            else ("invalid" if answers is not None else "unavailable")
                        ),
                    )
                )
        return result

    async def assess_content(
        self, page: FetchedPage, ledger: Sequence[CollectedExcerpt] = ()
    ) -> PageAssessment:
        self._check_open()
        answers = await self._request(content_request(page, ledger, self.settings), "content")
        values = {}
        issues = {}
        for name in ("usefulness", "novelty", "category"):
            answer = answers.get(name, {}) if answers is not None else {}
            kind = answer.get("type")
            if kind in ("score", "choice"):
                values[name] = answer["choice"] if name == "category" else answer["score"] / 3
                values[f"{name}_confidence"] = answer["confidence"]
            else:
                issues[name] = (
                    "unavailable"
                    if answers is None
                    else ("refused" if kind == "refusal" else "invalid")
                )
        return PageAssessment(**values, issues=issues)
