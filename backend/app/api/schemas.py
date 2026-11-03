"""Request and response models for the HTTP API.

Field names are snake_case in Python and serialized as camelCase to match the
frontend's ``frontend/lib/api/types.ts``.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel

from app.db.repository import clean_url

# Frontend status vocabulary ("success" where the DB says "completed").
ApiResearchStatus = Literal["pending", "running", "success", "failed"]
Trend = Literal["up", "flat", "down"]
SwotCategory = Literal["strength", "weakness", "opportunity", "threat"]


class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


# ---------------------------------------------------------------------------
# Requests
# ---------------------------------------------------------------------------


class CreateCompetitorInput(ApiModel):
    """Body of ``POST /competitors``."""

    model_config = ConfigDict(
        alias_generator=to_camel, populate_by_name=True, extra="forbid"
    )

    name: str = Field(min_length=1, max_length=255)
    # First source is the competitor's primary website.
    data_sources: list[str] = Field(min_length=1, max_length=20)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Name cannot be blank.")
        return value

    @field_validator("data_sources")
    @classmethod
    def _validate_sources(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values if value and value.strip()]
        if not cleaned:
            raise ValueError("Add at least one data source.")
        for value in cleaned:
            if len(value) > 2048:
                raise ValueError("Data source URLs must be at most 2048 characters.")
            clean_url(value)  # raises ValueError with a readable message
        return cleaned


# ---------------------------------------------------------------------------
# Responses
# ---------------------------------------------------------------------------


class Finding(ApiModel):
    id: str
    competitor_id: str
    competitor_name: str
    category: SwotCategory
    text: str
    source_url: str
    source_label: str
    detected_at: datetime
    is_new: bool | None = None
    is_changed: bool | None = None


class ChartPoint(ApiModel):
    label: str
    value: float


class ScoreSeries(ApiModel):
    label: str
    competitor: float
    # Null when there's no SCADABLE score for this feature.
    scadable: float | None = None


class ActivityItem(ApiModel):
    id: str
    text: str
    detected_at: datetime


class ChartSeries(ApiModel):
    scores: list[ScoreSeries] = []
    activity_over_time: list[ChartPoint] = []
    signals_by_source: list[ChartPoint] = []
    hiring_trend: list[ChartPoint] = []


class CompetitorSummary(ApiModel):
    id: str
    name: str
    website: str
    trend: Trend = "flat"
    research_status: ApiResearchStatus
    last_researched_at: datetime | None
    recent_change_summary: str | None


class CompetitorProfile(CompetitorSummary):
    summary: str
    findings: list[Finding]
    recent_activity: list[ActivityItem]
    open_roles: int | None = None
    next_refresh_at: datetime | None = None
    chart_series: ChartSeries = ChartSeries()


class CreateCompetitorResult(ApiModel):
    competitor: CompetitorSummary
    queued_run_id: str


class SwotOverview(ApiModel):
    is_stale: bool = False
    updated_at: datetime | None
    strengths: list[Finding]
    weaknesses: list[Finding]
    opportunities: list[Finding]
    threats: list[Finding]


class GithubStatus(ApiModel):
    connected: bool
    repository: str | None
    tracked_file: str | None
    status_label: str
    last_synced_at: datetime | None
    recent_change: str | None


class CompanyProfile(ApiModel):
    offerings: list[str]
    positioning_notes: list[str]
    github: GithubStatus


class RunStep(ApiModel):
    name: str
    result: str
    status: ApiResearchStatus


class AgentRun(ApiModel):
    id: str
    competitor_id: str | None
    competitor_name: str
    status: ApiResearchStatus
    cost_usd: float | None = None
    step_count: int
    cache_hits: int | None = None
    cache_misses: int | None = None
    started_at: datetime
    finished_at: datetime | None
    steps: list[RunStep]
    pull_request_url: str | None = None
    # Not in the frontend type yet; shown on failed runs if the UI wants it.
    error_message: str | None = None


class ErrorResponse(BaseModel):
    detail: str
