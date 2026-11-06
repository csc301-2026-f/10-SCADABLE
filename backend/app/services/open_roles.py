"""Backend mapping of the LLM's existing cited job-posting extraction.

The LLM owns extraction; this module only validates/serializes its result for
storage and for the competitor profile's ``openRoles`` field.
"""

from typing import Any


def _as_mapping(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if callable(getattr(value, "model_dump", None)):
        data = value.model_dump(mode="json")
        return data if isinstance(data, dict) else None
    return None


def normalize_job_postings(raw: Any) -> list[dict[str, Any]]:
    """Keep only well-formed, cited job families; don't infer missing counts.

    Each LLM posting represents a role/category with counts split by job
    family. Both the citation URL and the role are required to retain it.
    """
    if not isinstance(raw, list):
        return []

    postings: list[dict[str, Any]] = []
    for item in raw:
        job = _as_mapping(item)
        if job is None:
            continue
        role = job.get("role")
        category = job.get("category")
        source = job.get("job_page_url")
        if not all(isinstance(v, str) and v.strip() for v in (role, category, source)):
            continue
        raw_families = job.get("job_families")
        if not isinstance(raw_families, list):
            continue

        families = []
        for raw_family in raw_families:
            family = _as_mapping(raw_family)
            if family is None:
                continue
            name = family.get("job_family")
            count = family.get("count")
            if (not isinstance(name, str) or not name.strip()
                    or type(count) is not int or count < 0):
                continue
            families.append({"job_family": name.strip(), "count": count})
        if not families:
            continue

        postings.append({
            "role": role.strip(),
            "category": category.strip(),
            "job_page_url": source.strip(),
            "job_families": families,
        })
    return postings


def count_open_roles(postings: Any) -> int | None:
    """Sum supported role counts; unknown is not the same as zero openings.

    No extracted postings is insufficient evidence to claim a company is not
    hiring, because the crawler may simply not have visited its careers pages.
    """
    normalized = normalize_job_postings(postings)
    if not normalized:
        return None
    return sum(family["count"] for job in normalized for family in job["job_families"])


def open_roles_from_analysis(analysis: dict[str, Any]) -> int | None:
    """Read stored count, or derive it from already-saved LLM job postings."""
    count = analysis.get("open_roles")
    if type(count) is int and count >= 0:
        return count
    return count_open_roles(analysis.get("job_postings"))
