from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from app.core.errors import AppError

NORMALIZATION_VERSION = 1
_FORBIDDEN_LOCAL_CHARS = set('<>(),;:"\\[]')
_DOMAIN_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


@dataclass(frozen=True)
class NormalizedEmail:
    original: str
    canonical: str
    normalization_version: int = NORMALIZATION_VERSION


def normalize_email(value: str) -> NormalizedEmail:
    original = value.strip()
    if len(original) < 3 or len(original) > 320:
        raise AppError(
            "validation_error",
            "Email must be 3 to 320 characters",
            status_code=422,
        )
    if any(ch.isspace() for ch in original) or any(
        ch in '<>(),;:"\\[]' for ch in original
    ):
        raise AppError(
            "validation_error",
            "Email must be a single mailbox address",
            status_code=422,
        )
    if any(ord(ch) < 33 or ord(ch) > 126 for ch in original):
        raise AppError(
            "validation_error",
            "Email must use printable ASCII characters",
            status_code=422,
        )
    if original.count("@") != 1:
        raise AppError(
            "validation_error",
            "Email must contain exactly one @",
            status_code=422,
        )

    local, domain = original.split("@", 1)
    if not local or not domain:
        raise AppError(
            "validation_error",
            "Email must include a local part and domain",
            status_code=422,
        )
    if any(ch in _FORBIDDEN_LOCAL_CHARS for ch in local):
        raise AppError(
            "validation_error",
            "Email contains unsupported characters",
            status_code=422,
        )

    try:
        ascii_domain = domain.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise AppError(
            "validation_error",
            "Email domain is invalid",
            status_code=422,
        ) from exc

    labels = ascii_domain.split(".")
    if len(labels) < 2 or any(
        not _DOMAIN_LABEL_RE.fullmatch(label) for label in labels
    ):
        raise AppError("validation_error", "Email domain is invalid", status_code=422)

    canonical = f"{local.lower()}@{ascii_domain}"
    if len(canonical) > 320:
        raise AppError(
            "validation_error",
            "Email must be 3 to 320 characters",
            status_code=422,
        )
    return NormalizedEmail(original=original, canonical=canonical)


def clean_optional_text(value: str | None, field_name: str) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if cleaned == "":
        return None
    if len(cleaned) > 200:
        raise AppError(
            "validation_error",
            f"{field_name} must be 200 characters or fewer",
            status_code=422,
        )
    return cleaned


def validate_custom_fields(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise AppError(
            "validation_error",
            "Custom fields must be an object",
            status_code=422,
        )
    try:
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise AppError(
            "validation_error",
            "Custom fields must be JSON serializable",
            status_code=422,
        ) from exc
    if len(encoded.encode("utf-8")) > 16_384:
        raise AppError(
            "validation_error",
            "Custom fields must be 16KB or smaller",
            status_code=422,
        )
    return value
