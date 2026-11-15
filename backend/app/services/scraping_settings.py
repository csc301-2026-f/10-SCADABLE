"""Persistent, application-wide scraping depth for competitor research.

The backend currently has no per-account identity model, so this is a shared
setting for the deployment (not an account-specific preference). New crawls
read the value at run time; already-running crawls keep their existing limit.
"""

from typing import Literal

from sqlalchemy.orm import Session

from app.db.models import ScrapingSettings

ScrapingDepth = Literal["low", "medium", "high"]
PAGE_LIMITS: dict[ScrapingDepth, int] = {"low": 5, "medium": 10, "high": 15}
DEFAULT_DEPTH: ScrapingDepth = "low"


def get_scraping_depth(db: Session) -> ScrapingDepth:
    stored = db.get(ScrapingSettings, 1)
    return stored.depth if stored is not None else DEFAULT_DEPTH


def get_scraping_page_limit(db: Session) -> int:
    return PAGE_LIMITS[get_scraping_depth(db)]


def set_scraping_depth(db: Session, depth: ScrapingDepth) -> int:
    """Commit a new setting; other workers see it on their next crawl."""
    if depth not in PAGE_LIMITS:
        raise ValueError("Scraping depth must be low, medium, or high.")
    stored = db.get(ScrapingSettings, 1)
    if stored is None:
        stored = ScrapingSettings(id=1, depth=depth)
        db.add(stored)
    else:
        stored.depth = depth
    db.commit()
    return PAGE_LIMITS[depth]
