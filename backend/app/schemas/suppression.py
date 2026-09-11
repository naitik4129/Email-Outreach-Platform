from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

SuppressionReason = Literal["MANUAL", "UNSUBSCRIBE", "HARD_BOUNCE", "COMPLAINT"]
SuppressionStatus = Literal["ACTIVE", "RELEASED"]


class SuppressionCreateIn(BaseModel):
    email: str = Field(min_length=3, max_length=320)


class SuppressionReleaseIn(BaseModel):
    reason: str = Field(min_length=1, max_length=500)


class SuppressionOut(BaseModel):
    id: UUID
    workspace_id: UUID
    email: str
    canonical_address: str
    reason: SuppressionReason
    status: SuppressionStatus
    first_observed_at: datetime
    last_observed_at: datetime
    released_at: datetime | None
    release_actor_id: UUID | None
    removable: bool
    version: int
    created_at: datetime
    updated_at: datetime


class SuppressionPage(BaseModel):
    items: list[SuppressionOut]
    next_cursor: str | None
