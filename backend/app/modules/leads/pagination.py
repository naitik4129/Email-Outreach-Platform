from __future__ import annotations

import base64
import json
from uuid import UUID

from app.core.errors import AppError

DEFAULT_LIMIT = 25
MAX_LIMIT = 100


def normalize_limit(limit: int) -> int:
    if limit < 1:
        raise AppError("validation_error", "Limit must be at least 1", status_code=422)
    return min(limit, MAX_LIMIT)


def encode_cursor(resource_id: UUID | str) -> str:
    payload = json.dumps(
        {"id": str(resource_id)},
        separators=(",", ":"),
    ).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_cursor(cursor: str | None) -> UUID | None:
    if not cursor:
        return None
    try:
        padded = cursor + ("=" * (-len(cursor) % 4))
        data = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        return UUID(str(data["id"]))
    except Exception as exc:
        raise AppError(
            "validation_error",
            "Cursor is invalid",
            status_code=422,
        ) from exc
