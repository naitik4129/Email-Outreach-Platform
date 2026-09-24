from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class AdminWorkspaceOut(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: UUID
    name: str
    status: str
    sending_restriction_reason: str | None = None
    pending_safety_count: int = 0
    member_count: int = 0
    mailbox_count: int = 0
    created_at: datetime


class RestrictWorkspaceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(..., min_length=3, max_length=1000)


class PlatformAuditLogOut(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: UUID
    actor_kind: str
    actor_id: UUID | None = None
    action: str
    target_type: str
    target_id: UUID | None = None
    before_state: dict[str, Any] | None = None
    after_state: dict[str, Any] | None = None
    reason: str | None = None
    recorded_at: datetime
