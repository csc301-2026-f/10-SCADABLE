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
    assert current.prompt_version == "company-research-v2"
    historical = current.model_dump()
    historical["prompt_version"] = "company-research-v1"
    assert DecisionUsageSummary.model_validate(historical).prompt_version == "company-research-v1"
