"""Structured analysis entry points with source-citation validation."""
import os
from dotenv import load_dotenv
from langfuse import LangfuseSpan
from pydantic import BaseModel

from llm.client import OpenAIClient
from llm.prompts.validation import (
    EXTRACTION_VALIDATION_SYSTEM_PROMPT,
    SWOT_VALIDATION_SYSTEM_PROMPT,
    build_swot_validation_user_prompt,
    build_extraction_validation_user_prompt
)
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
    SWOTAnalysisByIndex,
    SWOTAnalysisByURL,
    SWOTEntryByURL,
    SWOTEntryByIndex,
    CompetitorSWOTByURL,
    FeaturePair,
    FullExtractionFeedback, 
    FullSWOTFeedback
)

from llm.validators import *

load_dotenv()
try:
    # Max total model calls per conversation (first attempt included); falls back to 3.
    OPENAI_CALL_LIMIT = int(os.getenv("OPENAI_CALL_LIMIT", "3"))
except ValueError:
    OPENAI_CALL_LIMIT = 3

def _extraction_by_index(position: int, company: ExtractedInformationByURL, url_index: list[dict[str, int]]) -> ExtractedInformationByIndex:
    """
    A helper function to build an Extract
    """
    return ExtractedInformationByIndex(
        company_index=position,
        company_name=company.company_name,
        summary=company.summary,
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



class Analyzer:
    def __init__(self, client: OpenAIClient, trace_prefix: str | None = None) -> None:
        """trace_prefix names each call "{prefix}:{step}" for cost lookups (e.g. "run-42");
        without it, calls are named from the company and crawl time."""
        self.client = client
        self.trace_prefix = trace_prefix

    def _observation_name(self, step: str, default: str) -> str:
        return f"{self.trace_prefix}:{step}" if self.trace_prefix else default

    async def extract_information(self, data: AnalysisInput) -> ExtractedInformationByURL:
        attempt_number = 1
        conversation_id = await self.client.start_thread(instructions=DATA_EXTRACTION_SYSTEM_PROMPT, 
                                                   metadata={"step": "extraction", "company": data.company_name})
        page_urls = [str(p.source.url) for p in data.pages]
        crawl_time_str = data.crawl_summary.finished_at.strftime("%Y-%m-%d %H:%M:%S")

        with self.client.create_session(name=self._observation_name("extraction-session", f"{data.company_name}-extraction"), conversation_id=conversation_id) as session:
            partial = await self.client.generate_in_thread(
                session=session,
                input_text=build_extraction_user_prompt(data),
                output_schema=PartialExtractedInformationByIndex,
                observation_name=self._observation_name(
                    f"extraction-{attempt_number}", data.company_name + f"-extraction-{attempt_number}-" + crawl_time_str
                ),
                conversation_id=conversation_id,
                pre_status=f"Extracting {data.company_name}'s information (attempt {attempt_number})",
                post_status=f"Validating {data.company_name}'s data extraction (attempt {attempt_number})"
            )

            feedback = await validate_llm_response(client=self.client,
                                                output_criteria=EXTRACTION_VALIDATION_SYSTEM_PROMPT, 
                                                llm_output=build_extraction_validation_user_prompt(partial),
                                                observation_name=self._observation_name(
                                                    f"extraction-validation-{attempt_number}",
                                                    data.company_name + f"-extraction-validation-{attempt_number}-" + crawl_time_str
                                                ),
                                                type="extraction"
                                                )

            full_feedback = FullExtractionFeedback(feedback=feedback, 
                                                        citation_violations=citation_violations(
                last_index=len(page_urls) - 1,
                index_directory={"Claim": [p.claim_page_index for p in partial.claims_cited], 
                                "Job posting": [p.job_page_index for p in partial.job_postings],
                                "Summary": partial.summary_page_indexes,
                                "Conflicting data": [c.claim_a_page_index for c in partial.conflicting_data] + [c.claim_b_page_index for c in partial.conflicting_data]
                                }

            ))

            while (full_feedback.feedback.feedbacks or full_feedback.citation_violations) and attempt_number < OPENAI_CALL_LIMIT:
                attempt_number += 1
                partial = await self.client.generate_in_thread(
                            session=session,
                            input_text=(
                                "A reviewer found bad values in your previous output (some rules were violated)."
                                "Fix them and return the corrected full output:\n"
                                f"{full_feedback.feedback.model_dump_json()} \n"
                                f"The reviewer also found some issues with your citation indexes. The indexes should range from 0 to {len(page_urls) - 1} inclusive: \n"
                                f"{full_feedback.citation_violations}\n"
                                "Please fix the citations so you are citing the data from the correct page sources with no out-of-range indexes."
                            ),
                            output_schema=PartialExtractedInformationByIndex,
                            observation_name=self._observation_name(
                                f"extraction-{attempt_number}", data.company_name + f"-extraction-{attempt_number}-" + crawl_time_str
                            ),
                            conversation_id=conversation_id,
                            pre_status=f"Extracting {data.company_name}'s information (attempt {attempt_number})",
                            post_status=f"Validating {data.company_name}'s data extraction (attempt {attempt_number})"
                        )
                
                feedback = await validate_llm_response(client=self.client,
                                                        output_criteria=EXTRACTION_VALIDATION_SYSTEM_PROMPT, 
                                                        llm_output=build_extraction_validation_user_prompt(partial),
                                                        observation_name=self._observation_name(
                                                            f"extraction-validation-{attempt_number}", 
                                                            data.company_name + f"-extraction-validation-{attempt_number}-" + crawl_time_str
                                                        ),
                                                        type="extraction")

                full_feedback = FullExtractionFeedback(feedback=feedback, 
                                                                    citation_violations=citation_violations(
                        last_index=len(page_urls) - 1,
                        index_directory={"Claim": [p.claim_page_index for p in partial.claims_cited], 
                                        "Job posting": [p.job_page_index for p in partial.job_postings],
                                        "Summary": partial.summary_page_indexes,
                                        "Conflicting data": [c.claim_a_page_index for c in partial.conflicting_data] + [c.claim_b_page_index for c in partial.conflicting_data]
                                        }
            
                ))

            session.update(status_message=f"Extraction complete")

            # The retry budget may run out with citations still invalid; fail loudly then.
            if full_feedback.citation_violations:
                what, index = full_feedback.citation_violations[0]
                raise ValueError(f"{what} page index {index} is out of range (0 to {len(page_urls) - 1}).")

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
        attempt_number = 1
        conversation_id = await self.client.start_thread(instructions=COMPARISON_SYSTEM_PROMPT, 
                                                    metadata={"step": "comparison"})
        companies =[data.scadable, *data.competitors]
        company_urls = [list(dict.fromkeys([c.claim_page_url for c in company.claims_cited] + [j.job_page_url for j in company.job_postings]))
                        for company in companies]

        url_index = [{url: i for i, url in enumerate(urls)} for urls in company_urls]

        llm_input = ComparisonInputByIndex(
            scadable=_extraction_by_index(0, data.scadable, url_index),
            competitors=[_extraction_by_index(p, c, url_index)
                         for p, c in enumerate(data.competitors, start=1)],
        )

        with self.client.create_session(name="comparison-session", conversation_id=conversation_id) as session:
            response = await self.client.generate_in_thread(
                input_text=build_comparison_user_prompt(llm_input),
                output_schema=SWOTAnalysisByIndex,
                observation_name=self._observation_name(
                    f"comparison-{attempt_number}", f"comparison-{attempt_number}-" + data.scadable.crawl_time_string
                ),
                conversation_id=conversation_id,
                pre_status=f"Building SWOT analysis (attempt {attempt_number})",
                post_status=f"Verifying SWOT analysis (attempt {attempt_number})"
            )

            feedback = await validate_llm_response(client=self.client, 
                                                output_criteria=SWOT_VALIDATION_SYSTEM_PROMPT, 
                                                llm_output=build_swot_validation_user_prompt(response), 
                                                observation_name=self._observation_name(
                                                    f"comparison-validation-{attempt_number}",
                                                    f"SWOT-validation-{attempt_number}-" + data.scadable.crawl_time_string
                                                ),
                                                type="SWOT")

            scv = swot_citation_violations(company_urls, response.SWOT_entries)
            fsv = feature_score_violations(len(companies), response.scores)
            csv = competitor_swot_violations(company_urls, response.competitor_swots)
            fpv = feature_pair_violations(len(companies), response.scores, response.feature_pairs)

            full_feedback = FullSWOTFeedback(feedback=feedback, 
                                            swot_citation_violations=scv, 
                                            feature_score_violations=fsv, 
                                            competitor_swot_violations=csv, 
                                            feature_pair_violations=fpv)

            while (full_feedback.feedback.feedbacks or any([scv, fsv, csv, fpv])) and attempt_number < OPENAI_CALL_LIMIT:
                attempt_number += 1
                response = await self.client.generate_in_thread(
                                        input_text=(
                                            "A reviewer went through your previous response to find any violations from your output. "
                                            "Fix them and return the corrected full output:\n"
                                            f"{full_feedback.feedback.model_dump_json()}\n"
                                            "The reviewer also went ahead to specifically look at any violations from citations, feature ratings, and scorings. Please fix the ones below as well. \n" \
                                            "Note that the 'Score object' refers to your feature scores from earlier, and uses zero-based indexing. Here are the violations:\n" \
                                            f"swot citation violations: {scv}\n"
                                            f"feature score violations: {fsv}\n"
                                            f"competitor swot violations: {csv}\n"
                                            f"feature pair violations: {fpv}\n"
                                        ),
                                        output_schema=SWOTAnalysisByIndex,
                                        observation_name=self._observation_name(
                                            f"comparison-{attempt_number}", f"SWOT-{attempt_number}-" + data.scadable.crawl_time_string
                                        ),
                                        conversation_id=conversation_id,
                                        pre_status=f"Building SWOT analysis (attempt {attempt_number})",
                                        post_status=f"Verifying SWOT analysis (attempt {attempt_number})"
                                    )
                            
                feedback = await validate_llm_response(client=self.client, 
                                                            output_criteria=SWOT_VALIDATION_SYSTEM_PROMPT, 
                                                            llm_output=build_swot_validation_user_prompt(response), 
                                                            observation_name=self._observation_name(
                                                                f"comparison-validation-{attempt_number}",
                                                                f"SWOT-validation-{attempt_number}-" + data.scadable.crawl_time_string
                                                            ),
                                                            type="SWOT")

                scv = swot_citation_violations(company_urls, response.SWOT_entries)
                fsv = feature_score_violations(len(companies), response.scores)
                csv = competitor_swot_violations(company_urls, response.competitor_swots)
                fpv = feature_pair_violations(len(companies), response.scores, response.feature_pairs)
        
                full_feedback = FullSWOTFeedback(feedback=feedback, 
                                                    swot_citation_violations=scv, 
                                                    feature_score_violations=fsv, 
                                                    competitor_swot_violations=csv, 
                                                    feature_pair_violations=fpv)
            
            def cited_urls(entry: SWOTEntryByIndex) -> list[str]:
                return [
                    company_urls[c.swot_component_company_index][c.swot_component_page_index]
                    for c in entry.swot_citations
                ]

            session.update(status_message="Completed full pipeline")
            return SWOTAnalysisByURL(
                competitor_notes=response.competitor_notes,
                SWOT_entries=[
                    SWOTEntryByURL(
                        component=swot.component,
                        what=swot.what,
                        swot_component_page_urls=cited_urls(swot),
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
                conflicting_data=[data.scadable.conflicting_data] + [c.conflicting_data for c in data.competitors],
                competitor_swots=[
                    CompetitorSWOTByURL(
                        company_name=companies[block.company_index].company_name,
                        SWOT_entries=[
                            SWOTEntryByURL(
                                component=entry.component,
                                what=entry.what,
                                swot_component_page_urls=cited_urls(entry),
                            )
                            for entry in block.SWOT_entries
                        ],
                    )
                    for block in response.competitor_swots
                ],
                feature_pairs=[
                    FeaturePair(
                        company_name=companies[pair.company_index].company_name,
                        competitor_feature=pair.competitor_feature,
                        scadable_feature=pair.scadable_feature,
                    )
                    for pair in response.feature_pairs
                ],
            )
