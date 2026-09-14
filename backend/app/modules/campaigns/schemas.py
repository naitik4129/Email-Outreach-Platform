from __future__ import annotations

from datetime import datetime, time
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator

# Weekday bit order (documented here since no migration comment defines it):
# bit 0 = Monday ... bit 6 = Sunday (ISO week order). A `weekdays` list of
# ISO weekday integers (1=Monday .. 7=Sunday) is the wire format; the service
# layer converts to/from the `weekday_set` integer bitmask stored in
# campaign_settings_versions.weekday_set.

# ---------------------------------------------------------------------------
# Campaign
# ---------------------------------------------------------------------------


class CampaignCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)


class CampaignUpdateIn(BaseModel):
    expected_version: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)


class CampaignArchiveIn(BaseModel):
    expected_version: int = Field(ge=1)


class CampaignDuplicateIn(BaseModel):
    name: str | None = Field(default=None, max_length=200)


class CampaignListItem(BaseModel):
    id: UUID
    workspace_id: UUID
    name: str
    description: str | None = None
    status: str
    draft_sequence_id: UUID | None = None
    draft_audience_id: UUID | None = None
    current_settings_id: UUID | None = None
    version: int
    created_at: datetime
    updated_at: datetime


class CampaignPage(BaseModel):
    items: list[CampaignListItem]
    next_cursor: str | None = None


class CampaignDetailOut(BaseModel):
    id: UUID
    workspace_id: UUID
    name: str
    description: str | None = None
    creator_id: UUID
    status: str
    start_at: datetime | None = None
    draft_sequence_id: UUID | None = None
    draft_audience_id: UUID | None = None
    current_settings_id: UUID | None = None
    planning_status: str
    archived_at: datetime | None = None
    error_reason: str | None = None
    version: int
    created_at: datetime
    updated_at: datetime


# ---------------------------------------------------------------------------
# Sequence + steps
# ---------------------------------------------------------------------------


class SequenceStepCreateIn(BaseModel):
    kind: Literal["EMAIL", "WAIT"]
    position: int = Field(ge=1)
    # EMAIL payload
    email_subject: str | None = Field(default=None, min_length=1, max_length=500)
    email_body_html: str | None = Field(default=None, max_length=200_000)
    source_template_version_id: UUID | None = None
    # WAIT payload
    wait_duration_minutes: int | None = Field(default=None, gt=0)


class SequenceStepUpdateIn(BaseModel):
    expected_version: int = Field(ge=1)
    email_subject: str | None = Field(default=None, min_length=1, max_length=500)
    email_body_html: str | None = Field(default=None, max_length=200_000)
    source_template_version_id: UUID | None = None
    wait_duration_minutes: int | None = Field(default=None, gt=0)


class SequenceStepReorderEntry(BaseModel):
    step_id: UUID
    position: int = Field(ge=1)


class SequenceStepsReorderIn(BaseModel):
    steps: list[SequenceStepReorderEntry] = Field(min_length=1)


class SequenceStepOut(BaseModel):
    id: UUID
    sequence_id: UUID
    campaign_id: UUID
    position: int
    kind: str
    email_subject: str | None = None
    email_body_html: str | None = None
    email_variable_schema: dict[str, Any] | None = None
    wait_duration_minutes: int | None = None
    source_template_version_id: UUID | None = None
    version: int
    created_at: datetime
    updated_at: datetime


class SequenceOut(BaseModel):
    id: UUID | None = None
    campaign_id: UUID
    revision: int
    status: str
    steps: list[SequenceStepOut]


# ---------------------------------------------------------------------------
# Mailbox assignment
# ---------------------------------------------------------------------------


class CampaignMailboxAssignIn(BaseModel):
    mailbox_id: UUID


class CampaignMailboxesReorderIn(BaseModel):
    mailbox_ids: list[UUID] = Field(min_length=1)


class CampaignMailboxOut(BaseModel):
    mailbox_id: UUID
    provider: str
    email_address: str
    sender_display_name: str | None = None
    connection_state: str
    health_state: str
    policy_state: str
    policy_reason: str | None = None
    active: bool
    allocation_position: int


# ---------------------------------------------------------------------------
# Schedule / settings
# ---------------------------------------------------------------------------


class CampaignSettingsCreateIn(BaseModel):
    timezone: str = Field(min_length=1, max_length=100)
    weekdays: list[int] = Field(min_length=1, max_length=7)
    window_start_local: time
    window_end_local: time
    daily_limit: int | None = Field(default=None, gt=0, le=100_000)

    @field_validator("weekdays")
    @classmethod
    def weekdays_must_be_iso(cls, value: list[int]) -> list[int]:
        if any(day < 1 or day > 7 for day in value):
            raise ValueError("weekdays must be ISO weekday integers 1 (Mon) - 7 (Sun)")
        if len(set(value)) != len(value):
            raise ValueError("weekdays must not contain duplicates")
        return sorted(set(value))

    @model_validator(mode="after")
    def window_start_before_end(self) -> CampaignSettingsCreateIn:
        if self.window_start_local >= self.window_end_local:
            raise ValueError(
                "window_start_local must be before window_end_local "
                "(same-day windows only; overnight windows are not supported in MVP)"
            )
        return self


class CampaignSettingsOut(BaseModel):
    id: UUID
    campaign_id: UUID
    revision: int
    timezone: str
    weekdays: list[int]
    window_start_local: time
    window_end_local: time
    daily_limit: int | None = None
    created_at: datetime


# ---------------------------------------------------------------------------
# Audience
# ---------------------------------------------------------------------------


class AudienceSelectIn(BaseModel):
    list_ids: list[UUID] = Field(default_factory=list)
    lead_ids: list[UUID] = Field(default_factory=list)


class AudienceOut(BaseModel):
    id: UUID
    campaign_id: UUID
    revision: int
    status: str
    started_at: datetime
    completed_at: datetime | None = None
    is_committed: bool = False
    total_candidates: int | None = None
    processed_count: int = 0
    accepted_count: int | None = None
    excluded_count: int | None = None
    error_reason: str | None = None


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------


class PreflightIssue(BaseModel):
    code: str
    message: str
    field_path: str | None = None


class PreflightResult(BaseModel):
    ready: bool
    errors: list[PreflightIssue]
    warnings: list[PreflightIssue]


# ---------------------------------------------------------------------------
# Review
# ---------------------------------------------------------------------------


class CampaignReviewOut(BaseModel):
    campaign: CampaignDetailOut
    sequence: SequenceOut | None = None
    mailboxes: list[CampaignMailboxOut]
    settings: CampaignSettingsOut | None = None
    audience: AudienceOut | None = None
    preflight: PreflightResult
