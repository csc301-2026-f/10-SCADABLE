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
from app.api.routes import company, competitors, runs, swot

logger = logging.getLogger(__name__)

DEFAULT_CORS_ORIGINS = "http://localhost:3000,http://127.0.0.1:3000"


def _cors_origins() -> list[str]:
    raw = os.getenv("CORS_ORIGINS", DEFAULT_CORS_ORIGINS)
    return [origin.strip().rstrip("/") for origin in raw.split(",") if origin.strip()]


@asynccontextmanager
async def _lifespan(app: FastAPI):
    # Explicit opt-in: refreshing competitor sites incurs crawler/LLM costs.
    scheduler_task = None
    if os.getenv("AUTO_REFRESH_ENABLED", "false").lower() == "true":
        from app.services.refresh_scheduler import run_scheduler

        poll_seconds = float(os.getenv("COMPETITOR_REFRESH_POLL_SECONDS", "60"))
        if poll_seconds <= 0:
            raise ValueError("COMPETITOR_REFRESH_POLL_SECONDS must be positive")
        scheduler_task = asyncio.create_task(run_scheduler(poll_seconds=poll_seconds))
        logger.info("Automatic competitor refresh scheduler enabled")
    try:
        yield
    finally:
        if scheduler_task is not None:
            scheduler_task.cancel()
            with suppress(asyncio.CancelledError):
                await scheduler_task


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
    return app


app = create_app()
