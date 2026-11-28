"""Structured analysis entry points with source-citation validation."""
import os
from dotenv import load_dotenv
from langfuse import LangfuseSpan
from pydantic import BaseModel
from copy import deepcopy

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
    Feature,
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

FIELDS_BY_KEY = {
    "summary": ("summary", "summary_page_indexes"),
    "job_postings": ("job_postings",),
    "claims_cited": ("claims_cited",),
    "conflicting_data": ("conflicting_data",),
}

KEY_BY_CITATION_LABEL = {
    "Summary": "summary",
    "Job posting": "job_postings",
    "Claim": "claims_cited",
    "Conflicting data": "conflicting_data",

}


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
    def __init__(self, client: OpenAIClient, trace_prefix: str | None = None, session_id: str | None = None) -> None:
        """Initializes an Analyzer.
        
        :param client: the OpenAIClient object (or LLM) which will conduct the full research.
        :param trace_prefix: the name of the specific chat trace within a chat session (used for tracking individual API calls).
        :param session_id: the ID of a langfuse session (to track the overall cost of all API calls in both extraction and comparison).
        """
        self.client = client
        self.trace_prefix = trace_prefix
        self.session_id = session_id

    def _observation_name(self, step: str, default: str) -> str:
        return f"{self.trace_prefix}:{step}" if self.trace_prefix else default

    async def extract_information(self, data: AnalysisInput) -> ExtractedInformationByURL:
        """
        Extracts information from what is given inside <data>. 

        :param data (AnalysisInput): the pages scraped from the company's website (company is given inside <data>).
        :return: all the data extracted, with URL citations for any factual information or information that was inferred from <data>.
        """
        finalized = None
        attempt_number = 1

        # Creates an OpenAI chat session to allow validation and output adjustments.
        conversation_id = await self.client.start_thread(instructions=DATA_EXTRACTION_SYSTEM_PROMPT, 
                                                   metadata={"step": "extraction", "company": data.company_name})
        page_urls = [str(p.source.url) for p in data.pages]
        crawl_time_str = data.crawl_summary.finished_at.strftime("%Y-%m-%d %H:%M:%S")

        # Creates a Langfuse session (this records the cost of every API call associated with this part of the pipeline)
        with self.client.create_session(name=self._observation_name("extraction-session", f"{data.company_name}-extraction"), 
                                        session_id=self.session_id) as session:
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

            # Makes a full copy of the initial output (this is what gets adjusted incrementally)
            finalized = partial.model_copy()

            # Gets feedback from another LLM call to see if the data extracted was even valid or not.
            feedback = await validate_llm_response(client=self.client,
                                                output_criteria=EXTRACTION_VALIDATION_SYSTEM_PROMPT, 
                                                llm_output=build_extraction_validation_user_prompt(scraped_pages=data, llm_output=partial),
                                                observation_name=self._observation_name(
                                                    f"extraction-validation-{attempt_number}",
                                                    data.company_name + f"-extraction-validation-{attempt_number}-" + crawl_time_str
                                                ),
                                                type="extraction"
                                                )

            # Packages the full feedback, including feedback about any citations that were invalid (out-of-bounds indices)
            full_feedback = FullExtractionFeedback(feedback=feedback, 
                                                        citation_violations=citation_violations(
                last_index=len(page_urls) - 1,
                index_directory={"Claim": [p.claim_page_index for p in finalized.claims_cited], 
                                "Job posting": [p.job_page_index for p in finalized.job_postings],
                                "Summary": finalized.summary_page_indexes,
                                "Conflicting data": [c.claim_a_page_index for c in finalized.conflicting_data] + [c.claim_b_page_index for c in finalized.conflicting_data]
                                }

            ))

            # Attempts to fix the LLM responses within the same OpenAI chat until there are no more pieces of feedback, or until the attempt limit has been reached
            while (full_feedback.feedback.feedbacks or full_feedback.citation_violations) and attempt_number < OPENAI_CALL_LIMIT:
                attempt_number += 1
                # Prompts the LLM to adjust its outputs based on the feedback
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

                # full_feedback is still the feedback that caused this retry
                what_to_change = {f.what for f in full_feedback.feedback.feedbacks}
                what_to_change |= {KEY_BY_CITATION_LABEL[label] for label, _ in full_feedback.citation_violations}

                updates = {
                    field: getattr(partial, field)
                    for key in what_to_change
                    for field in FIELDS_BY_KEY[key]
                }

                # Updates finalized so that only the components that had to be changed, are now in the finalized output (everything else stays the same).
                finalized = finalized.model_copy(update=updates)

                # Gets feedback from the LLM on the new adjusted output
                feedback = await validate_llm_response(client=self.client,
                                                        output_criteria=EXTRACTION_VALIDATION_SYSTEM_PROMPT, 
                                                        llm_output=build_extraction_validation_user_prompt(scraped_pages=data, llm_output=finalized),
                                                        observation_name=self._observation_name(
                                                            f"extraction-validation-{attempt_number}", 
                                                            data.company_name + f"-extraction-validation-{attempt_number}-" + crawl_time_str
                                                        ),
                                                        type="extraction")

                # Packages all the feedback including out-of-bounds citations
                full_feedback = FullExtractionFeedback(feedback=feedback, 
                                                                    citation_violations=citation_violations(
                        last_index=len(page_urls) - 1,
                        # Check what will be returned (finalized), not fields of the reply that were not taken
                        index_directory={"Claim": [p.claim_page_index for p in finalized.claims_cited], 
                                        "Job posting": [p.job_page_index for p in finalized.job_postings],
                                        "Summary": finalized.summary_page_indexes,
                                        "Conflicting data": [c.claim_a_page_index for c in finalized.conflicting_data] + [c.claim_b_page_index for c in finalized.conflicting_data]
                                        }
            
                ))

            if full_feedback.citation_violations:
                what, index = full_feedback.citation_violations[0]
                raise ValueError(f"{what} page index {index} is out of range (0 to {len(page_urls) - 1}).")

            # Updates the session status to indicate that the extraction step has been complete
            session.update(status_message=f"Extraction complete")

            # Packages all the information that was extracted, into a URL-based model. So every page index is translated back into its URL.
            result = ExtractedInformationByURL(company_name=data.company_name, 
                                        summary=finalized.summary,
                                        summary_page_urls=[page_urls[p] for p in finalized.summary_page_indexes], 
                                        job_postings=[JobCitedByURL(role=job.role, category=job.category, job_families=job.job_families, job_page_url=page_urls[job.job_page_index]) for job in finalized.job_postings],
                                        claims_cited=[ClaimCitedByURL(category=c.category, claim=c.claim, claim_page_url=page_urls[c.claim_page_index]) for c in finalized.claims_cited],
                                        conflicting_data=ConflictEntry(company_name=data.company_name, conflicts=[ConflictCitedByURL(claim_a=c.claim_a, claim_a_page_URL=page_urls[c.claim_a_page_index], claim_b=c.claim_b, claim_b_page_URL=page_urls[c.claim_b_page_index]) for c in finalized.conflicting_data]),
                                        crawl_time_string=crawl_time_str
                                        )
            return result

    async def compare(self, data: ComparisonInput) -> SWOTAnalysisByURL:
        """
        Generates a full SWOT analysis between SCADABLE and competitors.

        :param data: all the information extracted from SCADABLE and its competitors.
        :return: a thorough SWOT analysis with every fact having a URL citation (except for company features).
        """
        finalized = None
        attempt_number = 1
        # Starts a new OpenAI chat session to have multi-turn conversations and fix outputs within the same context window
        conversation_id = await self.client.start_thread(instructions=COMPARISON_SYSTEM_PROMPT, 
                                                    metadata={"step": "comparison"})
        companies =[data.scadable, *data.competitors]
        company_urls = [list(dict.fromkeys([c.claim_page_url for c in company.claims_cited] + [j.job_page_url for j in company.job_postings]))
                        for company in companies]

        url_index = [{url: i for i, url in enumerate(urls)} for urls in company_urls]

        # Converts the URL-based input back into index-based input
        llm_input = ComparisonInputByIndex(
            scadable=_extraction_by_index(0, data.scadable, url_index),
            competitors=[_extraction_by_index(p, c, url_index)
                         for p, c in enumerate(data.competitors, start=1)],
        )

        # Creates a Langfuse span session to track all the LLM API calls related to this specific step of the run
        with self.client.create_session(name="comparison-session", session_id=self.session_id) as session:
            # Generates the initial SWOT analysis
            response = await self.client.generate_in_thread(
                session=session,
                input_text=build_comparison_user_prompt(llm_input),
                output_schema=SWOTAnalysisByIndex,
                observation_name=self._observation_name(
                    f"comparison-{attempt_number}", f"comparison-{attempt_number}-" + data.scadable.crawl_time_string
                ),
                conversation_id=conversation_id,
                pre_status=f"Building SWOT analysis (attempt {attempt_number})",
                post_status=f"Verifying SWOT analysis (attempt {attempt_number})"
            )

            # Gets the feedback for the initial SWOT analysis
            feedback = await validate_llm_response(client=self.client, 
                                                output_criteria=SWOT_VALIDATION_SYSTEM_PROMPT, 
                                                llm_output=build_swot_validation_user_prompt(comparison_input=llm_input, llm_response=response), 
                                                observation_name=self._observation_name(
                                                    f"comparison-validation-{attempt_number}",
                                                    f"SWOT-validation-{attempt_number}-" + data.scadable.crawl_time_string
                                                ),
                                                type="SWOT")

            # Copies the initial SWOT analysis (this is what gets incrementally adjusted)            
            finalized = response.model_copy()

            # Gets all citation-range violations (out-of-bounds SWOT citation indexes)
            scv = swot_citation_violations(company_urls, finalized.SWOT_entries)
            fsv = feature_score_violations(len(companies), finalized.scores)
            csv = competitor_swot_violations(company_urls, finalized.competitor_swots)
            fpv = feature_pair_violations(len(companies), finalized.scores, finalized.features)

            # Packages all the citation and output feedback
            full_feedback = FullSWOTFeedback(feedback=feedback, 
                                            swot_citation_violations=scv, 
                                            feature_score_violations=fsv, 
                                            competitor_swot_violations=csv, 
                                            feature_pair_violations=fpv)

            # While there is feedback on things to fix and the number of attempts has not exceeded the limit, keep fine-tuning the output
            while (full_feedback.feedback.feedbacks or any([scv, fsv, csv, fpv])) and attempt_number < OPENAI_CALL_LIMIT:
                attempt_number += 1
                # Generates the next SWOT analysis based on previous feedback
                response = await self.client.generate_in_thread(
                                        session=session,
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

                # these still describe the output that caused this retry
                what_to_change = {f.what for f in full_feedback.feedback.feedbacks}
                if scv:
                    what_to_change.add("SWOT_entries")
                if fsv:
                    what_to_change.add("scores")
                if csv:
                    what_to_change.add("competitor_swots")
                if fpv:
                    what_to_change.add("features")

                # pairs refer to score names, so replace them together
                if what_to_change & {"scores", "features"}:
                    what_to_change |= {"scores", "features"}

                # Updates finalized to replace only the fields that needed fixing
                finalized = finalized.model_copy(
                    update={field: getattr(response, field) for field in what_to_change}
                )

                # Generates feedback for the previous iteration
                feedback = await validate_llm_response(client=self.client, 
                                                            output_criteria=SWOT_VALIDATION_SYSTEM_PROMPT, 
                                                            llm_output=build_swot_validation_user_prompt(comparison_input=llm_input, llm_response=finalized), 
                                                            observation_name=self._observation_name(
                                                                f"comparison-validation-{attempt_number}",
                                                                f"SWOT-validation-{attempt_number}-" + data.scadable.crawl_time_string
                                                            ),
                                                            type="SWOT")

                # Gets all the citation violations from the previous fix
                scv = swot_citation_violations(company_urls, finalized.SWOT_entries)
                fsv = feature_score_violations(len(companies), finalized.scores)
                csv = competitor_swot_violations(company_urls, finalized.competitor_swots)
                fpv = feature_pair_violations(len(companies), finalized.scores, finalized.features)
                
                # Stores all the feedback including citation violations
                full_feedback = FullSWOTFeedback(feedback=feedback, 
                                                    swot_citation_violations=scv, 
                                                    feature_score_violations=fsv, 
                                                    competitor_swot_violations=csv, 
                                                    feature_pair_violations=fpv)

            # The retry budget may run out with violations remaining
            remaining = [*scv, *fsv, *csv, *fpv]
            if remaining:
                raise ValueError(f"SWOT output still has violations after retries: {remaining}")
            
            # Gets back the URLs of the citation indexes
            def cited_urls(entry: SWOTEntryByIndex) -> list[str]:
                return [
                    company_urls[c.swot_component_company_index][c.swot_component_page_index]
                    for c in entry.swot_citations
                ]

            # updates session status to indicate that the full SWOT analysis is complete.
            session.update(status_message="Completed full pipeline")

            # Returns the index-based SWOT analysis into the URL-form SWOT analysis
            return SWOTAnalysisByURL(
                competitor_notes=finalized.competitor_notes,
                SWOT_entries=[
                    SWOTEntryByURL(
                        component=swot.component,
                        what=swot.what,
                        swot_component_page_urls=cited_urls(swot),
                    )
                    for swot in finalized.SWOT_entries
                ],
                scores=[
                    FeatureScore(
                        company_name=companies[score.company_index].company_name,
                        feature=score.feature,
                        score=score.score,
                    )
                    for score in finalized.scores
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
                    for block in finalized.competitor_swots
                ],
                features=[
                    Feature(
                        company_name=companies[pair.company_index].company_name,
                        competitor_feature=pair.competitor_feature,
                        scadable_feature=pair.scadable_feature,
                    )
                    for pair in finalized.features
                ],
            )
