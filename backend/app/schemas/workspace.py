from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class WorkspaceListItem(BaseModel):
    workspace_id: UUID
    workspace_name: str
    workspace_status: str
    membership_id: UUID
    role_code: str
    membership_version: int


class WorkspaceOut(BaseModel):
    id: UUID
    name: str
    status: str
    defaults: dict[str, object]
    version: int
    role_code: str


class WorkspaceCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)


class WorkspaceCreateOut(BaseModel):
    id: UUID
    name: str
    status: str
    role_code: str
    membership_id: UUID
    membership_version: int


class WorkspaceUpdateIn(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    defaults: dict[str, object] | None = None
    expected_version: int = Field(ge=1)


class MembershipOut(BaseModel):
    membership_id: UUID
    user_id: UUID
    display_name: str | None
    role_code: str
    status: str
    version: int
    joined_at: datetime
    revoked_at: datetime | None
