from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

import pytest

from app.core.errors import AppError
from app.modules.imports.csv_parsing import decode_csv_bytes, iter_csv_rows
from app.modules.imports.mapping import validate_mapping
from app.modules.imports.service import ImportService
from app.modules.leads.fields import PROFILE_FIELD_NAMES
from app.schemas.imports import LEADS_MAPPABLE_FIELDS, ImportMappingIn


class _FakeLeads:
    def ensure_recipient_address(self, **_: Any) -> None:
        return None


class _FakeImportRepo:
    """Records what _process_row would write; `existing` emulates a duplicate."""

    def __init__(self, *, existing: bool = False) -> None:
        self.leads = _FakeLeads()
        self.existing = existing
        self.upserted: dict[str, Any] | None = None
        self.results: list[dict[str, Any]] = []
        self.members: list[dict[str, Any]] = []

    def upsert_lead(self, **kwargs: Any) -> tuple[uuid.UUID, bool]:
        self.upserted = kwargs
        return uuid.uuid4(), not self.existing

    def insert_row_result(self, **kwargs: Any) -> None:
        self.results.append(kwargs)

    def add_member_if_missing(self, **kwargs: Any) -> None:
        self.members.append(kwargs)


def _process(
    row: Mapping[str, str],
    mapping: dict[str, str],
    *,
    existing: bool = False,
) -> tuple[str, _FakeImportRepo]:
    service = ImportService.__new__(ImportService)
    repo = _FakeImportRepo(existing=existing)
    service.repo = repo  # type: ignore[assignment]
    outcome = service._process_row(
        workspace_id=uuid.uuid4(),
        import_id=uuid.uuid4(),
        import_kind="LEADS",
        mapping=mapping,
        list_id=None,
        initiator_id=uuid.uuid4(),
        row_number=1,
        row=dict(row),
    )
    return outcome, repo


# --- mapping ------------------------------------------------------------


def test_every_profile_field_is_mappable() -> None:
    assert set(PROFILE_FIELD_NAMES) <= LEADS_MAPPABLE_FIELDS
    assert {"email", "first_name", "last_name", "company", "title"} <= (
        LEADS_MAPPABLE_FIELDS
    )


def test_mapping_accepts_new_fields_and_existing_five_field_mappings() -> None:
    headers = ["Email", "Job", "Phone #"]
    new = validate_mapping(
        "LEADS",
        ImportMappingIn(columns={"email": "Email", "title": "Job", "phone": "Phone #"}),
        headers,
    )
    assert new == {"email": "Email", "title": "Job", "phone": "Phone #"}

    legacy = validate_mapping(
        "LEADS", ImportMappingIn(columns={"email": "Email", "title": "Job"}), headers
    )
    assert legacy == {"email": "Email", "title": "Job"}


@pytest.mark.parametrize(
    "target",
    ["workspace_id", "status", "custom_fields", "validation_status", "industry"],
)
def test_mapping_still_rejects_non_allow_listed_targets(target: str) -> None:
    with pytest.raises(AppError, match="Unsupported mapping target"):
        validate_mapping(
            "LEADS",
            ImportMappingIn(columns={"email": "Email", target: "Email"}),
            ["Email"],
        )


def test_suppression_imports_cannot_map_profile_fields() -> None:
    with pytest.raises(AppError, match="Unsupported mapping target"):
        validate_mapping(
            "SUPPRESSION",
            ImportMappingIn(columns={"email": "Email", "phone": "Phone"}),
            ["Email", "Phone"],
        )


# --- csv ----------------------------------------------------------------


def test_bom_csv_with_legacy_headers_still_parses() -> None:
    text = decode_csv_bytes(
        "﻿email,first_name,company\nada@example.com,Ada,Analytical\n".encode()
    )
    assert list(iter_csv_rows(text)) == [
        (1, {"email": "ada@example.com", "first_name": "Ada", "company": "Analytical"})
    ]


# --- row processing -----------------------------------------------------


def test_row_with_all_new_fields_is_stored_normalized() -> None:
    outcome, repo = _process(
        {
            "Email": "Ada@Example.com",
            "Phone": "+1 (555) 123-4567",
            "Site": "example.com",
            "LI": "linkedin.com/in/ada",
            "Years": "7",
            "City": " Berlin ",
            "Founded": "1999",
            "CLI": "https://www.linkedin.com/company/acme",
        },
        {
            "email": "Email",
            "phone": "Phone",
            "website": "Site",
            "linkedin_url": "LI",
            "experience_years": "Years",
            "city": "City",
            "company_founded_year": "Founded",
            "company_linkedin_url": "CLI",
        },
    )

    assert outcome == "ACCEPTED"
    assert repo.upserted is not None
    assert repo.upserted["profile"] == {
        "phone": "+1 (555) 123-4567",
        "linkedin_url": "https://linkedin.com/in/ada",
        "website": "https://example.com",
        "city": "Berlin",
        "experience_years": 7,
        "company_founded_year": 1999,
        "company_linkedin_url": "https://www.linkedin.com/company/acme",
    }
    assert repo.results[0]["status"] == "ACCEPTED"
    assert repo.results[0]["validation_reason"] is None


def test_invalid_optional_values_are_dropped_not_rejected() -> None:
    outcome, repo = _process(
        {
            "Email": "ada@example.com",
            "Phone": "call me",
            "Site": "javascript:alert(1)",
            "City": "Berlin",
        },
        {"email": "Email", "phone": "Phone", "website": "Site", "city": "City"},
    )

    assert outcome == "ACCEPTED"
    assert repo.upserted is not None
    assert repo.upserted["profile"] == {"city": "Berlin"}
    assert repo.results[0]["status"] == "ACCEPTED"
    assert repo.results[0]["validation_reason"] == "ignored_invalid:phone,website"


def test_unmapped_profile_columns_write_nothing() -> None:
    outcome, repo = _process(
        {"Email": "ada@example.com", "Phone": "+1 555 123 4567"}, {"email": "Email"}
    )

    assert outcome == "ACCEPTED"
    assert repo.upserted is not None
    assert repo.upserted["profile"] == {}


def test_duplicate_row_reports_duplicate_without_warning() -> None:
    outcome, repo = _process(
        {"Email": "ada@example.com", "Phone": "nope"},
        {"email": "Email", "phone": "Phone"},
        existing=True,
    )

    assert outcome == "DUPLICATE"
    assert repo.results[0]["status"] == "DUPLICATE"
    assert repo.results[0]["validation_reason"] is None


@pytest.mark.parametrize(
    ("row", "reason"),
    [
        ({"Email": ""}, "missing_email"),
        ({"Email": "not-an-email"}, "invalid_email"),
    ],
)
def test_bad_email_still_rejects_the_row(row: dict[str, str], reason: str) -> None:
    outcome, repo = _process(row, {"email": "Email"})

    assert outcome == "REJECTED"
    assert repo.upserted is None
    assert repo.results[0]["validation_reason"] == reason
