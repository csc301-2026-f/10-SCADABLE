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
    PartialExtractedInformationByIndex,
    ConflictCitedByIndex,
    SWOTAnalysisByIndex,
    SWOTEntryByIndex,
    SWOTIndex,
    CompetitorSWOTByIndex,
    FeaturePairByIndex,
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


@pytest.mark.parametrize("duplicate", [False, True])
def test_analyze_uses_real_llm_schemas(fake_client, monkeypatch, duplicate):
    from llm.analyzer import Analyzer

    observed = []
    original = Analyzer.extract_information

    async def capture(self, data):
        observed.append(data)
        return await original(self, data)

    monkeypatch.setattr(Analyzer, "extract_information", capture)
    client = fake_client({
        "PartialExtractedInformationByIndex": PartialExtractedInformationByIndex(
            summary="Vanta automates compliance.",
            summary_page_indexes=[0],
            conflicting_data=[],
            job_postings=[JobCitedByIndex(
                role="Software Engineer", category="Engineering", job_page_index=0,
                job_families=[JobFamily(job_family="full-time", count=2),
                              JobFamily(job_family="co-op/internship", count=1)],
            )],
            claims_cited=[
                ClaimCitedByIndex(category="feature", claim="Evidence collection", claim_page_index=0),
            ],
        ),
    })
    scraped = {
        "competitor_name": "Vanta",
        "base_url": "https://vanta.com",
        "pages": [{"url": "https://vanta.com/features", "title": "Features", "text": "Evidence collection"}],
    }

    if duplicate:
        scraped["pages"].append({"url": "https://vanta.com/copy", "text": "Evidence collection"})
    result = asyncio.run(
        OpenAIAdapter().analyze(scraped, get_scadable_baseline(), trace_prefix="run-42:vanta")
    )
    assert len(observed[0].pages) == 1
    assert len(observed[0].crawl_summary.pages) == (2 if duplicate else 1)
    assert len(scraped["pages"]) == (2 if duplicate else 1)

    # A schema mismatch makes analyze() fall back to the mock, which has no "mode"
    assert result.get("mode") == "real"
    assert result["features"] == ["Evidence collection"]
    assert result["extracted_information"]["claims_cited"][0]["claim_page_url"] == "https://vanta.com/features"
    assert result["conflicting_data"] == {"company_name": "Vanta", "conflicts": []}
    # SWOT is produced by the later comparison; extraction must not invent it.
    assert result["swot_entries"] == []
    assert result["open_roles"] == 3
    assert result["job_postings"] == [{
        "role": "Software Engineer", "category": "Engineering",
        "job_page_url": "https://vanta.com/features",
        "job_families": [
            {"job_family": "full-time", "count": 2},
            {"job_family": "co-op/internship", "count": 1},
        ],
    }]
    # Calls are named for cost lookups, and the run records which ones it made
    assert client.observation_names == ["run-42:vanta:extraction"]
    assert result["llm_calls"] == ["run-42:vanta:extraction"]
    assert client.closed


def test_analyze_invalid_citation_fails_instead_of_fabricating_results(fake_client, monkeypatch):
    monkeypatch.delenv("ALLOW_MOCK_LLM", raising=False)
    client = fake_client({
        "PartialExtractedInformationByIndex": PartialExtractedInformationByIndex(
            summary="Vanta automates compliance.",
            summary_page_indexes=[0],
            job_postings=[],
            claims_cited=[],
            conflicting_data=[ConflictCitedByIndex(
                claim_a="SOC 2 supported", claim_b="SOC 2 coming soon",
                claim_a_page_index=0, claim_b_page_index=99,
            )],
        ),
    })
    scraped = {
        "competitor_name": "Vanta",
        "base_url": "https://vanta.com",
        "pages": [{"url": "https://vanta.com/features", "title": "Features", "text": "Evidence"}],
    }
    with pytest.raises(ValueError, match="Conflicting data page index"):
        asyncio.run(OpenAIAdapter().analyze(
            scraped, get_scadable_baseline(), trace_prefix="run-42:vanta"
        ))
    assert client.observation_names == ["run-42:vanta:extraction"]
    assert client.closed

    # Opt-in mocks remain available for offline demonstrations only.
    monkeypatch.setenv("ALLOW_MOCK_LLM", "true")
    result = asyncio.run(OpenAIAdapter().analyze(
        scraped, get_scadable_baseline(), trace_prefix="run-42:vanta"
    ))
    assert result["mode"] == "mock"


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
    assert result["conflicting_data"] == [
        {"company_name": "SCADABLE", "conflicts": []},
        {"company_name": "Vanta", "conflicts": []},
    ]
    assert client.closed


def test_compare_reuses_full_extraction_including_conflicts(fake_client):
    from llm.schemas import ExtractedInformationByURL, ClaimCitedByURL, ConflictEntry, ConflictCitedByURL

    client = fake_client({
        "SWOTAnalysisByIndex": SWOTAnalysisByIndex(
            competitor_notes=[], SWOT_entries=[],
            scores=[
                FeatureScoreByIndex(company_index=0, feature="Automated evidence collection", score=6),
                FeatureScoreByIndex(company_index=1, feature="Automated evidence collection", score=9),
            ],
        ),
    })
    facts = ExtractedInformationByURL(
        company_name="Vanta", summary="Vanta offers compliance automation",
        summary_page_urls=["https://vanta.com/features"],
        job_postings=[],
        claims_cited=[
            ClaimCitedByURL(category="feature", claim="Automated evidence collection", claim_page_url="https://vanta.com/features"),
            ClaimCitedByURL(category="pricing", claim="Premium pricing", claim_page_url="https://vanta.com/pricing"),
        ],
        conflicting_data=ConflictEntry(company_name="Vanta", conflicts=[ConflictCitedByURL(
            claim_a="Price A", claim_a_page_URL="https://vanta.com/features",
            claim_b="Price B", claim_b_page_URL="https://vanta.com/pricing",
        )]),
        crawl_time_string="2026-11-06 17:00:00",
    )
    result = asyncio.run(OpenAIAdapter().compare(
        get_scadable_baseline(), [{
            "company_name": "Vanta", "website_url": "https://vanta.com",
            "extracted_information": facts.model_dump(mode="json"),
            "summary": "ignored legacy summary", "features": [], "claims": [],
        }], trace_prefix="run-45",
    ))
    assert client.observation_names == ["run-45:comparison"]
    assert result["mode"] == "real"
    assert result["scores"] == [
        {"company_name": "SCADABLE", "feature": "Automated evidence collection", "score": 6},
        {"company_name": "Vanta", "feature": "Automated evidence collection", "score": 9},
    ]
    assert result["conflicting_data"][1]["conflicts"][0]["claim_b_page_URL"] == "https://vanta.com/pricing"
    assert client.closed


def test_research_run_uses_two_stage_adapter_and_persists_scores(monkeypatch):
    """Exercise crawl -> extraction -> DB -> comparison -> saved chart without API calls."""
    from sqlalchemy import create_engine
    from sqlalchemy.dialects.postgresql import JSONB
    from sqlalchemy.ext.compiler import compiles
    from sqlalchemy.orm import Session

    @compiles(JSONB, "sqlite")
    def _jsonb_on_sqlite(element, compiler, **kw):
        return "JSON"

    from app.db.database import Base
    from app.db.repository import (
        create_competitor, create_research_run, get_overall_swot, get_latest_swot, get_research_run,
    )
    from app.services.crawler import MockCrawl4AICrawler
    from app.services.orchestrator import execute_research_run
    from app.api.serializers import _scores

    responses = {
        "PartialExtractedInformationByIndex": PartialExtractedInformationByIndex(
            summary="Vanta automates compliance and collects evidence.",
            summary_page_indexes=[0],
            job_postings=[JobCitedByIndex(
                role="Engineer", category="Engineering", job_page_index=0,
                job_families=[JobFamily(job_family="full-time", count=2)],
            )],
            claims_cited=[
                ClaimCitedByIndex(
                    category="feature", claim="Automated evidence collection", claim_page_index=0,
                ),
                ClaimCitedByIndex(
                    category="pricing", claim="Annual subscriptions", claim_page_index=1,
                ),
            ],
            conflicting_data=[ConflictCitedByIndex(
                claim_a="Subscription A", claim_b="Subscription B",
                claim_a_page_index=0, claim_b_page_index=1,
            )],
        ),
        "SWOTAnalysisByIndex": SWOTAnalysisByIndex(
            competitor_notes=[],
            SWOT_entries=[SWOTEntryByIndex(
                component="strength", what="SCADABLE automates evidence collection.",
                swot_citations=[
                    SWOTIndex(swot_component_company_index=0, swot_component_page_index=0),
                    SWOTIndex(swot_component_company_index=1, swot_component_page_index=0),
                ],
            )],
            scores=[
                FeatureScoreByIndex(
                    company_index=0,
                    feature="Continuous automated evidence collection from cloud providers and dev tools",
                    score=6,
                ),
                FeatureScoreByIndex(company_index=1, feature="Automated evidence collection", score=8),
            ],
            feature_pairs=[FeaturePairByIndex(
                company_index=1,
                competitor_feature="Automated evidence collection",
                scadable_feature="Continuous automated evidence collection from cloud providers and dev tools",
            )],
            competitor_swots=[CompetitorSWOTByIndex(
                company_index=1,
                SWOT_entries=[
                    SWOTEntryByIndex(
                        component="strength", what="Vanta automates evidence collection.",
                        swot_citations=[SWOTIndex(swot_component_company_index=1, swot_component_page_index=0)],
                    ),
                    SWOTEntryByIndex(
                        component="weakness", what="Vanta has annual subscriptions.",
                        swot_citations=[SWOTIndex(swot_component_company_index=1, swot_component_page_index=1)],
                    ),
                ],
            )],
        ),
    }
    clients = []

    def new_fake_client():
        client = FakeClient(responses)
        clients.append(client)
        return client

    monkeypatch.setattr(llm_service, "_build_openai_client", new_fake_client)
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            competitor = create_competitor(db, "Vanta", "https://vanta.com")
            run = create_research_run(db, competitor.id)
            result = asyncio.run(execute_research_run(
                db, run.id, crawler=MockCrawl4AICrawler(), analyzer=OpenAIAdapter(),
            ))
            assert result.status.value == "completed"
            analysis = get_research_run(db, run.id).analysis_result
            assert analysis["mode"] == "real"
            assert analysis["open_roles"] == 2
            assert analysis["extracted_information"]["claims_cited"][1]["category"] == "pricing"
            assert analysis["conflicting_data"]["conflicts"][0]["claim_b_page_URL"] == "https://vanta.com/pricing"
            assert analysis["scores"] == [{
                "feature": "Automated evidence collection", "score": 8, "scadable_score": 6,
            }]
            assert _scores(analysis)[0].scadable == 6
            competitor_swot = get_latest_swot(db, competitor.id)
            assert [(entry.category.value, entry.content, entry.source_url) for entry in competitor_swot] == [
                ("strength", "Vanta automates evidence collection.", "https://vanta.com/features"),
                ("weakness", "Vanta has annual subscriptions.", "https://vanta.com/pricing"),
            ]
            assert len(get_overall_swot(db)) == 1
            assert get_overall_swot(db)[0].source_url == "https://vanta.com/features"

            # An unchanged refresh must reuse the facts and chart scores, not
            # pay for another extraction or comparison.
            repeat = create_research_run(db, competitor.id)
            repeat_result = asyncio.run(execute_research_run(
                db, repeat.id, crawler=MockCrawl4AICrawler(), analyzer=OpenAIAdapter(),
            ))
            assert repeat_result.status.value == "completed"
            reused = get_research_run(db, repeat.id).analysis_result
            assert reused["skipped_llm"] is True
            assert reused["extracted_information"] == analysis["extracted_information"]
            assert reused["open_roles"] == 2
            assert reused["scores"] == analysis["scores"]
            assert [entry.content for entry in get_latest_swot(db, competitor.id)] == [
                entry.content for entry in competitor_swot
            ]
    finally:
        engine.dispose()

    assert len(clients) == 2
    assert [c.observation_names for c in clients] == [
        ["run-1:vanta:extraction"], ["run-1:comparison"],
    ]
    assert all(c.closed for c in clients)


def test_compare_returns_cited_competitor_swot_and_explicit_pairs(fake_client):
    from llm.schemas import ClaimCitedByURL, ExtractedInformationByURL, ConflictEntry

    client = fake_client({
        "SWOTAnalysisByIndex": SWOTAnalysisByIndex(
            competitor_notes=[], SWOT_entries=[],
            scores=[
                FeatureScoreByIndex(company_index=0, feature="Automated evidence collection", score=6),
                FeatureScoreByIndex(company_index=1, feature="Compliance evidence automation", score=9),
            ],
            feature_pairs=[FeaturePairByIndex(
                company_index=1, competitor_feature="Compliance evidence automation",
                scadable_feature="Automated evidence collection",
            )],
            competitor_swots=[CompetitorSWOTByIndex(
                company_index=1,
                SWOT_entries=[SWOTEntryByIndex(
                    component="strength", what="Vanta automates evidence handling.",
                    swot_citations=[
                        SWOTIndex(swot_component_company_index=0, swot_component_page_index=0),
                        SWOTIndex(swot_component_company_index=1, swot_component_page_index=0),
                    ],
                )],
            )],
        ),
    })
    facts = ExtractedInformationByURL(
        company_name="Vanta", summary="Automated compliance",
        summary_page_urls=["https://vanta.com/features"],
        claims_cited=[ClaimCitedByURL(category="feature", claim="Compliance evidence automation", claim_page_url="https://vanta.com/features")],
        job_postings=[], conflicting_data=ConflictEntry(company_name="Vanta", conflicts=[]),
        crawl_time_string="2026-11-06 17:00:00",
    )
    result = asyncio.run(OpenAIAdapter().compare(
        get_scadable_baseline(), [{
            "company_name": "Vanta", "website_url": "https://vanta.com",
            "extracted_information": facts.model_dump(mode="json"),
        }], trace_prefix="run-77",
    ))
    assert result["feature_pairs"] == [{
        "company_name": "Vanta", "competitor_feature": "Compliance evidence automation",
        "scadable_feature": "Automated evidence collection",
    }]
    assert result["competitor_swots"] == [{
        "company_name": "Vanta", "swot_entries": [{
            "category": "strength", "content": "Vanta automates evidence handling.",
            "source_url": "https://vanta.com/features",
        }],
    }]
    assert client.observation_names == ["run-77:comparison"]
