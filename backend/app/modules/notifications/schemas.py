from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class NotificationOut(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: UUID
    workspace_id: UUID
    recipient_user_id: UUID
    type: str
    summary: str | None = None
    resource_link: str | None = None
    read_at: datetime | None = None
    created_at: datetime


class NotificationListOut(BaseModel):
    model_config = ConfigDict(extra="ignore")

    items: list[NotificationOut]
    total: int
    unread_count: int


class UnreadCountOut(BaseModel):
    model_config = ConfigDict(extra="ignore")

    unread_count: int


class NotificationPreferencesIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email_enabled: bool = True
    in_app_enabled: bool = True
    categories: dict[str, bool] = Field(
        default_factory=lambda: {
            "mailbox_health": True,
            "campaign_status": True,
            "replies": True,
            "deliverability": True,
            "team": True,
        }
    )


class NotificationPreferencesOut(BaseModel):
    model_config = ConfigDict(extra="ignore")

    email_enabled: bool = True
    in_app_enabled: bool = True
    categories: dict[str, bool] = Field(
        default_factory=lambda: {
            "mailbox_health": True,
            "campaign_status": True,
            "replies": True,
            "deliverability": True,
            "team": True,
        }
    )
