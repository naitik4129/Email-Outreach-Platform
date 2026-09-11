from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

LeadStatus = Literal["ACTIVE", "ARCHIVED"]
LeadValidationStatus = Literal[
    "UNKNOWN",
    "VALID",
    "INVALID",
    "RISKY",
    "CATCH_ALL",
    "DISPOSABLE",
]


class LeadCreateIn(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    first_name: str | None = Field(default=None, max_length=200)
    last_name: str | None = Field(default=None, max_length=200)
    company: str | None = Field(default=None, max_length=200)
    title: str | None = Field(default=None, max_length=200)
    custom_fields: dict[str, Any] = Field(default_factory=dict)
    list_id: UUID | None = None


class LeadUpdateIn(BaseModel):
    email: str | None = Field(default=None, min_length=3, max_length=320)
    first_name: str | None = Field(default=None, max_length=200)
    last_name: str | None = Field(default=None, max_length=200)
    company: str | None = Field(default=None, max_length=200)
    title: str | None = Field(default=None, max_length=200)
    custom_fields: dict[str, Any] | None = None
    validation_status: LeadValidationStatus | None = None
    expected_version: int = Field(ge=1)


class ExpectedVersionIn(BaseModel):
    expected_version: int = Field(ge=1)


class LeadOut(BaseModel):
    id: UUID
    workspace_id: UUID
    email: str
    canonical_address: str
    normalization_version: int
    first_name: str | None
    last_name: str | None
    company: str | None
    title: str | None
    custom_fields: dict[str, Any]
    status: LeadStatus
    validation_status: LeadValidationStatus
    validated_at: datetime | None
    contact_revision: int
    archived_at: datetime | None
    version: int
    created_at: datetime
    updated_at: datetime


class LeadListSummary(BaseModel):
    id: UUID
    name: str
    archived_at: datetime | None


class LeadDetailOut(LeadOut):
    lists: list[LeadListSummary]


class LeadListItem(LeadOut):
    list_count: int


class LeadPage(BaseModel):
    items: list[LeadListItem]
    next_cursor: str | None


class LeadListCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class LeadListUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    expected_version: int = Field(ge=1)


class LeadListOut(BaseModel):
    id: UUID
    workspace_id: UUID
    name: str
    archived_at: datetime | None
    membership_revision: int
    member_count: int
    version: int
    created_at: datetime
    updated_at: datetime


class LeadListPage(BaseModel):
    items: list[LeadListOut]
    next_cursor: str | None


class LeadListMemberCreateIn(BaseModel):
    lead_id: UUID


class LeadListMemberOut(BaseModel):
    lead: LeadOut
    added_by: UUID | None
    added_at: datetime


class LeadListMemberPage(BaseModel):
    items: list[LeadListMemberOut]
    next_cursor: str | None
