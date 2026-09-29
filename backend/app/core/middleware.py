from __future__ import annotations

import logging
import re
import uuid
from collections.abc import Awaitable, Callable

import psycopg
from fastapi import Request, Response
from sqlalchemy.exc import DBAPIError, InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from app.core.errors import service_unavailable_response, unhandled_exception_response
from app.core.request_context import get_request_id, reset_request_id, set_request_id

logger = logging.getLogger(__name__)

SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:/@+-]{1,128}$")

# How many characters of the database driver's own message go to the log. Enough
# to tell "max clients reached" from "connection reset"; never sent to the client.
_LOG_DETAIL_CHARS = 200


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


def is_transient_database_failure(exc: BaseException) -> bool:
    """True when the database was unreachable or too busy (retrying may succeed).

    Covers: no free pooled connection within the pool timeout, a connect that
    failed or timed out (including the pooler refusing new clients), a
    connection dropped mid-request, and statement/lock timeouts. Anything else
    -- a bad query, a missing function, a constraint violation -- is a real
    defect and stays a 500.
    """
    if isinstance(exc, PoolTimeoutError | OperationalError | InterfaceError):
        return True
    if isinstance(exc, DBAPIError) and exc.connection_invalidated:
        return True
    return isinstance(exc, psycopg.OperationalError | psycopg.InterfaceError)


def _pool_status() -> str:
    try:
        from app.core.config import Settings
        from app.db.session import engine_from_settings

        return engine_from_settings(Settings.current()).pool.status()
    except Exception:  # diagnostics must never turn into a second failure
        return "unavailable"


def _log_transient_database_failure(exc: BaseException, request_id: str | None) -> None:
    orig = getattr(exc, "orig", exc)
    logger.warning(
        "Database temporarily unavailable: type=%s sqlstate=%s detail=%r pool=%s",
        type(exc).__name__,
        getattr(orig, "sqlstate", None),
        str(orig)[:_LOG_DETAIL_CHARS],
        _pool_status(),
        extra={"request_id": request_id},
    )


async def unhandled_exception_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    """Turns an unhandled exception into a JSON error response from inside the
    normal middleware stack, so it still passes back out through
    CORSMiddleware. See the comment in app/core/errors.py for why this can't
    be a plain @app.exception_handler(Exception) instead. AppError,
    RequestValidationError, and StarletteHTTPException are already resolved
    deeper in the stack (Starlette's ExceptionMiddleware, which sits inside
    CORSMiddleware too) and never reach here.

    A transient database failure becomes a 503 with Retry-After (a retry is
    safe and useful); every other exception is a 500 with a full traceback in
    the log.
    """
    try:
        return await call_next(request)
    except Exception as exc:
        request_id = get_request_id() or getattr(request.state, "request_id", None)
        if is_transient_database_failure(exc):
            _log_transient_database_failure(exc, request_id)
            return service_unavailable_response(request_id)
        logger.exception(
            "Unexpected application error",
            extra={"request_id": request_id},
        )
        return unhandled_exception_response(request_id)
