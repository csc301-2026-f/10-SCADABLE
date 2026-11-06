"""Structured analysis entry points with source-citation validation."""

from llm.client import OpenAIClient
from llm.prompts.comparison import (
    COMPARISON_SYSTEM_PROMPT,
    build_comparison_user_prompt,
)
from llm.prompts.competitor import (
    DATA_EXTRACTION_SYSTEM_PROMPT,
    build_extraction_user_prompt,
)
from llm.schemas import (
    AnalysisInput,
    ClaimCitedByURL,
    ClaimCitedByIndex,
    ConflictCitedByURL,
    ConflictEntry,
    PartialExtractedInformationByIndex,
    ComparisonInput,
    ComparisonInputByIndex,
    JobCitedByIndex,
    JobCitedByURL,
    ExtractedInformationByIndex,
    ExtractedInformationByURL,
    FeatureScore,
    FeatureScoreByIndex,
    SWOTAnalysisByIndex,
    SWOTAnalysisByURL,
    SWOTEntryByURL,
    SWOTEntryByIndex
)

def _validate_swot_citations(company_urls: list[list[str]], entries: list[SWOTEntryByIndex]) -> None:
    for entry in entries:
        if not entry.swot_citations:
            raise ValueError("A SWOT entry must cite at least one page.")
        for c in entry.swot_citations:
            company, page = c.swot_component_company_index, c.swot_component_page_index
            if not 0 <= company < len(company_urls):
                raise ValueError(f"Company number {company} does not exist.")
            if not 0 <= page < len(company_urls[company]):
                raise ValueError(f"Page number {page} does not exist for company {company}.")
        if entry.component in ("strength", "weakness") and not any(
            c.swot_component_company_index == 0 for c in entry.swot_citations
        ):
            raise ValueError("A strength or weakness must cite at least one SCADABLE page.")

def _validate_feature_scores(company_count: int, scores: list[FeatureScoreByIndex]) -> None:
    """Scores belong to competitors only: company 0 is SCADABLE and is the baseline, never scored."""
    for score in scores:
        if not 1 <= score.company_index < company_count:
            raise ValueError(
                f"Score company index {score.company_index} is not a competitor "
                f"(valid: 1 to {company_count - 1})."
            )

def _extraction_by_index(position: int, company: ExtractedInformationByURL, url_index: list[dict[str, int]]) -> ExtractedInformationByIndex:
    """
    A helper function to build an Extract
    """
    return ExtractedInformationByIndex(
        company_index=position,
        company_name=company.company_name,
        job_postings=[
            JobCitedByIndex(
                role=j.role, 
                category=j.category,
                job_families=j.job_families,
                job_page_index=url_index[position][j.job_page_url]   
            )
            for j in company.job_postings
        ],
        claims_cited=[
            ClaimCitedByIndex(
                category=c.category,
                claim=c.claim,
                claim_page_index=url_index[position][c.claim_page_url],
            )
            for c in company.claims_cited
        ]
    )

def _validate_citations(*, last_index: int, index_directory: dict[str, list]):
    for what, indexes in index_directory.items():
        for i in indexes or []:
            if not 0 <= i <= last_index:
                raise ValueError(f"{what} page index {i} is out of range (0 to {last_index}).")


class Analyzer:
    def __init__(self, client: OpenAIClient, trace_prefix: str | None = None) -> None:
        """trace_prefix names each call "{prefix}:{step}" for cost lookups (e.g. "run-42");
        without it, calls are named from the company and crawl time."""
        self.client = client
        self.trace_prefix = trace_prefix

    def _observation_name(self, step: str, default: str) -> str:
        return f"{self.trace_prefix}:{step}" if self.trace_prefix else default

    async def extract_information(self, data: AnalysisInput) -> ExtractedInformationByURL:
        page_urls = [str(p.source.url) for p in data.pages]
        crawl_time_str = data.crawl_summary.finished_at.strftime("%Y-%m-%d %H:%M:%S")
        partial = await self.client.generate_structured(
            instructions=DATA_EXTRACTION_SYSTEM_PROMPT,
            input_text=build_extraction_user_prompt(data),
            output_schema=PartialExtractedInformationByIndex,
            observation_name=self._observation_name(
                "extraction", data.company_name + "-extraction-" + crawl_time_str
            ),
        )
        
        _validate_citations(
            last_index=len(page_urls) - 1,
            index_directory={"Claim": [p.claim_page_index for p in partial.claims_cited], 
                            "Job posting": [p.job_page_index for p in partial.job_postings],
                            "Summary": partial.summary_page_indexes,
                            "Conflicting data": [c.claim_a_page_index for c in partial.conflicting_data] + [c.claim_b_page_index for c in partial.conflicting_data]
                            }

        )
        result = ExtractedInformationByURL(company_name=data.company_name, 
                                      summary=partial.summary,
                                      summary_page_urls=[page_urls[p] for p in partial.summary_page_indexes], 
                                      job_postings=[JobCitedByURL(role=job.role, category=job.category, job_families=job.job_families, job_page_url=page_urls[job.job_page_index]) for job in partial.job_postings],
                                      claims_cited=[ClaimCitedByURL(category=c.category, claim=c.claim, claim_page_url=page_urls[c.claim_page_index]) for c in partial.claims_cited],
                                      conflicting_data=ConflictEntry(company_name=data.company_name, conflicts=[ConflictCitedByURL(claim_a=c.claim_a, claim_a_page_URL=page_urls[c.claim_a_page_index], claim_b=c.claim_b, claim_b_page_URL=page_urls[c.claim_b_page_index]) for c in partial.conflicting_data]),
                                      crawl_time_string=crawl_time_str
                                      )
        return result

    async def compare(self, data: ComparisonInput) -> SWOTAnalysisByURL:
        companies = [data.scadable, *data.competitors]
        company_urls = [list(dict.fromkeys([c.claim_page_url for c in company.claims_cited] + [j.job_page_url for j in company.job_postings]))
                        for company in companies]

        url_index = [{url: i for i, url in enumerate(urls)} for urls in company_urls]

        llm_input = ComparisonInputByIndex(
            scadable=_extraction_by_index(0, data.scadable, url_index),
            competitors=[_extraction_by_index(p, c, url_index)
                         for p, c in enumerate(data.competitors, start=1)],
        )

        response = await self.client.generate_structured(
            instructions=COMPARISON_SYSTEM_PROMPT,
            input_text=build_comparison_user_prompt(llm_input),
            output_schema=SWOTAnalysisByIndex,
            observation_name=self._observation_name(
                "comparison", "SWOT-" + data.scadable.crawl_time_string
            ),
        )
        _validate_swot_citations(company_urls, response.SWOT_entries)
        _validate_feature_scores(len(companies), response.scores)

        return SWOTAnalysisByURL(
            competitor_notes=response.competitor_notes,
            SWOT_entries=[
                SWOTEntryByURL(
                    component=swot.component,
                    what=swot.what,
                    swot_component_page_urls=[
                        company_urls[c.swot_component_company_index][c.swot_component_page_index]
                        for c in swot.swot_citations
                    ],
                )
                for swot in response.SWOT_entries
            ],
            scores=[
                FeatureScore(
                    company_name=companies[score.company_index].company_name,
                    feature=score.feature,
                    score=score.score,
                )
                for score in response.scores
            ],
            conflicting_data=[data.scadable.conflicting_data] + [c.conflicting_data for c in data.competitors]
        )

