from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

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


_MAX_URL_CHARS = 500
_MAX_PHONE_CHARS = 32
# "word:" that is not "host:port". These are rejected rather than treated as a
# hostname so "javascript:alert(1)" can never be stored as a link.
_NON_HTTP_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*:(?!\d+(?:/|$))")
_EXPLICIT_SCHEME_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://")
_PHONE_EXTENSION_RE = re.compile(
    r"^(?P<main>.*?)(?:\s*(?:ext\.?|x)\s*(?P<ext>\d{1,6}))?$", re.I
)
_PHONE_MAIN_RE = re.compile(r"^\+?[0-9()\-.\s]+$")


def clean_optional_url(
    value: str | None, field_name: str, *, linkedin: bool = False
) -> str | None:
    """Return an http(s) URL, defaulting a missing scheme to https.

    Values are rendered into emails and UI links, so any other scheme is
    rejected outright instead of being silently rewritten.
    """
    if value is None:
        return None
    cleaned = value.strip()
    if cleaned == "":
        return None
    invalid = AppError(
        "validation_error",
        f"{field_name} must be a valid http(s) URL",
        status_code=422,
    )
    if any(ch.isspace() for ch in cleaned):
        raise invalid
    if not _EXPLICIT_SCHEME_RE.match(cleaned):
        if _NON_HTTP_SCHEME_RE.match(cleaned):
            raise invalid
        cleaned = f"https://{cleaned.lstrip('/')}"
    if len(cleaned) > _MAX_URL_CHARS:
        raise AppError(
            "validation_error",
            f"{field_name} must be {_MAX_URL_CHARS} characters or fewer",
            status_code=422,
        )
    try:
        parts = urlsplit(cleaned)
        host = parts.hostname
        _ = parts.port  # raises ValueError for a malformed port
    except ValueError as exc:
        raise invalid from exc
    if parts.scheme.lower() not in {"http", "https"} or not host:
        raise invalid
    if parts.username is not None or parts.password is not None:
        raise invalid
    try:
        ascii_host = host.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise invalid from exc
    labels = ascii_host.split(".")
    if len(labels) < 2 or any(
        not _DOMAIN_LABEL_RE.fullmatch(label) for label in labels
    ):
        raise invalid
    if linkedin and not (
        ascii_host == "linkedin.com" or ascii_host.endswith(".linkedin.com")
    ):
        raise AppError(
            "validation_error",
            f"{field_name} must be a linkedin.com URL",
            status_code=422,
        )
    return cleaned


def clean_phone(value: str | None, field_name: str) -> str | None:
    """Validate a phone number without rewriting it to E.164.

    Formats vary too much across imported CSVs to normalize safely without a
    dedicated library, so the value is kept as entered (whitespace collapsed).
    """
    if value is None:
        return None
    cleaned = " ".join(value.split())
    if cleaned == "":
        return None
    invalid = AppError(
        "validation_error",
        f"{field_name} must be a valid phone number",
        status_code=422,
    )
    if len(cleaned) > _MAX_PHONE_CHARS:
        raise AppError(
            "validation_error",
            f"{field_name} must be {_MAX_PHONE_CHARS} characters or fewer",
            status_code=422,
        )
    match = _PHONE_EXTENSION_RE.match(cleaned)
    main = match.group("main").strip() if match else ""
    if not _PHONE_MAIN_RE.fullmatch(main):
        raise invalid
    digits = sum(ch.isdigit() for ch in main)
    if not 7 <= digits <= 15:
        raise invalid
    return cleaned


def clean_int_range(
    value: int | str | None, field_name: str, *, minimum: int, maximum: int
) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        raise AppError(
            "validation_error", f"{field_name} must be a whole number", status_code=422
        )
    if isinstance(value, str):
        stripped = value.strip()
        if stripped == "":
            return None
        if not re.fullmatch(r"-?\d{1,9}", stripped):
            raise AppError(
                "validation_error",
                f"{field_name} must be a whole number",
                status_code=422,
            )
        value = int(stripped)
    if not minimum <= value <= maximum:
        raise AppError(
            "validation_error",
            f"{field_name} must be between {minimum} and {maximum}",
            status_code=422,
        )
    return value


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
