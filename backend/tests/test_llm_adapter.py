"""Tests for the OpenAI adapter against the real llm package, with the OpenAI call faked.

The mock analyzer never builds llm schemas, so only these tests catch schema
changes in llm/ that would break the adapter. They need the llm package's
dependencies and are skipped when those are not installed.
"""

import asyncio

import pytest

pytest.importorskip("openai")
pytest.importorskip("pydantic_settings")

from llm.schemas import (  # noqa: E402
    ClaimCitedByIndex,
    FeatureScoreByIndex,
    JobCitedByIndex,
    JobFamily,
    PartialCompanyAnalysisByIndex,
    PartialExtractedInformationByIndex,
    SWOTAnalysisByIndex,
    SWOTEntryByIndex,
    SWOTIndex,
)

from app.core.baseline import get_scadable_baseline  # noqa: E402
from app.services import llm as llm_service  # noqa: E402
from app.services.llm import OpenAIAdapter  # noqa: E402


class FakeClient:
    """Returns a canned response per output schema instead of calling OpenAI."""

    def __init__(self, responses):
        self.responses = responses
        self.closed = False
        self.observation_names = []

    async def generate_structured(
        self, *, instructions, input_text, output_schema, observation_name="openai-api-call"
    ):
        self.observation_names.append(observation_name)
        return self.responses[output_schema.__name__]

    async def close(self):
        self.closed = True


@pytest.fixture
def fake_client(monkeypatch):
    def install(responses):
        client = FakeClient(responses)
        monkeypatch.setattr(llm_service, "_build_openai_client", lambda: client)
        return client

    return install


def test_analyze_uses_real_llm_schemas(fake_client):
    client = fake_client({
        "PartialExtractedInformationByIndex": PartialExtractedInformationByIndex(
            summary="Vanta automates compliance.",
            summary_page_indexes=[0],
            job_postings=[JobCitedByIndex(
                role="Software Engineer", category="Engineering", job_page_index=0,
                job_families=[JobFamily(job_family="full-time", count=2),
                              JobFamily(job_family="co-op/internship", count=1)],
            )],
            claims_cited=[
                ClaimCitedByIndex(category="features", claim="Evidence collection", claim_page_index=0),
            ],
        ),
        "PartialCompanyAnalysisByIndex": PartialCompanyAnalysisByIndex(
            claims_cited=[
                ClaimCitedByIndex(category="strength", claim="Many integrations", claim_page_index=0),
            ],
        ),
    })
    scraped = {
        "competitor_name": "Vanta",
        "base_url": "https://vanta.com",
        "pages": [{"url": "https://vanta.com/features", "title": "Features", "text": "Evidence collection"}],
    }

    result = asyncio.run(
        OpenAIAdapter().analyze(scraped, get_scadable_baseline(), trace_prefix="run-42:vanta")
    )

    # A schema mismatch makes analyze() fall back to the mock, which has no "mode"
    assert result.get("mode") == "real"
    assert result["features"] == ["Evidence collection"]
    assert result["open_roles"] == 3
    assert result["job_postings"] == [{
        "role": "Software Engineer", "category": "Engineering",
        "job_page_url": "https://vanta.com/features",
        "job_families": [
            {"job_family": "full-time", "count": 2},
            {"job_family": "co-op/internship", "count": 1},
        ],
    }]
    assert {
        "category": "strength",
        "content": "Many integrations",
        "source_url": "https://vanta.com/features",
    } in result["swot_entries"]
    # Calls are named for cost lookups, and the run records which ones it made
    assert client.observation_names == ["run-42:vanta:extraction", "run-42:vanta:analysis"]
    assert result["llm_calls"] == ["run-42:vanta:extraction", "run-42:vanta:analysis"]
    assert client.closed


def test_analyze_fallback_keeps_paid_calls(fake_client):
    # Extraction succeeds (and is paid for), then analysis has no canned response and fails
    fake_client({
        "PartialExtractedInformationByIndex": PartialExtractedInformationByIndex(
            summary="Vanta automates compliance.",
            summary_page_indexes=[0],
            job_postings=[],
            claims_cited=[],
        ),
    })
    scraped = {
        "competitor_name": "Vanta",
        "base_url": "https://vanta.com",
        "pages": [{"url": "https://vanta.com/features", "title": "Features", "text": "Evidence"}],
    }

    result = asyncio.run(
        OpenAIAdapter().analyze(scraped, get_scadable_baseline(), trace_prefix="run-42:vanta")
    )

    assert result.get("mode") is None  # fell back to the mock
    assert result["llm_calls"] == ["run-42:vanta:extraction", "run-42:vanta:analysis"]


def test_compare_uses_real_llm_schemas(fake_client):
    client = fake_client({
        "SWOTAnalysisByIndex": SWOTAnalysisByIndex(
            competitor_notes=[],
            SWOT_entries=[
                # Strength citing SCADABLE (company 0) and Vanta (company 1)
                SWOTEntryByIndex(
                    component="strength",
                    what="Native EU CRA tracking Vanta lacks.",
                    swot_citations=[
                        SWOTIndex(swot_component_company_index=0, swot_component_page_index=0),
                        SWOTIndex(swot_component_company_index=1, swot_component_page_index=0),
                    ],
                ),
                # Weakness citing only SCADABLE
                SWOTEntryByIndex(
                    component="weakness",
                    what="Smaller partner network.",
                    swot_citations=[
                        SWOTIndex(swot_component_company_index=0, swot_component_page_index=0),
                    ],
                ),
            ],
            scores=[FeatureScoreByIndex(company_index=1, feature="Evidence collection", score=8)],
        ),
    })
    competitors = [{
        "company_name": "Vanta",
        "website_url": "https://vanta.com",
        "summary": "Vanta automates compliance.",
        "features": ["Evidence collection"],
        "claims": [
            {"category": "strength", "content": "Many integrations", "source_url": "https://vanta.com/features"},
            {"category": "weakness", "content": "High price", "source_url": None},
        ],
    }]

    result = asyncio.run(
        OpenAIAdapter().compare(get_scadable_baseline(), competitors, trace_prefix="run-42")
    )

    assert client.observation_names == ["run-42:comparison"]
    assert result["swot_entries"] == [
        # The competitor page is preferred over SCADABLE's homepage
        {"category": "strength", "content": "Native EU CRA tracking Vanta lacks.", "source_url": "https://vanta.com/features"},
        {"category": "weakness", "content": "Smaller partner network.", "source_url": "https://scadable.com"},
    ]
    assert result["scores"] == [
        {"company_name": "Vanta", "feature": "Evidence collection", "score": 8}
    ]
    assert client.closed
