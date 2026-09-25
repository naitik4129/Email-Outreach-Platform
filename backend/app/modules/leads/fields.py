from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from app.core.errors import AppError
from app.modules.leads.normalization import (
    clean_int_range,
    clean_optional_text,
    clean_optional_url,
    clean_phone,
)

FieldKind = Literal["text", "phone", "url", "linkedin_url", "int", "year"]
ProfileValue = str | int | None

# Mirrors the CHECK constraints in supabase/migrations/0021_lead_profile_fields.sql.
_EXPERIENCE_YEARS = (0, 80)
_FOUNDED_YEAR_MIN = 1600


@dataclass(frozen=True)
class ProfileField:
    name: str
    label: str
    kind: FieldKind
    searchable: bool = False


# Column names below are interpolated into SQL by LeadRepository, so this tuple
# must remain the only source of them; never build ProfileField from request data.
PROFILE_FIELDS: tuple[ProfileField, ...] = (
    ProfileField("phone", "Phone", "phone"),
    ProfileField("department", "Department", "text", searchable=True),
    ProfileField("experience_years", "Experience", "int"),
    ProfileField("linkedin_url", "LinkedIn", "linkedin_url"),
    ProfileField("website", "Website", "url"),
    ProfileField("city", "City", "text", searchable=True),
    ProfileField("state", "State", "text", searchable=True),
    ProfileField("country", "Country", "text", searchable=True),
    ProfileField("company_website", "Company website", "url"),
    ProfileField("company_industry", "Company industry", "text", searchable=True),
    ProfileField("company_founded_year", "Company founded year", "year"),
    ProfileField("company_linkedin_url", "Company LinkedIn", "linkedin_url"),
)
PROFILE_FIELD_NAMES: tuple[str, ...] = tuple(f.name for f in PROFILE_FIELDS)
SEARCHABLE_PROFILE_COLUMNS: tuple[str, ...] = tuple(
    f.name for f in PROFILE_FIELDS if f.searchable
)


def clean_profile_value(field: ProfileField, value: Any) -> ProfileValue:
    if field.kind == "text":
        return clean_optional_text(value, field.label)
    if field.kind == "phone":
        return clean_phone(value, field.label)
    if field.kind == "url":
        return clean_optional_url(value, field.label)
    if field.kind == "linkedin_url":
        return clean_optional_url(value, field.label, linkedin=True)
    if field.kind == "int":
        return clean_int_range(
            value,
            field.label,
            minimum=_EXPERIENCE_YEARS[0],
            maximum=_EXPERIENCE_YEARS[1],
        )
    # A founding year cannot be in the future; the DB only bounds it to 2100.
    return clean_int_range(
        value,
        field.label,
        minimum=_FOUNDED_YEAR_MIN,
        maximum=datetime.now(UTC).year,
    )


def clean_profile_fields(raw: Mapping[str, Any]) -> dict[str, ProfileValue]:
    """Validate the profile fields present in ``raw``; absent keys stay absent.

    An explicit None/blank is kept as None so an update can clear a field.
    Raises AppError(422) on the first invalid value.
    """
    return {
        field.name: clean_profile_value(field, raw[field.name])
        for field in PROFILE_FIELDS
        if field.name in raw
    }


def clean_profile_fields_lenient(
    raw: Mapping[str, Any],
) -> tuple[dict[str, ProfileValue], list[str]]:
    """Like clean_profile_fields, but drop invalid values instead of raising.

    Used by CSV import, where one malformed optional cell must not reject an
    otherwise good lead. Returns the cleaned values and the names that were
    dropped so the caller can record a warning.
    """
    cleaned: dict[str, ProfileValue] = {}
    ignored: list[str] = []
    for field in PROFILE_FIELDS:
        if field.name not in raw:
            continue
        try:
            cleaned[field.name] = clean_profile_value(field, raw[field.name])
        except AppError:
            ignored.append(field.name)
    return cleaned, ignored
