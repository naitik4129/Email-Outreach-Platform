from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.modules.leads.fields import PROFILE_FIELD_NAMES

ImportKind = Literal["LEADS", "SUPPRESSION"]
ImportStatus = Literal[
    "PENDING",
    "PROCESSING",
    "COMPLETED",
    "COMPLETED_WITH_ERRORS",
    "FAILED",
]
ImportRowStatus = Literal["ACCEPTED", "DUPLICATE", "REJECTED"]

# Lead fields a CSV column may be mapped onto. Deliberately excludes
# workspace_id/id/created_at/version/status and every other internal or
# security-relevant column -- see CLAUDE.md Phase 3 task, "Column Mapping".
LEADS_MAPPABLE_FIELDS = frozenset(
    {"email", "first_name", "last_name", "company", "title", *PROFILE_FIELD_NAMES}
)
# A SUPPRESSION-kind import may only ever create MANUAL suppressions; reason
# is never a mapping target (see docs/architecture/SUPPRESSION.md and
# USER_ROLES.md -- only manually triggered sources may be actively created by
# UI/import in Phase 3).
SUPPRESSION_MAPPABLE_FIELDS = frozenset({"email"})


class ImportMappingIn(BaseModel):
    columns: dict[str, str] = Field(default_factory=dict)
    source_filename: str | None = Field(default=None, max_length=200)


class ImportUploadOut(BaseModel):
    storage_object_key: str
    storage_object_version: str
    storage_object_digest: str
    headers: list[str]
    sample_rows: list[dict[str, str]]
    detected_total_rows: int
    warnings: list[str]


class ImportCreateIn(BaseModel):
    storage_object_key: str = Field(min_length=1, max_length=1024)
    storage_object_version: str = Field(min_length=1, max_length=1024)
    storage_object_digest: str = Field(min_length=64, max_length=64)
    import_kind: ImportKind
    mapping: ImportMappingIn
    list_id: UUID | None = None


class ImportJobOut(BaseModel):
    id: UUID
    workspace_id: UUID
    initiator_id: UUID
    import_kind: ImportKind
    mapping: dict
    list_id: UUID | None
    status: ImportStatus
    total_rows: int | None
    processed_rows: int
    accepted_rows: int
    duplicate_rows: int
    rejected_rows: int
    failure_summary: str | None
    version: int
    created_at: datetime
    updated_at: datetime


class ImportJobPage(BaseModel):
    items: list[ImportJobOut]
    next_cursor: str | None


class ImportRowResultOut(BaseModel):
    row_number: int
    status: ImportRowStatus
    lead_id: UUID | None
    suppression_id: UUID | None
    validation_reason: str | None


class ImportRowResultPage(BaseModel):
    items: list[ImportRowResultOut]
    next_cursor: str | None
