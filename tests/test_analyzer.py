"""Enforce source citations at the boundary of structured model responses."""

import pytest

from llm.analyzer import Analyzer
from llm.inputs import load_analysis_input
from llm.schemas import (
    ClaimCitedByIndex,
    ClaimCitedByURL,
    ExtractedInformationByURL,
    JobCitedByIndex,
    JobFamily,
    PartialCompanyAnalysisByIndex,
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


def facts(url="https://example.com/"):
    """An extraction result (URL-based): the input to analyze_company."""
    return ExtractedInformationByURL(
        company_name="Example",
        summary="Evidence collection",
        summary_page_urls=[url],
        job_postings=[],
        claims_cited=[
            ClaimCitedByURL(category="features", claim="Collects evidence", claim_page_url=url)
        ],
        crawl_time_string=CRAWL_TIME,
    )


def partial(source_index: int = 0):
    """What the model returns for the analysis: claims cite a page by number."""
    return PartialCompanyAnalysisByIndex(
        claims_cited=[
            ClaimCitedByIndex(category="strength", claim="Collects evidence", claim_page_index=source_index)
        ]
    )


def partial_facts(summary_index=0, claim_index=0, job_index=None):
    """What the model returns for extraction: no company_name, pages cited by number."""
    jobs = [] if job_index is None else [
        JobCitedByIndex(
            role="Engineer", category="AI",
            job_families=[JobFamily(job_family="full-time", count=1)],
            job_page_index=job_index,
        )
    ]
    return PartialExtractedInformationByIndex(
        summary="Evidence collection",
        summary_page_indexes=[summary_index],
        job_postings=jobs,
        claims_cited=[ClaimCitedByIndex(category="features", claim="Collects evidence", claim_page_index=claim_index)],
    )

def one_page_export(tmp_path):
    return write_export(tmp_path, make_result(pages=[make_page(final_url="https://example.com/new/")]))

@pytest.mark.parametrize("bad_index", [1, 99, -1])
async def test_company_analysis_rejects_page_indexes_out_of_range(bad_index):
    # facts() cites one page, so the only valid page number is 0.
    analyzer = Analyzer(ResponseClient(partial(bad_index)))
    with pytest.raises(ValueError, match="Claim page index"):
        await analyzer.analyze_company(facts())


async def test_company_analysis_adds_name_summary_features_and_resolves_urls():
    client = ResponseClient(partial())
    result = await Analyzer(client).analyze_company(facts())

    assert result.company_name == "Example"  # from facts, not the model
    assert result.summary == "Evidence collection"
    assert result.summary_page_urls == ["https://example.com/"]
    assert result.features == ["Collects evidence"]
    assert [(c.claim, c.claim_page_url) for c in result.claims_cited] == [
        ("Collects evidence", "https://example.com/")
    ]
    assert result.crawl_time_string == CRAWL_TIME  # copied from facts
    assert client.requests[0]["output_schema"] is PartialCompanyAnalysisByIndex


@pytest.mark.parametrize("bad_index", [1, 99, -1])
@pytest.mark.parametrize(
    "field, label",
    [("summary_index", "Summary"), ("claim_index", "Claim"), ("job_index", "Job posting")],
)
async def test_extraction_rejects_page_indexes_out_of_range(tmp_path, field, label, bad_index):
    analyzer = Analyzer(ResponseClient(partial_facts(**{field: bad_index})))
    with pytest.raises(ValueError, match=f"{label} page index"):
        await analyzer.extract_information(load_analysis_input(one_page_export(tmp_path)))


async def test_extraction_resolves_page_numbers_to_urls_and_uses_the_export_name(tmp_path):
    index = one_page_export(tmp_path)
    client = ResponseClient(partial_facts(job_index=0))
    result = await Analyzer(client).extract_information(load_analysis_input(index))

    assert result.company_name == load_analysis_input(index).company_name
    assert client.requests[0]["output_schema"] is PartialExtractedInformationByIndex
    assert result.summary_page_urls == ["https://example.com/"]
    assert result.claims_cited[0].claim_page_url == "https://example.com/"
    assert result.job_postings[0].job_page_url == "https://example.com/"
    # The crawl's finish time, formatted, is kept with the extracted facts.
    assert result.crawl_time_string == CRAWL_TIME


async def test_extraction_prompt_has_markdown_and_page_numbers_but_no_urls(tmp_path):
    client = ResponseClient(partial_facts())
    await Analyzer(client).extract_information(load_analysis_input(one_page_export(tmp_path)))

    prompt = client.requests[0]["input_text"]
    assert MARKDOWN in prompt
    assert "page_index: 0" in prompt
    assert "Example home" in prompt and "features" in prompt
    assert "https://example.com" not in prompt