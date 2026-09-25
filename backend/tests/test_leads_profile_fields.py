from __future__ import annotations

import re
import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from app.api.deps import WorkspaceContext
from app.core.errors import AppError
from app.modules.leads.fields import (
    PROFILE_FIELD_NAMES,
    PROFILE_FIELDS,
    SEARCHABLE_PROFILE_COLUMNS,
    clean_profile_fields,
    clean_profile_fields_lenient,
)
from app.modules.leads.normalization import (
    clean_int_range,
    clean_optional_url,
    clean_phone,
)
from app.modules.leads.repository import LeadRepository, contains_pattern
from app.modules.leads.service import LeadService, _lead_out
from app.schemas.leads import LeadCreateIn, LeadOut, LeadUpdateIn

MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "supabase"
    / "migrations"
    / "0021_lead_profile_fields.sql"
)


# --- URLs ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://example.com", "https://example.com"),
        ("  example.com/about  ", "https://example.com/about"),
        ("http://www.example.co.uk/path?q=1#x", "http://www.example.co.uk/path?q=1#x"),
        ("example.com:8080/x", "https://example.com:8080/x"),
        ("//example.com", "https://example.com"),
        ("", None),
        ("   ", None),
        (None, None),
    ],
)
def test_url_is_normalized_or_cleared(raw: str | None, expected: str | None) -> None:
    assert clean_optional_url(raw, "Website") == expected


@pytest.mark.parametrize(
    "raw",
    [
        "javascript:alert(1)",
        "JavaScript:alert(1)",
        "data:text/html;base64,AAAA",
        "mailto:a@example.com",
        "ftp://example.com",
        "file:///etc/passwd",
        "https://user:pass@example.com",
        "https://localhost",
        "https://exa mple.com",
        "https://",
        "https://example.com:notaport",
        "https://-bad-.com",
        "https://" + "a" * 500 + ".com",
    ],
)
def test_url_rejects_unsafe_or_malformed_values(raw: str) -> None:
    with pytest.raises(AppError) as excinfo:
        clean_optional_url(raw, "Website")
    assert excinfo.value.status_code == 422


def test_linkedin_url_requires_linkedin_host() -> None:
    assert (
        clean_optional_url("linkedin.com/in/ada", "LinkedIn", linkedin=True)
        == "https://linkedin.com/in/ada"
    )
    assert (
        clean_optional_url("https://uk.linkedin.com/in/ada", "LinkedIn", linkedin=True)
        == "https://uk.linkedin.com/in/ada"
    )
    for bad in ("https://notlinkedin.com/in/ada", "https://linkedin.com.evil.io/x"):
        with pytest.raises(AppError, match="linkedin.com"):
            clean_optional_url(bad, "LinkedIn", linkedin=True)


# --- phone / numbers ----------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("+1 (555) 123-4567", "+1 (555) 123-4567"),
        ("555.123.4567", "555.123.4567"),
        ("  +44   20  7946 0958 ", "+44 20 7946 0958"),
        ("555-123-4567 ext 89", "555-123-4567 ext 89"),
        ("555-123-4567 x12", "555-123-4567 x12"),
        ("  ", None),
        (None, None),
    ],
)
def test_phone_is_validated_but_not_rewritten(
    raw: str | None, expected: str | None
) -> None:
    assert clean_phone(raw, "Phone") == expected


@pytest.mark.parametrize(
    "raw",
    [
        "call me",
        "12345",
        "1" * 16,
        "+1 555 123 4567 ext 1234567",
        "555-123-4567;rm",
        "1" * 33,
    ],
)
def test_phone_rejects_non_phone_values(raw: str) -> None:
    with pytest.raises(AppError) as excinfo:
        clean_phone(raw, "Phone")
    assert excinfo.value.status_code == 422


def test_int_range_accepts_strings_and_rejects_junk() -> None:
    assert clean_int_range("12", "Experience", minimum=0, maximum=80) == 12
    assert clean_int_range(" 0 ", "Experience", minimum=0, maximum=80) == 0
    assert clean_int_range("", "Experience", minimum=0, maximum=80) is None
    assert clean_int_range(None, "Experience", minimum=0, maximum=80) is None
    for bad in ("5.5", "ten", "-1", "81", True, 999):
        with pytest.raises(AppError):
            clean_int_range(bad, "Experience", minimum=0, maximum=80)  # type: ignore[arg-type]


def test_founded_year_cannot_be_in_the_future() -> None:
    this_year = datetime.now(UTC).year
    assert clean_profile_fields({"company_founded_year": str(this_year)}) == {
        "company_founded_year": this_year
    }
    with pytest.raises(AppError):
        clean_profile_fields({"company_founded_year": this_year + 1})
    with pytest.raises(AppError):
        clean_profile_fields({"company_founded_year": 1599})


# --- registry -----------------------------------------------------------


def test_clean_profile_fields_only_returns_present_keys() -> None:
    assert clean_profile_fields({}) == {}
    assert clean_profile_fields({"city": "  Berlin ", "phone": None, "state": ""}) == {
        "city": "Berlin",
        "phone": None,
        "state": None,
    }


def test_lenient_cleaning_drops_only_invalid_values() -> None:
    cleaned, ignored = clean_profile_fields_lenient(
        {
            "city": "Berlin",
            "website": "javascript:alert(1)",
            "phone": "nope",
            "experience_years": "7",
            "unknown_column": "x",
        }
    )
    assert cleaned == {"city": "Berlin", "experience_years": 7}
    assert ignored == ["phone", "website"]  # registry order


def test_registry_matches_api_schemas() -> None:
    for model in (LeadCreateIn, LeadUpdateIn, LeadOut):
        missing = set(PROFILE_FIELD_NAMES) - set(model.model_fields)
        assert not missing, f"{model.__name__} lacks {missing}"


def test_registry_names_are_unique_safe_identifiers() -> None:
    assert len(set(PROFILE_FIELD_NAMES)) == len(PROFILE_FIELD_NAMES)
    assert all(re.fullmatch(r"[a-z][a-z_]*", name) for name in PROFILE_FIELD_NAMES)
    assert set(SEARCHABLE_PROFILE_COLUMNS) <= set(PROFILE_FIELD_NAMES)
    assert {f.kind for f in PROFILE_FIELDS} <= {
        "text",
        "phone",
        "url",
        "linkedin_url",
        "int",
        "year",
    }


def test_migration_covers_every_registry_field() -> None:
    """Guards drift between the Python registry and 0021's columns/grants/trigger."""
    sql = MIGRATION.read_text(encoding="utf-8")
    grants = re.findall(
        r"GRANT (INSERT|UPDATE) \(([^)]*)\) ON public\.leads TO (\w+);", sql
    )
    assert sorted((verb, role) for verb, _, role in grants) == [
        ("INSERT", "app_api"),
        ("INSERT", "app_worker_general"),
        ("UPDATE", "app_api"),
        ("UPDATE", "app_worker_general"),
    ]
    for _, columns, _ in grants:
        assert {c.strip() for c in columns.split(",")} == set(PROFILE_FIELD_NAMES)
    for name in PROFILE_FIELD_NAMES:
        assert re.search(rf"ADD COLUMN {name} ", sql), name
        assert f"NEW.{name}" in sql and f"OLD.{name}" in sql, name


# --- service / repository wiring ---------------------------------------


def _row(**overrides: Any) -> dict[str, Any]:
    now = datetime.now(UTC)
    row: dict[str, Any] = {
        "id": uuid.uuid4(),
        "workspace_id": uuid.uuid4(),
        "original_address": "ada@example.com",
        "canonical_address": "ada@example.com",
        "normalization_version": 1,
        "first_name": None,
        "last_name": None,
        "company": None,
        "title": None,
        **{name: None for name in PROFILE_FIELD_NAMES},
        "custom_fields": {},
        "status": "ACTIVE",
        "validation_status": "UNKNOWN",
        "validated_at": None,
        "contact_revision": 1,
        "archived_at": None,
        "version": 1,
        "created_at": now,
        "updated_at": now,
    }
    row.update(overrides)
    return row


def _context() -> WorkspaceContext:
    return WorkspaceContext(
        workspace_id=uuid.uuid4(), user_id=uuid.uuid4(), role_code="MEMBER"
    )


class _RecordingRepo:
    def __init__(self) -> None:
        self.created: dict[str, Any] | None = None
        self.updated: dict[str, Any] | None = None

    def ensure_recipient_address(self, **_: Any) -> None:
        return None

    def create_lead(self, **kwargs: Any) -> Mapping[str, Any]:
        self.created = kwargs
        return _row(**kwargs["profile"])

    def update_lead(self, **kwargs: Any) -> Mapping[str, Any]:
        self.updated = kwargs
        return _row(**kwargs["values"])


def _service() -> tuple[LeadService, _RecordingRepo]:
    service = LeadService.__new__(LeadService)
    repo = _RecordingRepo()
    service.repo = repo  # type: ignore[assignment]
    return service, repo


def test_lead_out_exposes_profile_fields() -> None:
    out = _lead_out(_row(city="Berlin", experience_years=7))
    assert out.city == "Berlin"
    assert out.experience_years == 7
    assert out.phone is None


def test_create_lead_cleans_and_passes_profile_fields() -> None:
    service, repo = _service()
    out = service.create_lead(
        _context(),
        LeadCreateIn(
            email="ada@example.com",
            phone=" +1 555 123 4567 ",
            website="example.com",
            linkedin_url="linkedin.com/in/ada",
            city="  Berlin ",
            experience_years=7,
            company_founded_year=1999,
        ),
    )

    assert repo.created is not None
    assert repo.created["profile"]["phone"] == "+1 555 123 4567"
    assert repo.created["profile"]["website"] == "https://example.com"
    assert repo.created["profile"]["city"] == "Berlin"
    assert repo.created["profile"]["state"] is None
    assert out.linkedin_url == "https://linkedin.com/in/ada"


def test_create_lead_rejects_invalid_profile_value_with_422() -> None:
    service, repo = _service()
    with pytest.raises(AppError) as excinfo:
        service.create_lead(
            _context(),
            LeadCreateIn(email="ada@example.com", website="javascript:alert(1)"),
        )
    assert excinfo.value.status_code == 422
    assert repo.created is None


def test_update_lead_distinguishes_unsent_from_explicit_null() -> None:
    service, repo = _service()

    service.update_lead(
        _context(),
        uuid.uuid4(),
        LeadUpdateIn.model_validate(
            {"expected_version": 1, "city": None, "state": "BE"}
        ),
    )

    assert repo.updated is not None
    # Explicit null clears; fields that were never sent are not touched.
    assert repo.updated["values"] == {"city": None, "state": "BE"}


def test_update_lead_with_only_unsent_profile_fields_is_rejected() -> None:
    service, _ = _service()
    with pytest.raises(AppError, match="No mutable fields"):
        service.update_lead(
            _context(),
            uuid.uuid4(),
            LeadUpdateIn.model_validate({"expected_version": 1}),
        )


class _FakeResult:
    def mappings(self) -> _FakeResult:
        return self

    def one(self) -> dict[str, Any]:
        return _row()

    def all(self) -> list[dict[str, Any]]:
        return []


class _FakeSession:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def execute(
        self, statement: Any, params: dict[str, Any] | None = None
    ) -> _FakeResult:
        self.calls.append((str(statement), params or {}))
        return _FakeResult()


def test_repository_insert_lists_every_profile_column_and_param() -> None:
    session = _FakeSession()
    LeadRepository(session).create_lead(  # type: ignore[arg-type]
        workspace_id=uuid.uuid4(),
        original_address="ada@example.com",
        canonical_address="ada@example.com",
        first_name=None,
        last_name=None,
        company=None,
        title=None,
        profile={"city": "Berlin"},
        custom_fields={},
    )

    sql, params = session.calls[0]
    for name in PROFILE_FIELD_NAMES:
        assert re.search(rf"\b{name}\b", sql), name
        assert f":{name}" in sql
        assert name in params
    assert params["city"] == "Berlin"
    assert params["phone"] is None


def test_repository_search_covers_profile_text_columns() -> None:
    session = _FakeSession()
    LeadRepository(session).list_leads(  # type: ignore[arg-type]
        workspace_id=uuid.uuid4(),
        limit=10,
        after_id=None,
        status=None,
        query="berlin",
        list_id=None,
    )

    sql, params = session.calls[0]
    assert params["query"] == "%berlin%"
    for column in (
        "title",
        "department",
        "city",
        "state",
        "country",
        "company_industry",
    ):
        assert f"l.{column} ILIKE :query ESCAPE '\\'" in sql
    # Non-text and URL/phone fields are deliberately not searched.
    for column in ("phone", "linkedin_url", "website", "experience_years"):
        assert f"l.{column} ILIKE" not in sql


@pytest.mark.parametrize(
    ("typed", "pattern"),
    [
        ("berlin", "%berlin%"),
        ("100%", r"%100\%%"),
        ("a_b", r"%a\_b%"),
        ("back\\slash", r"%back\\slash%"),
        ("%_", r"%\%\_%"),
    ],
)
def test_search_text_is_matched_literally(typed: str, pattern: str) -> None:
    assert contains_pattern(typed) == pattern


def test_search_passes_the_escaped_pattern_to_the_query() -> None:
    session = _FakeSession()
    LeadRepository(session).list_leads(  # type: ignore[arg-type]
        workspace_id=uuid.uuid4(),
        limit=10,
        after_id=None,
        status=None,
        query="50%_off",
        list_id=None,
    )

    _, params = session.calls[0]
    assert params["query"] == r"%50\%\_off%"
