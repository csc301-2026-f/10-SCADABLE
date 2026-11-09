"""Validate extraction citations and conversions in the two-stage LLM pipeline."""

import pytest

from llm.analyzer import Analyzer
from llm.inputs import load_analysis_input
from llm.schemas import (
    ClaimCitedByIndex,
    ConflictCitedByIndex,
    JobCitedByIndex,
    JobFamily,
    PartialExtractedInformationByIndex,
)
from tests.helpers import CRAWL_TIME, MARKDOWN, make_page, make_result, write_export

pytestmark = pytest.mark.asyncio


class ResponseClient:
    def __init__(self, response):
        self.response = response
        self.requests = []

    async def generate_structured(self, **request):
        self.requests.append(request)
        return self.response


def partial_facts(summary_index=0, claim_index=0, job_index=None, conflict_index=None):
    jobs = [] if job_index is None else [
        JobCitedByIndex(
            role="Engineer", category="AI",
            job_families=[JobFamily(job_family="full-time", count=1)],
            job_page_index=job_index,
        )
    ]
    conflicts = [] if conflict_index is None else [
        ConflictCitedByIndex(
            claim_a="Supports SOC 2", claim_b="SOC 2 coming soon",
            claim_a_page_index=0, claim_b_page_index=conflict_index,
        )
    ]
    return PartialExtractedInformationByIndex(
        summary="Evidence collection",
        summary_page_indexes=[summary_index],
        job_postings=jobs,
        claims_cited=[ClaimCitedByIndex(category="feature", claim="Collects evidence", claim_page_index=claim_index)],
        conflicting_data=conflicts,
    )


def one_page_export(tmp_path):
    return write_export(tmp_path, make_result(pages=[make_page(final_url="https://example.com/new/")]))


@pytest.mark.parametrize("bad_index", [1, 99, -1])
@pytest.mark.parametrize(
    "field, label",
    [("summary_index", "Summary"), ("claim_index", "Claim"),
     ("job_index", "Job posting"), ("conflict_index", "Conflicting data")],
)
async def test_extraction_rejects_page_indexes_out_of_range(tmp_path, field, label, bad_index):
    analyzer = Analyzer(ResponseClient(partial_facts(**{field: bad_index})))
    with pytest.raises(ValueError, match=f"{label} page index"):
        await analyzer.extract_information(load_analysis_input(one_page_export(tmp_path)))


async def test_extraction_resolves_urls_company_name_conflicts_and_jobs(tmp_path):
    index = one_page_export(tmp_path)
    client = ResponseClient(partial_facts(job_index=0, conflict_index=0))
    result = await Analyzer(client).extract_information(load_analysis_input(index))

    assert result.company_name == "Example"
    assert result.summary_page_urls == ["https://example.com/"]
    assert result.claims_cited[0].claim_page_url == "https://example.com/"
    assert result.job_postings[0].job_page_url == "https://example.com/"
    assert result.conflicting_data.company_name == "Example"
    assert [(c.claim_a_page_URL, c.claim_b_page_URL) for c in result.conflicting_data.conflicts] == [
        ("https://example.com/", "https://example.com/")
    ]
    assert result.crawl_time_string == CRAWL_TIME
    assert client.requests[0]["output_schema"] is PartialExtractedInformationByIndex


async def test_extraction_prompt_has_markdown_and_page_numbers_but_no_urls(tmp_path):
    client = ResponseClient(partial_facts())
    await Analyzer(client).extract_information(load_analysis_input(one_page_export(tmp_path)))
    prompt = client.requests[0]["input_text"]
    assert MARKDOWN in prompt
    assert "page_index: 0" in prompt
    assert "Example home" in prompt and "features" in prompt
    assert "https://example.com" not in prompt
