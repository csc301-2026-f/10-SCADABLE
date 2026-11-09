"""Contract tests for backend job-posting extraction and role counts."""

from pydantic import BaseModel

from app.services.open_roles import (
    count_open_roles,
    normalize_job_postings,
    open_roles_from_analysis,
)


class Family(BaseModel):
    job_family: str
    count: int


class Job(BaseModel):
    role: str
    category: str
    job_page_url: str
    job_families: list[Family]


def test_count_from_llm_models_and_preserve_source():
    raw = [
        Job(role="Software Engineer", category="Engineering", job_page_url="https://x.com/careers",
            job_families=[Family(job_family="full-time", count=3),
                          Family(job_family="co-op/internship", count=2)]),
        Job(role="Analyst", category="Data", job_page_url="https://x.com/jobs",
            job_families=[Family(job_family="full-time", count=1)]),
    ]
    normalized = normalize_job_postings(raw)
    assert len(normalized) == 2
    assert normalized[0]["job_page_url"] == "https://x.com/careers"
    assert normalized[0]["job_families"] == [
        {"job_family": "full-time", "count": 3},
        {"job_family": "co-op/internship", "count": 2},
    ]
    assert count_open_roles(normalized) == 6
    assert open_roles_from_analysis({"job_postings": normalized}) == 6


def test_missing_data_returns_unknown_not_zero():
    assert count_open_roles(None) is None
    assert count_open_roles([]) is None
    assert open_roles_from_analysis({}) is None
    assert open_roles_from_analysis({"job_postings": []}) is None
    assert open_roles_from_analysis({"open_roles": None}) is None


def test_ignores_invalid_counts_and_missing_citations():
    raw = [
        {"role": "Engineer", "category": "Engineering", "job_page_url": "https://x.com/jobs",
         "job_families": [
             {"job_family": "full-time", "count": True},
             {"job_family": "full-time", "count": -3},
             {"job_family": "full-time", "count": "5"},
             {"job_family": "part-time", "count": 2},
         ]},
        {"role": "Uncited", "category": "Engineering", "job_families": [
            {"job_family": "full-time", "count": 7},
        ]},
        None,
    ]
    assert count_open_roles(raw) == 2
    assert normalize_job_postings(raw) == [{
        "role": "Engineer", "category": "Engineering", "job_page_url": "https://x.com/jobs",
        "job_families": [{"job_family": "part-time", "count": 2}],
    }]


def test_explicit_valid_zero_and_fallback_from_saved_job_postings():
    assert count_open_roles([{"role": "Job", "category": "Other", "job_page_url": "https://x.com",
                              "job_families": [{"job_family": "other", "count": 0}]}]) == 0
    assert open_roles_from_analysis({"open_roles": 0}) == 0
    assert open_roles_from_analysis({"open_roles": True}) is None
    assert open_roles_from_analysis({"open_roles": -10, "job_postings": [
        {"role": "Job", "category": "Other", "job_page_url": "https://x.com",
         "job_families": [{"job_family": "other", "count": 2}]}
    ]}) == 2
