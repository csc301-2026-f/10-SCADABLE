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

class AnalysisPage(BaseModel):
    """One exported page's metadata alongside its unchanged Markdown."""

    source: ExportedPage
    markdown: str


class AnalysisInput(BaseModel):
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
    A url-based version of a cited job (use this for converting the job citations from ints to strings).
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
    """One competitor feature scored 0-10, by company name. Use this after the LLM output has been converted."""
    company_name: str
    feature: str
    score: Score

class PartialExtractedInformation(BaseModel):
    """Parent schema for LLM prompting (Never explicitly use this for the output schema)."""
    summary: str


class PartialExtractedInformationByIndex(PartialExtractedInformation):
    """Used for the LLM output schema (lower chances of made-up links)."""
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

class CompanyAnalysisByIndex(BaseModel):
    company_index: int
    company_name: str
    summary: str
    features: list[str]
    claims_cited: list[ClaimCitedByIndex]


class CompetitorResearchResult(BaseModel):
    """Analysis and extracted facts with the scraper's original run provenance."""

    crawl_summary: CrawlExportSummary
    extracted_information: ExtractedInformationByURL


class ComparisonInput(BaseModel):
    scadable: ExtractedInformationByURL
    competitors: list[ExtractedInformationByURL]

class ComparisonInputByIndex(BaseModel):
    scadable: ExtractedInformationByIndex
    competitors: list[ExtractedInformationByIndex]

class CompetitorNote(BaseModel):
    """Short explanation of what one competitor means for SCADABLE."""
    company_name: str
    why_it_matters: str


class SWOTEntry(BaseModel):
    component: SWOTComponent
    what: str

class SWOTIndex(BaseModel):
    swot_component_company_index: int
    swot_component_page_index: int

class SWOTEntryByIndex(SWOTEntry):
    swot_citations: list[SWOTIndex]

class SWOTEntryByURL(SWOTEntry):
    swot_component_page_urls: list[str]

# NOTE THIS IS THE BASE CLASS FOR THE LLM OUTPUT
class SWOTAnalysis(BaseModel):
    competitor_notes: list[CompetitorNote]

# NOTE THIS IS ONLY USED FOR THE LLM OUTPUT
class SWOTAnalysisByIndex(SWOTAnalysis):
    SWOT_entries: list[SWOTEntryByIndex]
    scores: list[FeatureScoreByIndex]

# NOTE THIS IS USED FOR THE FINAL OUTPUT
class SWOTAnalysisByURL(SWOTAnalysis):
    SWOT_entries: list[SWOTEntryByURL]
    scores: list[FeatureScore]
    conflicting_data: list[ConflictEntry]
