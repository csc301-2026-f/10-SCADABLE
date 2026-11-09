"""Convert a company analysis into flat, source-linked SWOT rows."""

from llm.schemas import ExtractedInformationByURL

SWOT_CATEGORIES = ("strength", "weakness")


def to_swot_entries(analysis: ExtractedInformationByURL) -> list[dict[str, str]]:
    """Return one {category, content, source_url} row per strength or weakness.

    Rows keep the order the model produced them in. Claims with any other
    category (such as the summary) are not SWOT findings and are skipped.
    """
    return [
        {
            "category": claim.category,
            "content": claim.claim,
            "source_url": claim.claim_page_url,
        }
        for claim in analysis.claims_cited
        if claim.category in SWOT_CATEGORIES
    ]
