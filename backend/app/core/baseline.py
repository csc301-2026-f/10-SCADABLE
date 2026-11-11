"""SCADABLE fallback baseline; validated GitHub snapshots take precedence."""

from typing import Any
from copy import deepcopy

SCADABLE_BASELINE: dict[str, Any] = {
    "company_name": "SCADABLE",
    "website_url": "https://scadable.com",
    "domain": "Automated Security Compliance & Regulatory Governance SaaS",
    "summary": (
        "SCADABLE is an automated compliance and regulatory governance platform "
        "providing real-time policy updates, continuous evidence collection, and "
        "audit readiness tracking across critical security frameworks."
    ),
    "supported_frameworks": [
        "SOC 2 Type I & II",
        "ISO 27001:2022",
        "HIPAA Security & Privacy Rules",
        "EU Cyber Resilience Act (CRA)",
        "GDPR",
    ],
    "core_capabilities": [
        "Continuous automated evidence collection from cloud providers and dev tools",
        "Developer-first compliance and automated code remediation guidance",
        "Real-time regulatory change alerting and policy mapping",
        "Integrated auditor portal with stream-based evidence verification",
        "Multi-cloud support (AWS, Azure, GCP) and source control (GitHub, GitLab)",
    ],
    "differentiators": [
        "Native tracking for European regulatory frameworks like the EU CRA",
        "Direct code-level remediation rather than passive configuration checks",
        "Lower total cost of ownership compared to legacy compliance platforms",
    ],
}


def get_scadable_baseline(db: Any = None) -> dict[str, Any]:
    """Get the last validated GitHub snapshot, or the static bootstrap baseline.

    The active snapshot is stored in PostgreSQL; passing the session allows
    research runs and the company API to use the same data. A failed/pending
    GitHub sync intentionally retains the last *successful* snapshot.
    """
    if db is not None and hasattr(db, "get"):
        from app.db.models import GitHubFeatureState, GitHubRepositorySelection
        selection = db.get(GitHubRepositorySelection, 1)
        if selection is not None:
            state = db.get(GitHubFeatureState, 1)
            if state is not None and isinstance(state.baseline_data, dict):
                return deepcopy(state.baseline_data)
    return deepcopy(SCADABLE_BASELINE)
