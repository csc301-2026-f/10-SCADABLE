"""Feature scoring lives in compare(): schemas, validation, name conversion and prompts."""

import pytest
from pydantic import ValidationError

from llm.analyzer import Analyzer, build_company_analysis
from llm.prompts.comparison import COMPARISON_SYSTEM_PROMPT
from llm.prompts.competitor import COMPANY_ANALYSIS_SYSTEM_PROMPT
from llm.schemas import (
    ClaimCitedByIndex,
    ClaimCitedByURL,
    CompanyAnalysis,
    ComparisonInput,
    CompetitorNote,
    ExtractedInformationByURL,
    FeatureScore,
    FeatureScoreByIndex,
    PartialCompanyAnalysisByIndex,
    SWOTAnalysisByIndex,
    SWOTEntryByIndex,
    SWOTIndex,
)

def company(name, features, pages=("https://x.example/",)):
    return CompanyAnalysis(
        company_name=name,
        summary=f"{name} summary",
        summary_page_urls=[],
        claims_cited=[
            ClaimCitedByURL(category="strength", claim=f"{name} is good", claim_page_url=url)
            for url in pages
        ],
        features=features,
        crawl_time_string="2026-10-19 12:00:01",
    )


DATA = ComparisonInput(
    scadable=company("SCADABLE", ["Fixes issues"], ("https://scadable.com/",)),
    competitors=[
        company("Vanta", ["Broad frameworks", "Auto evidence"], ("https://vanta.com/",)),
        company("Drata", ["Fast audits"], ("https://drata.com/",)),
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
            SWOT_entries=[
                SWOTEntryByIndex(
                    component="threat",
                    what="Vanta covers more frameworks",
                    swot_citations=[
                        SWOTIndex(swot_component_company_index=1, swot_component_page_index=0)
                    ],
                )
            ],
            scores=self.scores,
        )


def score(company_index, feature="Broad frameworks", value=7):
    return FeatureScoreByIndex(company_index=company_index, feature=feature, score=value)


# ---------------------------------------------------------------- schemas


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
    assert FeatureScoreByIndex(company_index=1, feature="f", score=ok).score == ok
    assert FeatureScore(company_name="Vanta", feature="f", score=ok).score == ok


def test_score_schema_has_no_numeric_limits_for_openai_strict_mode():
    flat = str(SWOTAnalysisByIndex.model_json_schema())
    assert "minimum" not in flat and "maximum" not in flat


def test_by_index_score_carries_company_index_feature_and_score():
    assert set(FeatureScoreByIndex.model_fields) == {"company_index", "feature", "score"}
    assert set(FeatureScore.model_fields) == {"company_name", "feature", "score"}


def test_company_analysis_has_features_and_no_scores():
    assert "features" in CompanyAnalysis.model_fields
    assert "scores" not in CompanyAnalysis.model_fields
    assert set(PartialCompanyAnalysisByIndex.model_fields) == {"claims_cited"}


def test_llm_output_schema_for_comparison_has_scores_by_index():
    assert "scores" in SWOTAnalysisByIndex.model_fields


# ---------------------------------------------------------------- features


def test_build_company_analysis_copies_the_features_claims():
    facts = ExtractedInformationByURL(
        company_name="Acme",
        summary="s",
        summary_page_urls=[],
        job_postings=[],
        claims_cited=[
            ClaimCitedByURL(category="features", claim="Reports", claim_page_url="https://a.com/"),
            ClaimCitedByURL(category="pricing", claim="Pro, $9", claim_page_url="https://a.com/p"),
            ClaimCitedByURL(category="features", claim="Alerts", claim_page_url="https://a.com/"),
        ],
        crawl_time_string="2026-10-19 12:00:01",
    )
    partial = PartialCompanyAnalysisByIndex(
        claims_cited=[ClaimCitedByIndex(category="strength", claim="Good", claim_page_index=0)]
    )
    analysis = build_company_analysis(partial, facts, ["https://a.com/"])
    assert analysis.features == ["Reports", "Alerts"]


# ---------------------------------------------------------------- compare()


@pytest.mark.asyncio
async def test_compare_returns_scores_with_company_names():
    client = FakeClient([score(1, "Broad frameworks", 8), score(1, "Auto evidence", 5), score(2, "Fast audits", 3)])
    result = await Analyzer(client).compare(DATA)
    assert [(s.company_name, s.feature, s.score) for s in result.scores] == [
        ("Vanta", "Broad frameworks", 8),
        ("Vanta", "Auto evidence", 5),
        ("Drata", "Fast audits", 3),
    ]
    assert all(isinstance(s, FeatureScore) for s in result.scores)


@pytest.mark.asyncio
async def test_compare_sends_each_companys_features_to_the_model():
    client = FakeClient([score(1)])
    await Analyzer(client).compare(DATA)
    prompt = client.requests[0]["input_text"]
    for feature in ("Fixes issues", "Broad frameworks", "Auto evidence", "Fast audits"):
        assert feature in prompt
    assert client.requests[0]["output_schema"] is SWOTAnalysisByIndex


@pytest.mark.asyncio
async def test_compare_with_no_scores_is_allowed():
    result = await Analyzer(FakeClient([])).compare(DATA)
    assert result.scores == []


@pytest.mark.parametrize("bad_company", [0, 3, -1])
@pytest.mark.asyncio
async def test_compare_rejects_scores_for_scadable_or_unknown_companies(bad_company):
    with pytest.raises(ValueError, match="Score company index"):
        await Analyzer(FakeClient([score(bad_company)])).compare(DATA)


# ---------------------------------------------------------------- prompts


def test_analysis_prompt_no_longer_scores_features():
    assert "FEATURE_SCORE" not in COMPANY_ANALYSIS_SYSTEM_PROMPT
    assert "SCORING_CRITERIA" not in COMPANY_ANALYSIS_SYSTEM_PROMPT
    assert "scores" not in COMPANY_ANALYSIS_SYSTEM_PROMPT


def test_comparison_prompt_explains_the_scoring():
    assert "FEATURE_SCORE_BY_INDEX" in COMPARISON_SYSTEM_PROMPT
    assert "SCORING_CRITERIA" in COMPARISON_SYSTEM_PROMPT
    assert "scores" in COMPARISON_SYSTEM_PROMPT
    assert "features (list[string])" in COMPARISON_SYSTEM_PROMPT
