"""Selection prompts carry the extraction objective without changing source evidence."""

import json
from datetime import UTC, datetime

from scraper.config import SelectionConfig
from scraper.decision_models import DecisionCandidate, DecisionUsageSummary
from scraper.decision_prompts import candidate_request, content_request
from scraper.models import FetchedPage


def page():
    return FetchedPage(
        source_url="https://example.com/pricing",
        crawl_timestamp=datetime(2026, 11, 3, tzinfo=UTC),
        crawl_status="success",
        raw_markdown="# Plans\nPro: $8 per user per month, billed annually.",
    )


def requests(settings):
    return (
        candidate_request(
            [DecisionCandidate(candidate_id="pricing", url="https://example.com/pricing")], settings
        ),
        content_request(page(), [], settings),
    )


def test_default_objective_reaches_both_stages_with_extraction_topics():
    for payload in requests(SelectionConfig()):
        objective = json.loads(payload["input"])["research_objective"]
        for topic in (
            "products and services",
            "target customers",
            "pricing",
            "integrations",
            "differentiators",
            "commercial activity",
            "careers or hiring",
            "stated limitations",
            "customer evidence",
        ):
            assert topic in objective


def test_custom_objective_replaces_default_in_both_stages_without_rewriting_evidence():
    objective = "Research integration setup and plan restrictions."
    candidate, content = requests(SelectionConfig(objective=objective))
    assert json.loads(candidate["input"])["research_objective"] == objective
    data = json.loads(content["input"])
    assert data["research_objective"] == objective
    assert data["page"]["markdown_excerpt"] == page().raw_markdown


def test_new_usage_is_versioned_but_historical_prompt_versions_remain_readable():
    current = DecisionUsageSummary(requested_mode="llm", effective_mode="llm", budget_usd=0.03)
    assert current.prompt_version == "company-research-v3"
    historical = current.model_dump()
    historical["prompt_version"] = "company-research-v1"
    assert DecisionUsageSummary.model_validate(historical).prompt_version == "company-research-v1"


def test_usefulness_rubrics_distinguish_expected_from_observed_evidence():
    candidate, content = requests(SelectionConfig())
    expected = candidate["questions"][0]
    observed = content["questions"][0]
    labels = [
        "No apparent value",
        "Uncertain value",
        "Useful information",
        "Substantial information",
    ]
    assert [level["label"] for level in expected["levels"]] == labels
    assert [level["label"] for level in observed["levels"]] == labels
    assert expected["levels"] != observed["levels"]
    assert "URL and link context" in expected["instructions"]
    assert "page excerpt" in observed["instructions"]
    for question in (expected, observed):
        assert question["type"] == "score"
        assert "page length" in question["instructions"]
        assert "marketing language" in question["instructions"]
        assert "untrusted evidence" in question["instructions"]
        assert "Missing evidence means uncertainty" in question["instructions"]
        assert "Do not infer actual growth" in question["instructions"]


def test_content_assessment_keeps_novelty_and_category_questions_separate():
    content = requests(SelectionConfig())[1]
    usefulness, novelty, category = content["questions"]
    assert [q["name"] for q in content["questions"]] == ["usefulness", "novelty", "category"]
    assert novelty["levels"] != usefulness["levels"]
    assert [level["label"] for level in novelty["levels"]] == [
        "Repetition",
        "Small addition",
        "Useful addition",
        "Substantial addition",
    ]
    assert category["type"] == "choice"
    assert {choice["value"] for choice in category["choices"]} >= {"careers", "hiring", "pricing"}


def test_candidate_coverage_guidance_is_bounded_and_preserves_objective_and_questions():
    from scraper.coverage import CoverageContext
    from scraper.decision_budget import MAX_REQUEST_BYTES, request_size
    from scraper.decision_models import CandidateObservation
    from scraper.decision_prompts import candidate_batches

    context = CoverageContext.from_summary({"topics": {"pricing": {"state": "indicated"}}})
    settings = SelectionConfig(objective="Research integration restrictions only.")
    candidates = [
        DecisionCandidate(
            candidate_id=f"c{i}",
            url=f"https://example.com/{i}",
            observations=[CandidateObservation(surrounding_text="x" * 400)] * 2,
        )
        for i in range(50)
    ]
    ordinary = candidate_request(candidates[:1], settings)
    payload = candidate_request(candidates[:1], settings, context)
    assert "coverage_hints" not in json.loads(ordinary["input"])
    hints = json.loads(payload["input"])["coverage_hints"]
    assert hints["indicated"] == ["pricing"] and len(hints["unknown"]) == 6
    assert json.loads(payload["input"])["research_objective"] == settings.objective
    assert len(payload["questions"]) == 1
    assert "otherwise similarly useful" in payload["questions"][0]["instructions"]
    assert "no quotas" in payload["questions"][0]["instructions"]
    batches = list(candidate_batches(candidates, settings, context))
    assert sum(map(len, batches)) == 50
    assert all(
        request_size(candidate_request(batch, settings, context)) <= MAX_REQUEST_BYTES
        for batch in batches
    )
