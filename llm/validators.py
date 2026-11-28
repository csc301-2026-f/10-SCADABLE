from llm.client import OpenAIClient

from llm.schemas import (SWOTEntryByIndex, 
                     FeatureScoreByIndex, 
                     CompetitorSWOTByIndex, 
                     FeatureByIndex, 
                     SWOTFeedbacks, 
                     ExtractionFeedbacks)

def swot_citation_violations(
    company_urls: list[list[str]], entries: list[SWOTEntryByIndex],
    *, owner_index: int = 0,
) -> list[str]:
    violations = []
    for entry in entries:
        if not entry.swot_citations:
            violations.append("A SWOT entry object must cite at least one page.")
        for c in entry.swot_citations:
            company, page = c.swot_component_company_index, c.swot_component_page_index
            if not 0 <= company < len(company_urls):
                violations.append(f"Company number {company} does not exist.")
                continue
            if not 0 <= page < len(company_urls[company]):
                violations.append(f"Page number {page} does not exist for company {company}.")
        if entry.component in ("strength", "weakness") and not any(
            c.swot_component_company_index == owner_index for c in entry.swot_citations
        ):
            violations.append(f"A strength or weakness must cite at least one page from its subject company (subject company index must be {owner_index}).")
    return violations
            

def feature_score_violations(company_count: int, scores: list[FeatureScoreByIndex]) -> list[str]:
    """Scores may belong to any company, including SCADABLE (index 0)."""
    violations = []
    for i, score in enumerate(scores):
        if not 0 <= score.company_index < company_count:
            violations.append(
                f"Score company index {score.company_index} is out of range "
                f"(valid: 0 to {company_count - 1} inclusive)."
            )
        if not 0 <= score.score <= 10:
            violations.append(f"Score object at index {i} has an invalid score ({score.score} is not in range of 0 and 10 inclusive) for company {score.company_index}")
    return violations

def competitor_swot_violations(
    company_urls: list[list[str]], blocks: list[CompetitorSWOTByIndex],
) -> list[str]:
    violations = []
    seen: set[int] = set()
    for block in blocks:
        index = block.company_index
        if not 1 <= index < len(company_urls):
            violations.append(f"Competitor SWOT company index {index} is out of range (valid: 0 to {len(company_urls) - 1} inclusive).")
        if index in seen:
            violations.append(f"Duplicate competitor SWOT for company index {index}.")
        seen.add(index)
        violations.extend(swot_citation_violations(company_urls, block.SWOT_entries, owner_index=index))
    return violations


def feature_pair_violations(
    company_count: int, scores: list[FeatureScoreByIndex],
    pairs: list[FeatureByIndex],
) -> list[str]:
    violations = []
    scored = {(s.company_index, s.feature) for s in scores}
    seen: set[tuple[int, str]] = set()
    for pair in pairs:
        if not 1 <= pair.company_index < company_count:
            violations.append(f"Feature pair company index {pair.company_index} is out of range (valid: 0 to {company_count - 1} inclusive).")
        key = (pair.company_index, pair.competitor_feature)
        if key not in scored:
            violations.append(f"Feature pair references unscored competitor feature: {key!r}.")
        if key in seen:
            violations.append(f"Duplicate feature pair for {key!r}.")
        seen.add(key)
        if pair.scadable_feature is not None and (0, pair.scadable_feature) not in scored:
            violations.append("Feature pair references an unscored SCADABLE feature.")
    return violations


def citation_violations(*, last_index: int, index_directory: dict[str, list]):
    """
    Lists down all the citations that had been violated from the output.
    """
    violated_citations = []
    for what, indexes in index_directory.items():
        for i in indexes or []:
            if not 0 <= i <= last_index:
                violated_citations.append((what, i,))
    return violated_citations

async def validate_llm_response(*, client: OpenAIClient, output_criteria: str, llm_output: str, observation_name: str, type: str):
    """
    Checks whether the LLM output satisfies the output criteria for the expected prompt set.
    
    :return: an object containing all the feedbacks for a specific analysis.
    """
    response = await client.generate_response(instructions=output_criteria,
                                      input_text=llm_output,
                                      output_schema=SWOTFeedbacks if type == "SWOT" else ExtractionFeedbacks,
                                      observation_name=observation_name)
    return response.output_parsed