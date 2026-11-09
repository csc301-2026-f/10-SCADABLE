"""Check the rows handed to the backend for each analyzed company."""

from llm.schemas import ClaimCitedByURL, ConflictEntry, ExtractedInformationByURL
from llm.swot_entries import to_swot_entries


def analysis(*claims):
    return ExtractedInformationByURL(
        company_name="Acme", summary="s", summary_page_urls=[], claims_cited=list(claims), job_postings=[],
        conflicting_data=ConflictEntry(company_name="Acme", conflicts=[]),
        crawl_time_string="2026-10-19 12:00:01",
    )


def claim(category, text, url):
    return ClaimCitedByURL(category=category, claim=text, claim_page_url=url)


def test_strengths_and_weaknesses_become_rows_with_their_own_source():
    rows = to_swot_entries(
        analysis(
            claim("strength", "Free plan", "https://acme.com/pricing"),
            claim("weakness", "No SOC 2 support", "https://acme.com/features"),
        )
    )
    assert rows == [
        {"category": "strength", "content": "Free plan",
         "source_url": "https://acme.com/pricing"},
        {"category": "weakness", "content": "No SOC 2 support",
         "source_url": "https://acme.com/features"},
    ]


def test_other_categories_are_not_swot_findings():
    rows = to_swot_entries(
        analysis(
            claim("feature", "Acme sells widgets and SOC1", "https://acme.com/features"),
            claim("pricing", "Pro, $9/mo", "https://acme.com/pricing"),
            claim("strength", "Fast", "https://acme.com/"),
        )
    )
    assert [r["content"] for r in rows] == ["Fast"]


def test_order_of_the_model_output_is_kept():
    rows = to_swot_entries(
        analysis(
            claim("weakness", "w1", "https://acme.com/a"),
            claim("strength", "s1", "https://acme.com/b"),
            claim("weakness", "w2", "https://acme.com/c"),
        )
    )
    assert [r["content"] for r in rows] == ["w1", "s1", "w2"]


def test_no_claims_gives_no_rows():
    assert to_swot_entries(analysis()) == []


def test_category_values_match_the_backend_enum():
    # backend SWOTCategory values are the singular lowercase words.
    rows = to_swot_entries(
        analysis(claim("strength", "s", "https://a.com/"), claim("weakness", "w", "https://a.com/"))
    )
    assert {r["category"] for r in rows} == {"strength", "weakness"}
