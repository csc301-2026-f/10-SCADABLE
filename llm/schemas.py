"""Source-backed analysis inputs and structured competitor insights."""

from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, Field

from scraper import CrawlExportSummary, ExportedPage

Category = Literal[
    "feature",
    "target_customers",
    "pricing",
    "integrations",
    "differentiator",
    "strength",
    "weakness",
    "workforce_improvement",
    "workforce_decline"
]

Family = Literal[
    "part-time", 
    "full-time",
    "co-op/internship",
    "apprenticeship",
    "other"
]

SWOTComponent = Literal[
    "strength",
    "weakness",
    "opportunity",
    "threat"
]

ExtractionKeys = Literal[
    "summary", 
    "job_postings",
    "claims_cited",
    "conflicting_data"
]

SWOTKeys = Literal[
    "SWOT_entries", 
    "scores", 
    "competitor_swots",
    "features",
    "competitor_notes"
]

class AnalysisPage(BaseModel):
    """One exported page's metadata alongside its unchanged Markdown."""

    source: ExportedPage
    markdown: str


class AnalysisInput(BaseModel):
    """
    Input for all the pages that were crawled (used for the data extraction).
    """
    crawl_summary: CrawlExportSummary
    pages: list[AnalysisPage] = Field(min_length=1)

    @property
    def company_name(self) -> str:
        return self.crawl_summary.competitor_name

class JobFamily(BaseModel):
    """
    Represents a job family (used for JobCited class).
    """
    job_family: Family
    count: int

class JobCited(BaseModel):
    """
    Represents each job posting as a general category, providing inputs on the role, category, 
    job families this role is divided into, and the number of roles per job family.

    NEVER use this for the LLM's output schema.
    """
    role: str 
    category: str
    job_families: list[JobFamily]

class JobCitedByIndex(JobCited):
    """
    An index-based version of a cited job (Use this for the LLM output schema).
    """
    job_page_index: int

class JobCitedByURL(JobCited):
    """
    A url-based version of a cited job (use this when converting the LLM outputs into final function outputs).
    """
    job_page_url: str


class ClaimCited(BaseModel):
    """One fact or finding about a company, with the page that supports it.
    
    NEVER use this for the LLM's output schema.
    """
    category: Category
    claim: str

class ClaimCitedByIndex(ClaimCited):
    """
    A claim that is cited by page index. Use this for the LLM's output schema.
    """
    claim_page_index: int

class ClaimCitedByURL(ClaimCited):
    """
    A claim cited by URL. Use this after the LLM's output has been produced.
    """
    claim_page_url: str


def _check_score_range(value: int) -> int:
    """
    Checks if the score of a feature is in a valid range.
    """
    if not 0 <= value <= 10:
        raise ValueError("score must be between 0 and 10")
    return value


class ConflictCited(BaseModel):
    """
    Contains the conflicting data found (claim 1 and claim 2 conflict).
    """
    claim_a: str
    claim_b: str

class ConflictCitedByURL(ConflictCited):
    """
    Contains the conflicting data found, along with its citations (only used for final output).
    """
    claim_a_page_URL: str
    claim_b_page_URL: str

class ConflictCitedByIndex(ConflictCited):
    """
    Contains the conflicting data found, along with its citations (only used for LLM output).
    """
    claim_a_page_index: int
    claim_b_page_index: int


class ConflictEntry(BaseModel):
    """
    A model containing all the conflicting pieces of data found for a company.
    """
    company_name: str
    conflicts: list[ConflictCitedByURL]


# AfterValidator (not Field limits) keeps the range out of the JSON schema sent to
# OpenAI, whose strict mode does not accept every numeric keyword.
Score = Annotated[int, AfterValidator(_check_score_range)]


class FeatureScoreByIndex(BaseModel):
    """One competitor feature scored 0-10, by company index. Use this for the LLM output schema."""

    company_index: int
    feature: str
    score: Score


class FeatureScore(BaseModel):
    """One competitor feature scored 0-10, by company name. Use this after the LLM had made its output."""
    company_name: str
    feature: str
    score: Score

class PartialExtractedInformation(BaseModel):
    """Parent schema for data extraction LLM prompting (Never use this for the LLM output schema)."""
    summary: str


class PartialExtractedInformationByIndex(PartialExtractedInformation):
    """Used to get all **new** information from the LLM and 
    uses integers to cite the pages instead of copying URLs, reducing chance of hallucinations."""
    summary_page_indexes: list[int]
    job_postings: list[JobCitedByIndex]
    claims_cited: list[ClaimCitedByIndex]
    conflicting_data: list[ConflictCitedByIndex]

    def claims_in(self, category: Category) -> list[str]:
        return [c.claim for c in self.claims_cited if c.category == category]

class ExtractedInformation(PartialExtractedInformation):
    """Facts extracted from source pages before interpretation. Converts all "index" values into their URLs."""
    company_name: str

class ExtractedInformationByURL(ExtractedInformation):
    """Used for LLM output conversion"""
    summary_page_urls: list[str]
    job_postings: list[JobCitedByURL]
    claims_cited: list[ClaimCitedByURL]
    conflicting_data: ConflictEntry
    crawl_time_string: str

class ExtractedInformationByIndex(ExtractedInformation):
    """Used for input conversion for comparison LLM prompt."""
    company_index: int
    job_postings: list[JobCitedByIndex]
    claims_cited: list[ClaimCitedByIndex]

class CompetitorResearchResult(BaseModel):
    """Analysis and extracted facts with the scraper's original run provenance."""

    crawl_summary: CrawlExportSummary
    extracted_information: ExtractedInformationByURL

class ComparisonInput(BaseModel):
    """
    Contains input of extracted data from SCADABLE and competitors.
    """
    scadable: ExtractedInformationByURL
    competitors: list[ExtractedInformationByURL]

class ComparisonInputByIndex(BaseModel):
    """
    LLM input with data citations using numerical indexing instead of URLs.
    """
    scadable: ExtractedInformationByIndex
    competitors: list[ExtractedInformationByIndex]

class CompetitorNote(BaseModel):
    """Short explanation of what one competitor means for SCADABLE."""
    company_name: str
    why_it_matters: str

class SWOTFeedback(BaseModel):
    """
    Contains feedback for specific high-level fields in the SWOT analysis that can be fixed.
    """
    what: SWOTKeys
    why: str

class SWOTFeedbacks(BaseModel):
    """
    A short feedback for the SWOT analysis, containing the names of the fields which had "bad outputs," along with 
    how they can be corrected.
    """
    feedbacks: list[SWOTFeedback]

class ExtractionFeedback(BaseModel):
    """
    a short feedback model for the company extraction.
    """
    what: ExtractionKeys
    why: str

class ExtractionFeedbacks(BaseModel):
    """
    lists down all the feedback of failed output fields from the LLM response.
    """
    feedbacks: list[ExtractionFeedback]

class FullExtractionFeedback(BaseModel):
    """
    lists down every feedback including citation indexing violations from the LLM output for data extraction.
    """
    feedback: ExtractionFeedbacks
    citation_violations: list[tuple[str, int]]

class FullSWOTFeedback(BaseModel):
    """
    lists down every feedback including citation violations from the LLM output for the final SWOT analysis.
    """
    feedback: SWOTFeedbacks
    swot_citation_violations: list[str]
    feature_score_violations: list[str]
    competitor_swot_violations: list[str]
    feature_pair_violations: list[str]

class SWOTEntry(BaseModel):
    """
    A SWOT analysis entry.
    """
    component: SWOTComponent
    what: str

class SWOTIndex(BaseModel):
    """
    The citation to a specific SWOT entry.
    """
    swot_component_company_index: int
    swot_component_page_index: int

class SWOTEntryByIndex(SWOTEntry):
    """
    A SWOT entry with index-based citations.
    """
    swot_citations: list[SWOTIndex]

class SWOTEntryByURL(SWOTEntry):
    """
    A SWOT entry with URL-based citations.
    """
    swot_component_page_urls: list[str]

class SWOTAnalysis(BaseModel):
    """
    The base class for a SWOT analysis output (NEVER use this in the LLM output).
    """
    competitor_notes: list[CompetitorNote]

class CompetitorSWOTByIndex(BaseModel):
    """Competitor-owned findings, produced in the same comparison request."""
    company_index: int
    SWOT_entries: list[SWOTEntryByIndex]

class CompetitorSWOTByURL(BaseModel):
    """
    competitor-owned findings, with URL-based citations. 
    """
    company_name: str
    SWOT_entries: list[SWOTEntryByURL]


class FeatureByIndex(BaseModel):
    """An explicitly comparable pair of already-scored features."""
    company_index: int
    competitor_feature: str
    scadable_feature: str | None


class Feature(BaseModel):
    """
    A feature from a company, compared with SCADABLE.
    """
    company_name: str
    competitor_feature: str
    scadable_feature: str | None


class SWOTAnalysisByIndex(SWOTAnalysis):
    """
    A full SWOT analysis with index-based citations (used in LLM output only)
    """
    SWOT_entries: list[SWOTEntryByIndex] = Field(default_factory=list)
    scores: list[FeatureScoreByIndex] = Field(default_factory=list)
    competitor_swots: list[CompetitorSWOTByIndex] = Field(default_factory=list)
    features: list[FeatureByIndex] = Field(default_factory=list)

class SWOTAnalysisByURL(SWOTAnalysis):
    """
    A full SWOT analysis with URL-based citations (used in the final return statement of the outer function).
    """
    SWOT_entries: list[SWOTEntryByURL]
    scores: list[FeatureScore]
    conflicting_data: list[ConflictEntry]
    competitor_swots: list[CompetitorSWOTByURL] = Field(default_factory=list)
    features: list[Feature] = Field(default_factory=list)
