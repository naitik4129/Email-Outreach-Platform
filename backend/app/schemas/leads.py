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


class LeadProfileIn(BaseModel):
    """Optional profile fields shared by create and update.

    Field names must match app.modules.leads.fields.PROFILE_FIELDS; formats are
    enforced there, these bounds only reject oversized input early.
    """

    phone: str | None = Field(default=None, max_length=64)
    department: str | None = Field(default=None, max_length=200)
    experience_years: int | None = Field(default=None, ge=0, le=80)
    linkedin_url: str | None = Field(default=None, max_length=500)
    website: str | None = Field(default=None, max_length=500)
    city: str | None = Field(default=None, max_length=200)
    state: str | None = Field(default=None, max_length=200)
    country: str | None = Field(default=None, max_length=200)
    company_website: str | None = Field(default=None, max_length=500)
    company_industry: str | None = Field(default=None, max_length=200)
    company_founded_year: int | None = Field(default=None, ge=1600, le=2100)
    company_linkedin_url: str | None = Field(default=None, max_length=500)


class LeadCreateIn(LeadProfileIn):
    email: str = Field(min_length=3, max_length=320)
    first_name: str | None = Field(default=None, max_length=200)
    last_name: str | None = Field(default=None, max_length=200)
    company: str | None = Field(default=None, max_length=200)
    title: str | None = Field(default=None, max_length=200)
    custom_fields: dict[str, Any] = Field(default_factory=dict)
    list_id: UUID | None = None


class LeadUpdateIn(LeadProfileIn):
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
    phone: str | None
    department: str | None
    experience_years: int | None
    linkedin_url: str | None
    website: str | None
    city: str | None
    state: str | None
    country: str | None
    company_website: str | None
    company_industry: str | None
    company_founded_year: int | None
    company_linkedin_url: str | None
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


LeadActivityKind = Literal[
    "EMAIL_SENT",
    "EMAIL_OPENED",
    "EMAIL_BOUNCED",
    "REPLY_RECEIVED",
    "AUTO_REPLY_RECEIVED",
]


class LeadActivityItem(BaseModel):
    """One event in a lead's email history. Reply content is a short preview;
    the full thread stays in the inbox (conversation_id)."""

    kind: LeadActivityKind
    occurred_at: datetime
    message_id: UUID
    subject: str | None = None
    campaign_id: UUID | None = None
    campaign_name: str | None = None
    sequence_step_position: int | None = None
    conversation_id: UUID | None = None
    sender_email: str | None = None
    body_preview: str | None = None
    occurrence_count: int | None = None
    bounce_type: str | None = None


class LeadActivityOut(BaseModel):
    lead_id: UUID
    items: list[LeadActivityItem]


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
