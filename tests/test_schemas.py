"""Schema contracts for two-stage extraction and SWOT comparison."""

import pytest
from pydantic import ValidationError

from llm.schemas import (
    ClaimCited,
    ClaimCitedByIndex,
    ClaimCitedByURL,
    ConflictCitedByIndex,
    ConflictCitedByURL,
    ConflictEntry,
    ExtractedInformationByURL,
    JobFamily,
    PartialExtractedInformationByIndex,
    SWOTEntry,
)


def claim(category="feature", text="Evidence collection", url="https://example.com/"):
    return ClaimCitedByURL(category=category, claim=text, claim_page_url=url)


def extraction_fields(**overrides):
    return {
        "company_name": "Acme",
        "summary": "Acme collects evidence",
        "summary_page_urls": ["https://example.com/"],
        "job_postings": [],
        "claims_cited": [claim()],
        "conflicting_data": ConflictEntry(company_name="Acme", conflicts=[]),
        "crawl_time_string": "2026-10-19 12:00:01",
        **overrides,
    }


def partial_fields(**overrides):
    return {
        "summary": "Collects evidence", "summary_page_indexes": [0],
        "job_postings": [],
        "claims_cited": [ClaimCitedByIndex(category="feature", claim="Evidence", claim_page_index=0)],
        "conflicting_data": [],
        **overrides,
    }


def test_extraction_schema_contains_source_linked_fields_and_conflicts():
    schema = ExtractedInformationByURL.model_json_schema()
    assert set(schema["required"]) == set(extraction_fields())
    assert ExtractedInformationByURL(**extraction_fields()).company_name == "Acme"


def test_extraction_requires_all_fields():
    for missing in extraction_fields():
        fields = extraction_fields()
        del fields[missing]
        with pytest.raises(ValidationError):
            ExtractedInformationByURL(**fields)


def test_extraction_rejects_null_company_name_and_summary():
    with pytest.raises(ValidationError):
        ExtractedInformationByURL(**extraction_fields(company_name=None, summary=None))


def test_partial_extraction_requires_conflicts_and_page_indexes():
    schema = PartialExtractedInformationByIndex.model_json_schema()
    assert set(schema["required"]) == set(partial_fields())
    assert "company_name" not in schema["properties"]


def test_partial_extraction_filters_claims_by_category():
    partial = PartialExtractedInformationByIndex(**partial_fields())
    assert partial.claims_in("feature") == ["Evidence"]
    assert partial.claims_in("pricing") == []


def test_claim_schema_uses_current_literal_categories():
    schema = ClaimCited.model_json_schema()
    assert set(schema["properties"]["category"]["enum"]) == {
        "feature", "target_customers", "pricing", "integrations", "differentiator",
        "strength", "weakness", "workforce_improvement", "workforce_decline",
    }


@pytest.mark.parametrize("category", ["features", "differentiators", "opportunity", "not_real_category"])
def test_invalid_claim_category_rejected(category):
    with pytest.raises(ValidationError):
        ClaimCitedByIndex(category=category, claim="x", claim_page_index=0)


def test_conflicting_data_carries_two_independent_citations():
    indexed = ConflictCitedByIndex(
        claim_a="SOC 2 supported", claim_b="SOC 2 coming soon",
        claim_a_page_index=0, claim_b_page_index=1,
    )
    assert indexed.claim_a_page_index == 0
    assert indexed.claim_b_page_index == 1
    resolved = ConflictCitedByURL(
        claim_a=indexed.claim_a, claim_b=indexed.claim_b,
        claim_a_page_URL="https://example.com/a", claim_b_page_URL="https://example.com/b",
    )
    assert resolved.claim_a_page_URL != resolved.claim_b_page_URL


def test_job_family_rejects_invalid_family():
    with pytest.raises(ValidationError):
        JobFamily(job_family="trainee", count=10)


def test_swot_component_rejects_invalid_component():
    with pytest.raises(ValidationError):
        SWOTEntry(component="invalid", what="bad")
