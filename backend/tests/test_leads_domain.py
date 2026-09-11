from __future__ import annotations

import uuid

import pytest

from app.core.errors import AppError
from app.modules.leads.normalization import (
    clean_optional_text,
    normalize_email,
    validate_custom_fields,
)
from app.modules.leads.pagination import decode_cursor, encode_cursor, normalize_limit


def test_email_normalization_preserves_plus_and_dots() -> None:
    result = normalize_email(" Alice.Example+demo@EXAMPLE.COM ")

    assert result.original == "Alice.Example+demo@EXAMPLE.COM"
    assert result.canonical == "alice.example+demo@example.com"
    assert result.normalization_version == 1


def test_email_normalization_rejects_display_name_and_controls() -> None:
    with pytest.raises(AppError, match="single mailbox"):
        normalize_email("Alice <alice@example.com>")

    with pytest.raises(AppError):
        normalize_email("alice\n@example.com")


def test_email_normalization_rejects_malformed_domain() -> None:
    with pytest.raises(AppError, match="domain"):
        normalize_email("alice@example")

    with pytest.raises(AppError, match="domain"):
        normalize_email("alice@-example.com")


def test_optional_text_is_trimmed_and_empty_becomes_none() -> None:
    assert clean_optional_text("  Acme  ", "Company") == "Acme"
    assert clean_optional_text("   ", "Company") is None


def test_custom_fields_must_be_bounded_json_object() -> None:
    assert validate_custom_fields({"segment": "founder"}) == {"segment": "founder"}

    with pytest.raises(AppError, match="16KB"):
        validate_custom_fields({"large": "x" * 20_000})


def test_cursor_roundtrip_and_bounds() -> None:
    lead_id = uuid.uuid4()
    assert decode_cursor(encode_cursor(lead_id)) == lead_id
    assert normalize_limit(25) == 25
    assert normalize_limit(10_000) == 100

    with pytest.raises(AppError, match="Cursor"):
        decode_cursor("not-a-cursor")
