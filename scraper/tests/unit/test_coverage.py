import pytest

from scraper.coverage import coverage_summary
from scraper.decision_models import PageAssessment
from scraper.models import FetchedPage
from scraper.types import utc_now


def page(category="pricing", usefulness=2 / 3, **assessment):
    return FetchedPage(
        source_url="https://example.com/",
        crawl_timestamp=utc_now(),
        crawl_status="success",
        raw_markdown="Evidence",
        content_assessment=PageAssessment(
            category=category, usefulness=usefulness, category_confidence=0.9, **assessment
        ),
    )


@pytest.mark.parametrize(
    "category, topics",
    [
        ("products", {"products_services"}),
        ("services", {"products_services"}),
        ("features", {"products_services"}),
        ("positioning", {"target_customers", "differentiators"}),
        ("pricing", {"pricing"}),
        ("integrations", {"integrations"}),
        ("news", {"commercial_activity"}),
        ("press_release", {"commercial_activity"}),
        ("careers", {"careers_hiring"}),
        ("hiring", {"careers_hiring"}),
        ("blog", set()),
    ],
)
def test_seven_topic_mapping_is_explicit_approximation(category, topics):
    summary = coverage_summary([page(category)])
    assert {
        key for key, value in summary["topics"].items() if value["state"] == "indicated"
    } == topics
    assert len(summary["topics"]) == 7
    assert "approximation" in summary["method"]


@pytest.mark.parametrize(
    "score, state",
    [
        (2 / 3 - 0.0001, "not_indicated"),
        (2 / 3, "indicated"),
        (1.0, "indicated"),
        (None, "unknown"),
    ],
)
def test_usefulness_boundary_and_missing_usage(score, state):
    assert coverage_summary([page(usefulness=score)])["topics"]["pricing"]["state"] == state


@pytest.mark.parametrize("issue", ["invalid", "refused", "unavailable"])
def test_invalid_assessments_and_duplicates_add_no_hints(issue):
    source = page(issues={"category": issue})
    duplicate = page("integrations").model_copy(update={"metadata": {"duplicate_of": "source"}})
    summary = coverage_summary([source, duplicate])
    assert all(topic["state"] == "unknown" for topic in summary["topics"].values())


def test_exact_bodies_do_not_inflate_sources_even_with_crawler_dedup_disabled():
    source = page()
    duplicate = source.model_copy(update={"source_url": "https://example.com/copy"})
    assert len(coverage_summary([source, duplicate])["topics"]["pricing"]["sources"]) == 1
    assert all(
        value["state"] == "unknown"
        for value in coverage_summary([], available=False)["topics"].values()
    )
