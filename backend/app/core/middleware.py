from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Awaitable, Callable

from fastapi import Request, Response

from app.core.errors import unhandled_exception_response
from app.core.request_context import get_request_id, reset_request_id, set_request_id

logger = logging.getLogger(__name__)

SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:/@+-]{1,128}$")


def choose_request_id(request: Request) -> str:
    incoming = request.headers.get("x-request-id") or request.headers.get(
        "x-correlation-id"
    )
    if incoming and SAFE_REQUEST_ID.fullmatch(incoming):
        return incoming
    return str(uuid.uuid4())


async def request_id_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    request_id = choose_request_id(request)
    token = set_request_id(request_id)
    request.state.request_id = request_id
    try:
        response = await call_next(request)
    finally:
        reset_request_id(token)
    response.headers["X-Request-ID"] = request_id
    return response


async def unhandled_exception_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Turns an unhandled exception into a 500 JSON response from inside the
    normal middleware stack, so it still passes back out through
    CORSMiddleware. See the comment in app/core/errors.py for why this can't
    be a plain @app.exception_handler(Exception) instead. AppError,
    RequestValidationError, and StarletteHTTPException are already resolved
    deeper in the stack (Starlette's ExceptionMiddleware, which sits inside
    CORSMiddleware too) and never reach here.
    """
    try:
        return await call_next(request)
    except Exception:
        request_id = get_request_id() or getattr(request.state, "request_id", None)
        logger.exception(
            "Unexpected application error",
            extra={"request_id": request_id},
        )
        return unhandled_exception_response(request_id)

