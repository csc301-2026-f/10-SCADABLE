"""Structured analysis entry points with source-citation validation."""

from llm.client import OpenAIClient
from llm.prompts.comparison import (
    COMPARISON_SYSTEM_PROMPT,
    build_comparison_user_prompt,
)
from llm.prompts.competitor import (
    COMPANY_ANALYSIS_SYSTEM_PROMPT,
    DATA_EXTRACTION_SYSTEM_PROMPT,
    build_company_user_prompt,
    build_extraction_user_prompt,
)
from llm.schemas import (
    AnalysisInput,
    ClaimCitedByURL,
    ClaimCitedByIndex,
    PartialCompanyAnalysisByIndex,
    PartialExtractedInformationByIndex,
    CompanyAnalysis,
    CompanyAnalysisByIndex,
    ComparisonInput,
    ComparisonInputByIndex,
    JobCitedByURL,
    JobCitedByIndex,
    ExtractedInformationByURL,
    ExtractedInformationByIndex,
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

def _company_by_index(position: int, company: CompanyAnalysis, url_index) -> CompanyAnalysisByIndex:
    return CompanyAnalysisByIndex(
        company_index=position,
        company_name=company.company_name,
        summary=company.summary,
        features=company.features,
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
    def __init__(self, client: OpenAIClient) -> None:
        self.client = client

    async def extract_information(self, data: AnalysisInput) -> ExtractedInformationByURL:
        page_urls = [str(p.source.url) for p in data.pages]
        crawl_time_str = data.crawl_summary.finished_at.strftime("%Y-%m-%d %H:%M:%S")
        partial = await self.client.generate_structured(
            instructions=DATA_EXTRACTION_SYSTEM_PROMPT,
            input_text=build_extraction_user_prompt(data),
            output_schema=PartialExtractedInformationByIndex,
            observation_name=data.company_name + "-extraction-" + crawl_time_str
        )
        
        _validate_citations(
            last_index=len(page_urls) - 1,
            index_directory={"Claim": [p.claim_page_index for p in partial.claims_cited], 
                            "Job posting": [p.job_page_index for p in partial.job_postings],
                            "Summary": partial.summary_page_indexes
                            }
        )
        result = ExtractedInformationByURL(company_name=data.company_name, 
                                      summary=partial.summary,
                                      summary_page_urls=[page_urls[p] for p in partial.summary_page_indexes], 
                                      job_postings=[JobCitedByURL(role=job.role, category=job.category, job_families=job.job_families, job_page_url=page_urls[job.job_page_index]) for job in partial.job_postings],
                                      claims_cited=[ClaimCitedByURL(category=c.category, claim=c.claim, claim_page_url=page_urls[c.claim_page_index]) for c in partial.claims_cited],
                                      crawl_time_string=crawl_time_str
                                      )
        return result

    async def analyze_company(self, data: ExtractedInformationByURL) -> CompanyAnalysis:
        """
        Analyzes the company based on its website data extracted and stored "data".
        """
        # Stores all distinct URLs from the input
        urls = list(dict.fromkeys(                  # distinct, in first-seen order
            [c.claim_page_url for c in data.claims_cited]
            + [j.job_page_url for j in data.job_postings]
            + data.summary_page_urls
        ))
        url_index = {url: i for i, url in enumerate(urls)}

        llm_input = ExtractedInformationByIndex(company_name=data.company_name, 
                                                       summary=data.summary, 
                                                       job_postings=[JobCitedByIndex(role=d.role, 
                                                                                     category=d.category, 
                                                                                     job_families=d.job_families,
                                                                                     job_page_index=url_index[d.job_page_url]) for d in data.job_postings],
                                                        claims_cited=[ClaimCitedByIndex(category=c.category, claim=c.claim, claim_page_index=url_index[c.claim_page_url]) for c in data.claims_cited])
        partial = await self.client.generate_structured(
            instructions=COMPANY_ANALYSIS_SYSTEM_PROMPT,
            input_text=build_company_user_prompt(llm_input),
            output_schema=PartialCompanyAnalysisByIndex,
            observation_name=data.company_name + "-analysis-" + data.crawl_time_string
        )
        _validate_citations(
            last_index=len(urls) - 1,
            index_directory={"Claim":[p.claim_page_index for p in partial.claims_cited]}
        )
        result = build_company_analysis(partial, data, urls)
        return result

    async def compare(self, data: ComparisonInput) -> SWOTAnalysisByURL:
        companies = [data.scadable, *data.competitors]
        company_urls = [list(dict.fromkeys(c.claim_page_url for c in company.claims_cited))
                        for company in companies]

        url_index = [{url: i for i, url in enumerate(urls)} for urls in company_urls]

        llm_input = ComparisonInputByIndex(
            scadable=_company_by_index(0, data.scadable, url_index),
            competitors=[_company_by_index(p, c, url_index)
                         for p, c in enumerate(data.competitors, start=1)],
        )

        response = await self.client.generate_structured(
            instructions=COMPARISON_SYSTEM_PROMPT,
            input_text=build_comparison_user_prompt(llm_input),
            output_schema=SWOTAnalysisByIndex,
            observation_name="SWOT-" + data.scadable.crawl_time_string
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
        )


def build_company_analysis(partial: PartialCompanyAnalysisByIndex, data: ExtractedInformationByURL, index_url: list[str]) -> CompanyAnalysis:
    """
    A helper function that returns the partial result into a CompanyAnalysis object, 
    containing the company's name, summary, and source URLs for the summary.
    """
    return CompanyAnalysis(claims_cited=[ClaimCitedByURL(category=c.category, 
                                                         claim=c.claim, claim_page_url=index_url[c.claim_page_index]) for c in partial.claims_cited],  
                           summary=data.summary, 
                           summary_page_urls=data.summary_page_urls,
                           features=[c.claim for c in data.claims_cited if c.category == "features"],
                           company_name=data.company_name,
                           crawl_time_string=data.crawl_time_string
                           )
