"""HTTP contracts of the personalization API (ADR-0011)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.modules.personalization.config_schema import PersonalizationConfig

ApprovalStatus = Literal["NONE", "APPROVED", "STALE"]


class ApprovalOut(BaseModel):
    status: ApprovalStatus
    approved_at: datetime | None = None
    approved_by: UUID | None = None


class PersonalizationCapabilitiesOut(BaseModel):
    enabled: bool
    model: str | None = None


class PersonalizationStateOut(BaseModel):
    campaign_id: UUID
    campaign_type: str
    enabled: bool
    model: str | None = None
    config: PersonalizationConfig | None = None
    # The draft sequence's version, sent back as `expected_version` when saving.
    config_version: int | None = None
    # What a sample approval is bound to right now (objective + reference
    # templates + prompt version + model).
    config_digest: str
    approval: ApprovalOut


class PersonalizationConfigIn(BaseModel):
    config: PersonalizationConfig
    expected_version: int | None = Field(default=None, ge=1)


class PreviewCreateIn(BaseModel):
    # Client-generated: makes a retried request return the same batch instead of
    # spending more of the daily preview budget.
    batch_id: UUID
    audience_member_ids: list[UUID] = Field(default_factory=list, max_length=5)


class PreviewRecipientOut(BaseModel):
    audience_member_id: UUID
    first_name: str | None = None
    last_name: str | None = None
    company: str | None = None
    title: str | None = None


class PreviewItemOut(BaseModel):
    id: UUID
    recipient: PreviewRecipientOut
    step_id: UUID
    step_position: int
    state: Literal["PENDING", "OK", "FAILED"]
    subject: str | None = None
    body_html: str | None = None
    facts: list[dict[str, Any]] = Field(default_factory=list)
    research_summary: dict[str, Any] | None = None
    fallback_used: bool = False
    failure_codes: list[str] = Field(default_factory=list)


class PreviewBatchOut(BaseModel):
    batch_id: UUID
    config_digest: str
    current_digest: str
    # True when the objective/templates changed after this batch was generated.
    stale: bool
    created_at: datetime
    expires_at: datetime
    complete: bool
    all_ok: bool
    items: list[PreviewItemOut]


class ApproveIn(BaseModel):
    batch_id: UUID
    config_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


class BudgetOut(BaseModel):
    generation_used: int
    generation_cap: int
    preview_used: int
    preview_cap: int
    fetch_used: int
    fetch_cap: int


class GenerationProgressOut(BaseModel):
    campaign_id: UUID
    pending: int
    succeeded: int
    failed: int
    superseded: int
    fallback: int
    oldest_pending_at: datetime | None = None
    failure_codes: dict[str, int] = Field(default_factory=dict)
    budget: BudgetOut
