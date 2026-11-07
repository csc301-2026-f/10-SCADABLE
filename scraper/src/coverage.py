"""Category-based hints, not evidence verification or capability-absence claims."""

from collections.abc import Sequence
from dataclasses import dataclass

from .content_identity import markdown_fingerprint
from .models import FetchedPage, ScrapedPage

TOPIC_CATEGORIES = {
    "products_services": {"products", "services", "features"},
    "target_customers": {"positioning"},
    "pricing": {"pricing"},
    "integrations": {"integrations"},
    "differentiators": {"positioning"},
    "commercial_activity": {"news", "press_release"},
    "careers_hiring": {"careers", "hiring"},
}
METHOD = (
    "Primary-category approximation on unique successful pages with usefulness >= 2/3. "
    "Positioning optimistically indicates both target customers and differentiators. "
    "Hints do not confirm evidence or establish absence of a capability."
)


@dataclass(frozen=True)
class CoverageContext:
    """A fixed-size state vector; source excerpts/URLs never enter candidate hints."""

    states: tuple[str, ...]

    def __post_init__(self):
        if len(self.states) != len(TOPIC_CATEGORIES) or any(
            state not in {"indicated", "not_indicated", "unknown"} for state in self.states
        ):
            raise ValueError("Coverage requires seven valid topic states")

    @classmethod
    def from_summary(cls, summary: dict):
        return cls(
            tuple(
                summary["topics"].get(topic, {}).get("state", "unknown")
                for topic in TOPIC_CATEGORIES
            )
        )

    def as_prompt(self) -> dict:
        return {
            "method": METHOD,
            **{
                label: [
                    topic
                    for topic, state in zip(TOPIC_CATEGORIES, self.states, strict=True)
                    if state == value
                ]
                for label, value in (
                    ("indicated", "indicated"),
                    ("gaps", "not_indicated"),
                    ("unknown", "unknown"),
                )
            },
        }


def coverage_summary(pages: Sequence[FetchedPage | ScrapedPage], *, available: bool = True) -> dict:
    sources = {topic: [] for topic in TOPIC_CATEGORIES}
    unknown = not available
    seen = set()
    for page in pages:
        if page.crawl_status != "success" or page.metadata.get("duplicate_of"):
            continue
        body = page.cleaned_markdown if isinstance(page, ScrapedPage) else page.raw_markdown
        fingerprint = markdown_fingerprint(body)
        if fingerprint is None or fingerprint in seen:
            continue
        seen.add(fingerprint)
        assessment = page.content_assessment
        if (
            assessment is None
            or assessment.usefulness is None
            or assessment.category_confidence is None
            or assessment.issues
        ):
            unknown = True
            continue
        if assessment.usefulness < 2 / 3:
            continue
        for topic, categories in TOPIC_CATEGORIES.items():
            if assessment.category.value in categories:
                sources[topic].append(
                    {
                        "url": str(page.source_url),
                        "category": assessment.category.value,
                        "usefulness": assessment.usefulness,
                    }
                )
    return {
        "method": METHOD,
        "topics": {
            topic: {
                "state": "indicated" if evidence else ("unknown" if unknown else "not_indicated"),
                "sources": evidence,
            }
            for topic, evidence in sources.items()
        },
    }
