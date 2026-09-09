from __future__ import annotations

import re
import uuid
from collections.abc import Awaitable, Callable

from fastapi import Request, Response

from app.core.request_context import reset_request_id, set_request_id

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

