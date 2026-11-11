"""FastAPI application entry point.

Run locally from ``backend/``:  uvicorn app.main:app --reload
"""

import os
import asyncio
from contextlib import asynccontextmanager, suppress
import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.errors import register_error_handlers
from app.api.routes import company, competitors, github, runs, swot

logger = logging.getLogger(__name__)

DEFAULT_CORS_ORIGINS = "http://localhost:3000,http://127.0.0.1:3000"


def _cors_origins() -> list[str]:
    raw = os.getenv("CORS_ORIGINS", DEFAULT_CORS_ORIGINS)
    return [origin.strip().rstrip("/") for origin in raw.split(",") if origin.strip()]


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # Explicit opt-in: refreshing competitor sites incurs crawler/LLM costs.
    scheduler_task = None
    github_task = None
    if os.getenv("AUTO_REFRESH_ENABLED", "false").lower() == "true":
        from app.services.refresh_scheduler import run_scheduler

        poll_seconds = float(os.getenv("COMPETITOR_REFRESH_POLL_SECONDS", "60"))
        if poll_seconds <= 0:
            raise ValueError("COMPETITOR_REFRESH_POLL_SECONDS must be positive")
        scheduler_task = asyncio.create_task(run_scheduler(poll_seconds=poll_seconds))
        logger.info("Automatic competitor refresh scheduler enabled")
    # GitHub file reads are free; the worker only runs when credentials exist.
    if (
        os.getenv("GITHUB_SYNC_ENABLED", "true").lower() == "true"
        and os.getenv("GITHUB_APP_ID")
        and os.getenv("GITHUB_APP_PRIVATE_KEY")
    ):
        from app.services.github_sync import run_github_sync_worker
        poll = float(os.getenv("GITHUB_SYNC_POLL_SECONDS", "60"))
        if poll <= 0:
            raise ValueError("GITHUB_SYNC_POLL_SECONDS must be positive")
        github_task = asyncio.create_task(run_github_sync_worker(poll_seconds=poll))
        logger.info("GitHub feature sync worker enabled")
    try:
        yield
    finally:
        for task in (scheduler_task, github_task):
            if task is not None:
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task


def create_app() -> FastAPI:
    app = FastAPI(title="Live SWOT API", version="0.1.0", lifespan=_lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_cors_origins(),
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Accept", "Content-Type", "Authorization"],
    )
    register_error_handlers(app)

    @app.get("/health", tags=["health"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(competitors.router)
    app.include_router(runs.router)
    app.include_router(swot.router)
    app.include_router(company.router)
    app.include_router(github.router)
    return app


app = create_app()
