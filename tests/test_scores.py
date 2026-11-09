"""Two-stage feature-scoring schema, conversion, validation and prompt tests."""

import pytest
from pydantic import ValidationError

from llm.analyzer import Analyzer
from llm.prompts.comparison import COMPARISON_SYSTEM_PROMPT
from llm.schemas import (
    ClaimCitedByURL, ComparisonInput, CompetitorNote, ConflictEntry,
    ExtractedInformationByURL, FeatureScore, FeatureScoreByIndex,
    SWOTAnalysisByIndex, SWOTEntryByIndex, SWOTIndex,
)


def company(name, features, page):
    return ExtractedInformationByURL(
        company_name=name,
        summary=f"{name} summary",
        summary_page_urls=[page],
        job_postings=[],
        claims_cited=[ClaimCitedByURL(category="feature", claim=f, claim_page_url=page) for f in features],
        conflicting_data=ConflictEntry(company_name=name, conflicts=[]),
        crawl_time_string="2026-10-19 12:00:01",
    )


DATA = ComparisonInput(
    scadable=company("SCADABLE", ["Fixes issues"], "https://scadable.com/"),
    competitors=[
        company("Vanta", ["Broad frameworks", "Auto evidence"], "https://vanta.com/"),
        company("Drata", ["Fast audits"], "https://drata.com/"),
    ],
)


class FakeClient:
    def __init__(self, scores):
        self.scores = scores
        self.requests = []

    async def generate_structured(self, **request):
        self.requests.append(request)
        return SWOTAnalysisByIndex(
            competitor_notes=[
                CompetitorNote(company_name="Vanta", why_it_matters="x"),
                CompetitorNote(company_name="Drata", why_it_matters="y"),
            ],
            SWOT_entries=[SWOTEntryByIndex(
                component="threat", what="Vanta covers more frameworks",
                swot_citations=[SWOTIndex(swot_component_company_index=1, swot_component_page_index=0)],
            )],
            scores=self.scores,
        )


def score(company_index, feature="Broad frameworks", value=7):
    return FeatureScoreByIndex(company_index=company_index, feature=feature, score=value)


@pytest.mark.parametrize("cls_args", [
    (FeatureScoreByIndex, {"company_index": 1}),
    (FeatureScore, {"company_name": "Vanta"}),
])
@pytest.mark.parametrize("bad", [-1, 11])
def test_score_outside_zero_to_ten_is_rejected(cls_args, bad):
    cls, extra = cls_args
    with pytest.raises(ValidationError):
        cls(feature="Evidence collection", score=bad, **extra)


@pytest.mark.parametrize("ok", [0, 5, 10])
def test_scores_from_zero_to_ten_are_accepted(ok):
    assert score(1, value=ok).score == ok
    assert FeatureScore(company_name="Vanta", feature="f", score=ok).score == ok


def test_score_schema_avoids_unsupported_numeric_limits_for_strict_mode():
    flat = str(SWOTAnalysisByIndex.model_json_schema())
    assert "minimum" not in flat and "maximum" not in flat


def test_feature_scores_have_name_or_company_index():
    assert set(FeatureScoreByIndex.model_fields) == {"company_index", "feature", "score"}
    assert set(FeatureScore.model_fields) == {"company_name", "feature", "score"}
    assert "scores" in SWOTAnalysisByIndex.model_fields


@pytest.mark.asyncio
async def test_compare_maps_competitor_scores_back_to_names():
    client = FakeClient([score(1, "Broad frameworks", 8), score(2, "Fast audits", 3)])
    result = await Analyzer(client).compare(DATA)
    assert [(s.company_name, s.feature, s.score) for s in result.scores] == [
        ("Vanta", "Broad frameworks", 8), ("Drata", "Fast audits", 3)
    ]
    assert result.conflicting_data == [c.conflicting_data for c in [DATA.scadable, *DATA.competitors]]


@pytest.mark.asyncio
async def test_compare_accepts_scadable_scores_at_company_index_zero():
    client = FakeClient([score(0, "Fixes issues", 9), score(1, "Broad frameworks", 7)])
    result = await Analyzer(client).compare(DATA)

    assert [(s.company_name, s.feature, s.score) for s in result.scores] == [
        ("SCADABLE", "Fixes issues", 9),
        ("Vanta", "Broad frameworks", 7),
    ]
    assert "SCADABLE summary" in client.requests[0]["input_text"]
    assert "Score every" in client.requests[0]["instructions"]
    assert "SCADABLE" in client.requests[0]["instructions"]


@pytest.mark.asyncio
async def test_compare_sends_all_extracted_features_to_prompt():
    client = FakeClient([score(1)])
    await Analyzer(client).compare(DATA)
    prompt = client.requests[0]["input_text"]
    for feature in ("Fixes issues", "Broad frameworks", "Auto evidence", "Fast audits"):
        assert feature in prompt
    assert client.requests[0]["output_schema"] is SWOTAnalysisByIndex


@pytest.mark.asyncio
async def test_compare_with_no_scores_is_allowed():
    assert (await Analyzer(FakeClient([])).compare(DATA)).scores == []


@pytest.mark.parametrize("bad_company", [3, -1])
@pytest.mark.asyncio
async def test_compare_rejects_unknown_score_company_indices(bad_company):
    with pytest.raises(ValueError, match="Score company index"):
        await Analyzer(FakeClient([score(bad_company)])).compare(DATA)


def test_comparison_prompt_contains_scoring_rubric():
    assert "FEATURE_SCORE_BY_INDEX" in COMPARISON_SYSTEM_PROMPT
    assert "SCORING_CRITERIA" in COMPARISON_SYSTEM_PROMPT
    assert "score" in COMPARISON_SYSTEM_PROMPT


@pytest.mark.asyncio
async def test_comparison_returns_per_competitor_swot_and_pairs():
    from llm.schemas import CompetitorSWOTByIndex, FeaturePairByIndex

    class RichClient:
        async def generate_structured(self, **request):
            return SWOTAnalysisByIndex(
                competitor_notes=[], SWOT_entries=[],
                scores=[score(0, "Fixes issues", 8), score(1, "Broad frameworks", 7)],
                competitor_swots=[CompetitorSWOTByIndex(
                    company_index=1,
                    SWOT_entries=[SWOTEntryByIndex(
                        component="strength", what="Vanta supports broad frameworks.",
                        swot_citations=[SWOTIndex(swot_component_company_index=1, swot_component_page_index=0)],
                    )],
                )],
                feature_pairs=[FeaturePairByIndex(
                    company_index=1, competitor_feature="Broad frameworks",
                    scadable_feature="Fixes issues",
                )],
            )

    result = await Analyzer(RichClient()).compare(DATA)
    assert result.competitor_swots[0].company_name == "Vanta"
    assert result.competitor_swots[0].SWOT_entries[0].swot_component_page_urls == ["https://vanta.com/"]
    assert result.feature_pairs[0].scadable_feature == "Fixes issues"


@pytest.mark.asyncio
async def test_competitor_swot_requires_own_citation():
    from llm.schemas import CompetitorSWOTByIndex

    class BadClient:
        async def generate_structured(self, **request):
            return SWOTAnalysisByIndex(
                competitor_notes=[], SWOT_entries=[], scores=[],
                competitor_swots=[CompetitorSWOTByIndex(
                    company_index=1,
                    SWOT_entries=[SWOTEntryByIndex(
                        component="strength", what="Vanta strength without Vanta citation",
                        swot_citations=[SWOTIndex(swot_component_company_index=0, swot_component_page_index=0)],
                    )],
                )],
            )

    with pytest.raises(ValueError, match="subject company"):
        await Analyzer(BadClient()).compare(DATA)


@pytest.mark.parametrize("bad_pair", [
    {"company_index": 0, "competitor_feature": "Fixes issues", "scadable_feature": None},
    {"company_index": 1, "competitor_feature": "Missing feature", "scadable_feature": "Fixes issues"},
    {"company_index": 1, "competitor_feature": "Broad frameworks", "scadable_feature": "Missing baseline score"},
])
@pytest.mark.asyncio
async def test_rejects_invalid_explicit_feature_pairs(bad_pair):
    from llm.schemas import FeaturePairByIndex

    class BadClient:
        async def generate_structured(self, **request):
            return SWOTAnalysisByIndex(
                competitor_notes=[], SWOT_entries=[],
                scores=[score(0, "Fixes issues", 8), score(1, "Broad frameworks", 7)],
                feature_pairs=[FeaturePairByIndex(**bad_pair)],
            )

    with pytest.raises(ValueError, match="Feature pair"):
        await Analyzer(BadClient()).compare(DATA)

@pytest.mark.asyncio
async def test_competitor_swot_rejects_invalid_company_index():
    from llm.schemas import CompetitorSWOTByIndex

    class BadClient:
        async def generate_structured(self, **request):
            return SWOTAnalysisByIndex(
                competitor_notes=[], SWOT_entries=[], scores=[],
                competitor_swots=[CompetitorSWOTByIndex(company_index=0, SWOT_entries=[])],
            )

    with pytest.raises(ValueError, match="Competitor SWOT company index"):
        await Analyzer(BadClient()).compare(DATA)
