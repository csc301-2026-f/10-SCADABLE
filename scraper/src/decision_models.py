"""Application-owned evidence and outcomes; no OpenAI or Crawl4AI imports."""

from typing import Annotated, Literal

from pydantic import Field, JsonValue

from .types import ContractModel, NonEmptyText, PageCategory, UtcTimestamp, WebUrl, utc_now

UnitScore = Annotated[float, Field(strict=True, ge=0, le=1)]
TokenCount = Annotated[int, Field(strict=True, ge=0)]
CandidateId = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_-]+$")]
MODEL = "gpt-6-luna"
PROMPT_VERSION = "company-research-v2"


class CandidateObservation(ContractModel):
    parent_url: WebUrl | None = None
    anchor_text: Annotated[str, Field(max_length=160)] = ""
    heading: Annotated[str, Field(max_length=160)] = ""
    surrounding_text: Annotated[str, Field(max_length=400)] = ""
    location: Literal["navigation", "main_content", "footer", "unknown"] = "unknown"


class DecisionCandidate(ContractModel):
    candidate_id: CandidateId
    url: WebUrl
    discovery_source: NonEmptyText = "internal_link"
    observations: Annotated[list[CandidateObservation], Field(max_length=2)] = Field(
        default_factory=list
    )


class CollectedExcerpt(ContractModel):
    source_url: WebUrl
    text: Annotated[str, Field(max_length=500)]
    page_title: Annotated[str, Field(max_length=160)] = ""


class CandidateScore(ContractModel):
    candidate_id: CandidateId
    score: UnitScore | None = None
    confidence: UnitScore | None = None
    status: Literal["scored", "refused", "invalid", "unavailable"] = "unavailable"
    reason: NonEmptyText | None = None


class PageAssessment(ContractModel):
    usefulness: UnitScore | None = None
    novelty: UnitScore | None = None
    category: PageCategory = PageCategory.OTHER
    usefulness_confidence: UnitScore | None = None
    novelty_confidence: UnitScore | None = None
    category_confidence: UnitScore | None = None
    issues: dict[
        Literal["usefulness", "novelty", "category"],
        Literal["refused", "invalid", "unavailable"],
    ] = Field(default_factory=dict)


class SelectionDiagnostics(ContractModel):
    method: Literal["homepage", "ranked", "exploration", "structural", "bfs"]
    relevance_score: UnitScore | None = None
    assessment_available: bool = False


class DecisionFallback(ContractModel):
    reason: NonEmptyText
    occurred_at: UtcTimestamp = Field(default_factory=utc_now)


class DecisionUsageSummary(ContractModel):
    requested_mode: Literal["llm", "structural"]
    effective_mode: Literal["llm", "structural"]
    model: str = MODEL
    prompt_version: str = PROMPT_VERSION
    budget_usd: Annotated[float, Field(strict=True, ge=0)]
    request_count: TokenCount = 0
    reported_input_tokens: TokenCount = 0
    uncertain_input_tokens: TokenCount = 0
    reserved_input_tokens: TokenCount = 0
    estimated_cost_usd: Annotated[float, Field(strict=True, ge=0)] = 0.0
    fallback_events: list[DecisionFallback] = Field(default_factory=list)


class DecisionTraceRecord(ContractModel):
    """Bounded evidence and validated answers, never SDK exceptions or credentials."""

    occurred_at: UtcTimestamp = Field(default_factory=utc_now)
    operation: Literal["candidates", "content"]
    attempt: Annotated[int, Field(strict=True, ge=1)]
    request: dict[str, JsonValue]
    answers: list[dict[str, JsonValue]] = Field(default_factory=list)
    duration_seconds: Annotated[float, Field(ge=0)]
    reported_input_tokens: TokenCount | None = None
    uncertain_input_tokens: TokenCount = 0
    error_code: NonEmptyText | None = None
