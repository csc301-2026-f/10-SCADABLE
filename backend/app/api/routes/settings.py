"""Read and update the deployment-wide scraping depth setting."""

import os
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import ConfigDict
from sqlalchemy.orm import Session

from app.api.schemas import ApiModel
from app.db.database import get_db
from app.services.scraping_settings import (
    PAGE_LIMITS,
    ScrapingDepth,
    get_scraping_depth,
    set_scraping_depth,
)

router = APIRouter(prefix="/settings/scraping", tags=["settings"])


class ScrapingSettingsResponse(ApiModel):
    depth: ScrapingDepth
    max_pages: int


class UpdateScrapingSettings(ApiModel):
    model_config = ConfigDict(extra="forbid")
    depth: ScrapingDepth


def require_settings_admin(authorization: str | None = Header(default=None)) -> None:
    """Do not allow unauthenticated users to increase billable crawl depth.

    The admin token belongs on a trusted server (for example a Next.js server
    route), never in a NEXT_PUBLIC_* variable or a browser request.
    """
    expected = os.getenv("SCRAPING_SETTINGS_ADMIN_TOKEN", "")
    if not expected:
        raise HTTPException(status_code=503, detail="Scraping settings updates are not configured.")
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not secrets.compare_digest(token, expected):
        raise HTTPException(status_code=401, detail="Unauthorized.")


@router.get("", response_model=ScrapingSettingsResponse, summary="Read scraping depth")
def read_scraping_settings(db: Session = Depends(get_db)) -> ScrapingSettingsResponse:
    """Read the shared depth and maximum pages per future crawl."""
    depth = get_scraping_depth(db)
    return ScrapingSettingsResponse(depth=depth, max_pages=PAGE_LIMITS[depth])


@router.put(
    "", response_model=ScrapingSettingsResponse,
    dependencies=[Depends(require_settings_admin)],
    summary="Update scraping depth (admin only)",
)
def update_scraping_settings(
    payload: UpdateScrapingSettings,
    db: Session = Depends(get_db),
) -> ScrapingSettingsResponse:
    """Update the shared limit for future research runs, not already-running crawls."""
    max_pages = set_scraping_depth(db, payload.depth)
    return ScrapingSettingsResponse(depth=payload.depth, max_pages=max_pages)
