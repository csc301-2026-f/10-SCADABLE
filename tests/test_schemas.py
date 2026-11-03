"""Check the cited-claims schema helpers and constraints."""

import pytest
from pydantic import ValidationError

from llm.analyzer import build_company_analysis
from llm.schemas import (
    ClaimCited,
    ClaimCitedByIndex,
    ClaimCitedByURL,
    CompanyAnalysis,
    ExtractedInformationByURL,
    JobFamily,
    PartialCompanyAnalysisByIndex,
    PartialExtractedInformationByIndex,
    SWOTEntry
)


def claim(category, text, url="https://example.com/"):
    return ClaimCitedByURL(category=category, claim=text, claim_page_url=url)


def index_claim(category, text, index=0):
    return ClaimCitedByIndex(category=category, claim=text, claim_page_index=index)


def analysis_fields(**changes):
    """The fields CompanyAnalysis needs."""
    return {
        "company_name": "Acme", "summary": "s",
        "summary_page_urls": ["https://example.com/"],
        "claims_cited": [], "features": [],
        "crawl_time_string": "2026-10-19 12:00:01", **changes,
    }

def test_company_analysis_exposes_strengths_and_weaknesses_from_claims():
    analysis = CompanyAnalysis(
        **analysis_fields(claims_cited=[
            claim("strength", "Fast"), claim("weakness", "Pricey"), claim("strength", "Reliable"),
        ])
    )
    assert analysis.strengths == ["Fast", "Reliable"]
    assert analysis.weaknesses == ["Pricey"]


def test_partial_extraction_filters_claims_by_category():
    partial = PartialExtractedInformationByIndex(
        summary="s", summary_page_indexes=[0], job_postings=[],
        claims_cited=[index_claim("pricing", "Pro, $9"), index_claim("features", "Reports")],
    )
    assert partial.claims_in("pricing") == ["Pro, $9"]
    assert partial.claims_in("integrations") == []


def test_unknown_category_is_rejected():
    with pytest.raises(ValidationError):
        ClaimCited(category="opportunity", claim="x")


def test_json_schema_restricts_category_to_known_values():
    schema = ClaimCited.model_json_schema()
    assert set(schema["properties"]["category"]["enum"]) == {
        "features", "target_customers", "pricing",
        "integrations", "differentiators", "strength", "weakness", 
        "workforce_improvement", "workforce_decline"
    }


def test_partial_analysis_schema_only_asks_the_model_for_new_data():
    # The schema sent to OpenAI must not contain the fields we copy in ourselves.
    schema = PartialCompanyAnalysisByIndex.model_json_schema()
    assert set(schema["properties"]) == {"claims_cited"}
    assert set(schema["required"]) == {"claims_cited"}

def test_claim_citation_rejects_invalid_categrories():
    # The ClaimCitedByIndex rejects if the "category" of claim is 
    # not a strength, weakness, workforce_improvement, or workforce_decline
    with pytest.raises(ValidationError) as test_error_info:
        ClaimCitedByIndex(category="not_real_category", claim="not_real_claim", claim_page_index=0)

    errors = test_error_info.value.errors()
    assert len(errors) == 1
    assert errors[0]["type"] == "literal_error"
    assert errors[0]["loc"] == ("category",)
    assert errors[0]["input"] == "not_real_category"

def test_job_family_rejects_invalid_families():
    # Tests if JobFamily construction raises an error if an invalid job family is used for construction 
    # (not in the "Family" literals)
    with pytest.raises(ValidationError) as test_error_info:
        JobFamily(job_family="trainee", count=10)
    errors = test_error_info.value.errors()
    assert len(errors) == 1
    assert errors[0]["type"] == "literal_error"
    assert errors[0]["loc"] == ("job_family",)
    assert errors[0]["input"] == "trainee"

def test_swot_component_rejects_invalid_families():
    # Tests if SWOTEntry construction raises an error if an invalid component is used for construction 
    # (not in the "SWOTComponent" literals)
    with pytest.raises(ValidationError) as test_error_info:
        SWOTEntry(component="invalid-component", what="invalid entry")
    errors = test_error_info.value.errors()
    assert len(errors) == 1
    assert errors[0]["type"] == "literal_error"
    assert errors[0]["loc"] == ("component",)
    assert errors[0]["input"] == "invalid-component"


def test_full_analysis_schema_keeps_every_field():
    schema = CompanyAnalysis.model_json_schema()
    assert set(schema["properties"]) == {
        "company_name",
        "summary",
        "summary_page_urls",
        "claims_cited",
        "features",
        "crawl_time_string",
    }


def test_partial_extraction_schema_asks_for_page_numbers_and_not_the_company_name():
    schema = PartialExtractedInformationByIndex.model_json_schema()
    assert set(schema["required"]) == {
        "summary", "job_postings", "claims_cited", "summary_page_indexes",
    }
    assert "company_name" not in schema["properties"]


def test_build_company_analysis_resolves_urls_and_copies_the_fields():
    partial = PartialCompanyAnalysisByIndex(
        claims_cited=[index_claim("strength", "Fast", index=1)]
    )
    facts = ExtractedInformationByURL(
        company_name="Acme",
        summary="Acme sells widgets",
        summary_page_urls=["https://example.com/"],
        job_postings=[],
        claims_cited=[claim("features", "Reports")],
        crawl_time_string="2026-10-19 12:00:01",
    )
    analysis = build_company_analysis(
        partial, facts, ["https://example.com/a", "https://example.com/b"]
    )
    assert analysis.company_name == "Acme"
    assert analysis.summary == "Acme sells widgets"
    assert analysis.summary_page_urls == ["https://example.com/"]
    assert analysis.strengths == ["Fast"]
    assert analysis.claims_cited[0].claim_page_url == "https://example.com/b"
    assert analysis.features == ["Reports"]
    assert analysis.crawl_time_string == "2026-10-19 12:00:01"


@pytest.mark.parametrize(
    "missing",
    ["company_name", "summary", "summary_page_urls", "claims_cited", "features", "crawl_time_string"],
)
def test_full_analysis_requires_every_field(missing):
    fields = analysis_fields()
    del fields[missing]
    with pytest.raises(ValidationError):
        CompanyAnalysis(**fields)


def test_full_analysis_accepts_all_fields():
    assert CompanyAnalysis(**analysis_fields()).company_name == "Acme"


def test_full_analysis_rejects_null_name_and_summary():
    with pytest.raises(ValidationError):
        CompanyAnalysis(**analysis_fields(company_name=None, summary=None))
