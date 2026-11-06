"""Map domain and validation errors to consistent JSON error responses.

Every error body is ``{"detail": "<human readable message>"}``.
"""

import logging

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from app.db.repository import (
    DuplicateCompetitorError,
    InvalidStatusTransitionError,
    NotFoundError,
    ResearchAlreadyActiveError,
)

logger = logging.getLogger(__name__)


class ConflictError(Exception):
    """Request conflicts with current state (e.g. research already running)."""


def _error(status_code: int, detail: str) -> JSONResponse:
    return JSONResponse(status_code=status_code, content={"detail": detail})


def _format_validation_error(exc: RequestValidationError) -> str:
    messages = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error.get("loc", ()) if part != "body")
        message = str(error.get("msg", "Invalid value")).removeprefix("Value error, ")
        messages.append(f"{location}: {message}" if location else message)
    return "; ".join(messages) or "Invalid request."


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        return _error(status.HTTP_422_UNPROCESSABLE_CONTENT, _format_validation_error(exc))

    @app.exception_handler(NotFoundError)
    async def _not_found(_: Request, exc: NotFoundError) -> JSONResponse:
        return _error(status.HTTP_404_NOT_FOUND, str(exc) or "Not found.")

    @app.exception_handler(DuplicateCompetitorError)
    async def _duplicate(_: Request, exc: DuplicateCompetitorError) -> JSONResponse:
        return _error(
            status.HTTP_409_CONFLICT,
            str(exc) or "A competitor with this website already exists.",
        )

    @app.exception_handler(InvalidStatusTransitionError)
    @app.exception_handler(ConflictError)
    @app.exception_handler(ResearchAlreadyActiveError)
    async def _conflict(_: Request, exc: Exception) -> JSONResponse:
        return _error(status.HTTP_409_CONFLICT, str(exc) or "Conflict.")

    @app.exception_handler(ValueError)
    async def _value_error(request: Request, exc: ValueError) -> JSONResponse:
        # pydantic.ValidationError subclasses ValueError; here it means our own
        # response building failed, which is a server bug, not bad input.
        if isinstance(exc, ValidationError):
            logger.exception("Response validation failed on %s", request.url.path)
            return _error(status.HTTP_500_INTERNAL_SERVER_ERROR, "Internal server error.")
        return _error(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc) or "Invalid value.")

    @app.exception_handler(Exception)
    async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error on %s %s", request.method, request.url.path)
        return _error(status.HTTP_500_INTERNAL_SERVER_ERROR, "Internal server error.")
