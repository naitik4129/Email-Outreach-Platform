from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

VALID_INVITATION_ROLES = frozenset({"ADMIN", "MANAGER", "MEMBER", "VIEWER"})


class InvitationCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(..., min_length=3, max_length=320)
    role_code: str = Field(...)

    @field_validator("email")
    @classmethod
    def validate_email(cls, v: str) -> str:
        clean = v.strip().lower()
        if "@" not in clean or len(clean) < 3 or len(clean) > 320:
            raise ValueError("A valid email address is required")
        return clean

    @field_validator("role_code")
    @classmethod
    def validate_role(cls, v: str) -> str:
        clean = v.strip().upper()
        if clean not in VALID_INVITATION_ROLES:
            roles = ", ".join(sorted(VALID_INVITATION_ROLES))
            raise ValueError(
                f"Role must be one of: {roles}. OWNER cannot be granted via invitation."
            )
        return clean


class InvitationOut(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: UUID
    workspace_id: UUID
    inviter_id: UUID
    invited_email: str
    role_code: str
    status: str
    expires_at: datetime
    created_at: datetime
    invite_url: str | None = None


class InvitationVerifyOut(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: UUID
    workspace_id: UUID
    workspace_name: str
    invited_email: str
    role_code: str
    expires_at: datetime


class InvitationAcceptIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(..., min_length=16, max_length=256)


class InvitationAcceptOut(BaseModel):
    model_config = ConfigDict(extra="ignore")

    workspace_id: UUID
    membership_id: UUID
    role_code: str
    workspace_name: str


class MemberRoleUpdateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role_code: str = Field(...)
    expected_version: int = Field(..., gt=0)

    @field_validator("role_code")
    @classmethod
    def validate_role(cls, v: str) -> str:
        clean = v.strip().upper()
        if clean not in VALID_INVITATION_ROLES:
            roles = ", ".join(sorted(VALID_INVITATION_ROLES))
            raise ValueError(
                f"Role must be one of: {roles}. OWNER role requires ownership transfer."
            )
        return clean


class MemberRemoveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_version: int = Field(..., gt=0)


class TransferOwnershipIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_membership_id: UUID
    expected_owner_version: int = Field(..., gt=0)
    expected_target_version: int = Field(..., gt=0)


class TransferOwnershipOut(BaseModel):
    model_config = ConfigDict(extra="ignore")

    workspace_id: UUID
    previous_owner_id: UUID
    new_owner_id: UUID
