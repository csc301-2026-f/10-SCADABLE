import pytest

from scraper import ScraperConfig
from scraper.decision_models import CandidateObservation, CandidateScore, PageAssessment
from scraper.discovery.traversal import UrlFrontier


def frontier(**settings):
    value = UrlFrontier("https://example.com/", ScraperConfig(**settings))
    value.complete(str(value.pop_next().normalized_url), success=True)
    return value


def score_all(value, score=0.9):
    candidates = value.evaluation_candidates()
    value.apply_scores(
        candidates,
        [
            CandidateScore(candidate_id=item.candidate_id, score=score, status="scored")
            for item in candidates
        ],
    )
    return candidates


def test_opaque_urls_are_ranked_by_decisions_not_page_names():
    value = frontier()
    value.discover("/pricing")
    value.discover("/atlas", observation=CandidateObservation(anchor_text="Contract review"))
    candidates = value.evaluation_candidates()
    value.apply_scores(
        candidates,
        [
            CandidateScore(
                candidate_id=item.candidate_id,
                score=0.95 if str(item.url).endswith("atlas") else 0.1,
                status="scored",
            )
            for item in candidates
        ],
    )
    assert str(value.pop_next().normalized_url).endswith("/atlas")


def test_every_fifth_non_homepage_attempt_explores_and_robots_refusal_rolls_back():
    value = frontier()
    for index in range(8):
        value.discover(f"/known/{index}")
    score_all(value)
    value.discover("/unseen")
    for _ in range(4):
        record = value.pop_next()
        assert record.selection_method == "ranked"
        value.complete(str(record.normalized_url), success=True)
    rejected = value.pop_next()
    assert rejected.selection_method == "exploration"
    assert str(rejected.normalized_url).endswith("/unseen")
    value.reject_reserved(str(rejected.normalized_url), reason="robots_disallowed")
    assert value.non_homepage_attempts == 4
    replacement = value.pop_next()
    assert replacement.selection_method == "exploration"


def test_evaluation_candidates_are_fair_bounded_and_skip_policy_ineligible_urls():
    value = frontier(max_depth=1)
    for index in range(100):
        value.discover(f"/articles/{index}")
    value.discover("/atlas")
    value.discover("/login")
    value.discover("/too-deep", depth=2)
    value.discover("https://external.example/opportunity")
    candidates = value.evaluation_candidates()
    assert len(candidates) == 50
    assert any(str(item.url).endswith("/atlas") for item in candidates[:2])
    assert all("external.example" not in str(item.url) for item in candidates)
    assert all(not str(item.url).endswith(("login", "too-deep")) for item in candidates)


def test_cached_scores_only_refresh_once_for_materially_new_context():
    value = frontier()
    value.discover("/atlas", observation=CandidateObservation(anchor_text="Atlas"))
    score_all(value)
    value.discover("/atlas#again", observation=CandidateObservation(anchor_text=" Atlas "))
    assert value.evaluation_candidates() == []

    value.discover(
        "/atlas",
        observation=CandidateObservation(
            anchor_text="Atlas contract platform",
            surrounding_text="Review agreements",
            location="main_content",
        ),
    )
    second = score_all(value)
    assert len(second) == 1 and len(second[0].observations) <= 2
    value.discover(
        "/atlas",
        observation=CandidateObservation(
            anchor_text="Atlas new capabilities",
            surrounding_text="Entirely different description",
            location="main_content",
        ),
    )
    assert value.evaluation_candidates() == []


def test_refused_refresh_clears_old_score_and_cannot_promote_unknown_ids():
    value = frontier()
    value.discover("/atlas", title="Atlas")
    score_all(value)
    value.discover("/atlas", observation=CandidateObservation(surrounding_text="New information"))
    candidates = value.evaluation_candidates()
    value.apply_scores(
        candidates,
        [
            CandidateScore(candidate_id=candidates[0].candidate_id, status="refused"),
            CandidateScore(candidate_id="invented", score=1.0, status="scored"),
        ],
    )
    assert value.get("https://example.com/atlas").relevance_score is None
    assert value.pop_next().selection_method == "exploration"


def test_duplicate_semantic_context_from_another_parent_does_not_spend_refresh():
    value = frontier()
    for parent in ("https://example.com/a", "https://example.com/b"):
        value.discover(
            "/atlas",
            observation=CandidateObservation(
                parent_url=parent, anchor_text="Atlas", location="navigation"
            ),
        )
        if parent.endswith("/a"):
            score_all(value)
    assert value.evaluation_candidates() == []
    value.discover(
        "/atlas",
        observation=CandidateObservation(
            anchor_text="Atlas contract platform",
            surrounding_text="Review legal agreements",
            location="main_content",
        ),
    )
    assert len(value.evaluation_candidates()) == 1


def test_structural_fallback_ignores_old_model_scores_and_bfs_remains_depth_first():
    value = frontier()
    for index in range(4):
        value.discover(f"/articles/{index}")
    score_all(value)
    value.complete(str(value.pop_next().normalized_url), success=True)
    value.discover("/unknown")
    value.set_mode("structural")
    record = value.pop_next()
    assert str(record.normalized_url).endswith("/unknown")
    assert record.selection_method == "structural"
    bfs = frontier(strategy="bfs")
    bfs.discover("/deep", depth=2)
    bfs.discover("/shallow", depth=1)
    score_all(bfs)
    assert str(bfs.pop_next().normalized_url).endswith("/shallow")


def test_content_novelty_and_section_diversity_contribute_to_ranked_priority():
    value = frontier()
    for section in ("a", "b"):
        value.discover(f"/{section}/first")
    score_all(value)
    first = value.pop_next()
    value.complete(str(first.normalized_url), success=True)
    value.note_assessment(str(first.normalized_url), PageAssessment(novelty=0.0))
    value.discover("/a/next")
    score_all(value)
    assert str(value.pop_next().normalized_url).endswith("/b/first")


@pytest.mark.parametrize("fraction,method", [(0.0, "ranked"), (1.0, "exploration")])
def test_configured_exploration_endpoints(fraction, method):
    value = frontier(selection={"exploration_fraction": fraction})
    value.discover("/opaque")
    score_all(value)
    assert value.pop_next().selection_method == method


def test_observed_alias_does_not_collapse_variants_or_policy_exclusions():
    value = frontier(excluded_url_patterns=["blocked"])
    for url in ("/target", "/target/", "/target?q=1", "/blocked", "http://example.com/target"):
        value.discover(url)
    value.register_final_url("https://example.com/target", "https://example.com/")
    value.register_final_url("https://example.com/blocked", "https://example.com/")
    assert value.get("https://example.com/target").reason.startswith("duplicate_redirect:")
    assert (
        value.get("https://example.com/blocked").reason != "duplicate_redirect:https://example.com/"
    )
    for url in ("/target/", "/target?q=1", "http://example.com/target"):
        assert (
            value.get(url if url.startswith("http") else "https://example.com" + url).disposition
            == "pending"
        )


def test_in_flight_alias_remains_an_attempt_and_first_representative_wins():
    value = frontier()
    value.discover("/alias")
    reserved = value.pop_next()
    value.register_final_url("https://example.com/alias", "https://example.com/")
    value.complete(str(reserved.normalized_url), success=True)
    assert value.attempted_pages == 2
    assert value.get("https://example.com/alias").disposition == "crawled"


def test_coverage_changes_share_existing_single_refresh_allowance():
    from scraper.coverage import CoverageContext

    value = frontier()
    value.discover("/candidate")
    score_all(value)
    pricing = CoverageContext.from_summary({"topics": {"pricing": {"state": "indicated"}}})
    value.set_coverage_context(pricing)
    assert len(score_all(value)) == 1
    value.set_coverage_context(CoverageContext.from_summary({"topics": {}}))
    value.discover("/candidate", observation=CandidateObservation(anchor_text="New context"))
    assert value.evaluation_candidates() == []
    assert value.get("https://example.com/candidate").relevance_score == 0.9
