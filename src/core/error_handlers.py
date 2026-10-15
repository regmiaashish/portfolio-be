"""Turn every error into the same JSON shape: {success: false, message, errors}."""

import math
import time
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from loguru import logger
from slowapi.errors import RateLimitExceeded
from starlette.exceptions import HTTPException as StarletteHTTPException

from src.core.exceptions import AppException
from src.schemas.common_schema import ErrorResponse


def error_response(
    status_code: int,
    message: str,
    errors: Any = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    body = ErrorResponse(message=message, errors=errors)
    return JSONResponse(status_code=status_code, content=body.model_dump(), headers=headers)


async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
    if exc.status_code >= 500:
        logger.opt(exception=exc).error(
            "{} {} failed: {}", request.method, request.url.path, exc.message
        )
    return error_response(exc.status_code, exc.message, exc.errors)


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    message = exc.detail if isinstance(exc.detail, str) else HTTPStatus(exc.status_code).phrase
    return error_response(exc.status_code, message, headers=getattr(exc, "headers", None))


async def validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    # Only location, message and type: submitted values (e.g. passwords) are never echoed back.
    errors = [
        {"loc": list(error.get("loc", ())), "msg": error.get("msg", ""), "type": error.get("type")}
        for error in exc.errors()
    ]
    return error_response(422, "Request validation failed.", errors)


def _retry_after_seconds(request: Request, exc: RateLimitExceeded) -> int:
    """Seconds until the caller's current window resets (falls back to the window length)."""
    try:
        limit_item, identifiers = request.state.view_rate_limit
        window = request.app.state.limiter.limiter.get_window_stats(limit_item, *identifiers)
        return max(1, math.ceil(window.reset_time - time.time()))
    except Exception:  # noqa: BLE001 -- best effort; a header estimate must never fail the 429
        return int(exc.limit.limit.get_expiry())


async def rate_limit_exceeded_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    retry_after = _retry_after_seconds(request, exc)
    return error_response(
        429,
        f"Too many requests. Try again in {retry_after} seconds.",
        headers={"Retry-After": str(retry_after)},
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.opt(exception=exc).error("Unhandled error on {} {}", request.method, request.url.path)
    # This response is sent from outside the middleware stack (Starlette's
    # ServerErrorMiddleware), so the security headers are added here.
    policy = getattr(request.app.state, "security_headers", None)
    headers = policy.for_path(request.url.path) if policy is not None else None
    return error_response(500, "Internal server error.", headers=headers)


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppException, app_exception_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(RateLimitExceeded, rate_limit_exceeded_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
