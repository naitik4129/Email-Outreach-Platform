from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SendTaskPayload(BaseModel):
    """The strict minimal queue payload for email.send.

    Carries stable IDs only. Never carries email body, lead PII,
    credentials, tokens, or passwords. Future Phase 10 send worker
    reloads authoritative state from PostgreSQL.
    """

    model_config = ConfigDict(extra="forbid")

    schema_version: int = Field(default=1, gt=0)
    work_id: str
    delivery_id: str
    message_id: str
    dispatch_id: str
    resource_id: str
    workspace_id: str
    dispatch_generation: int = Field(gt=0)
    correlation_id: str | None = None

    @model_validator(mode="before")
    @classmethod
    def check_forbidden_fields(cls, data: Any) -> Any:
        if isinstance(data, dict):
            forbidden = {
                "body",
                "body_html",
                "body_text",
                "content",
                "subject",
                "token",
                "access_token",
                "refresh_token",
                "password",
                "credential",
                "lead",
                "campaign",
                "mailbox",
                "recipient_email",
            }
            found = forbidden.intersection(k.lower() for k in data.keys())
            if found:
                raise ValueError(
                    f"Send task payload contains forbidden sensitive fields: {found}"
                )
        return data


class DueMessageCandidate(BaseModel):
    id: UUID
    workspace_id: UUID
    campaign_id: UUID | None = None
    mailbox_id: UUID
    due_at: datetime
    purpose: str
    schedule_generation: int
    status: str
    dispatch_generation: int


class ClaimResult(BaseModel):
    message_id: UUID
    workspace_id: UUID
    dispatch_generation: int
    claimed: bool
    reason: str | None = None


class SchedulerIterationSummary(BaseModel):
    due_discovered: int = 0
    messages_claimed: int = 0
    deliveries_published: int = 0
    claims_recovered: int = 0
    leases_recovered: int = 0
    duration_ms: float = 0.0
    oldest_due_lag_seconds: float | None = None
