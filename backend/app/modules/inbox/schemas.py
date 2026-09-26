from __future__ import annotations

from datetime import datetime
from enum import Enum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ConversationFilter(str, Enum):
    ALL = "ALL"
    UNREAD = "UNREAD"
    READ = "READ"
    REPLIED = "REPLIED"
    ARCHIVED = "ARCHIVED"


class MessageDirection(str, Enum):
    INBOUND = "INBOUND"
    OUTBOUND = "OUTBOUND"


class ConversationParticipant(BaseModel):
    model_config = ConfigDict(frozen=True)

    email: str
    name: str | None = None


class ConversationListItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    mailbox_id: UUID
    mailbox_address: str
    mailbox_provider: str
    campaign_id: UUID | None = None
    campaign_name: str | None = None
    subject: str
    snippet: str
    latest_activity_at: datetime
    is_read: bool
    read_at: datetime | None = None
    archived_at: datetime | None = None
    participant_email: str
    participant_name: str | None = None
    reply_status: str = "NONE"
    message_count: int = 1


class ConversationPage(BaseModel):
    items: list[ConversationListItem]
    next_cursor: str | None = None
    has_more: bool = False
    unread_count: int = 0


class MessageThreadItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    direction: MessageDirection
    sender_email: str
    sender_name: str | None = None
    recipient_email: str
    recipient_name: str | None = None
    subject: str
    content_text: str | None = None
    content_html: str | None = None
    timestamp: datetime
    status: str | None = None
    sequence_step_id: UUID | None = None
    # 1-based position of the sequence step this message belongs to (for a
    # reply: the step of the email that was replied to).
    sequence_step_position: int | None = None
    association_status: str | None = None
    classification: str | None = None


class ConversationDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    workspace_id: UUID
    mailbox_id: UUID
    mailbox_address: str
    mailbox_provider: str
    campaign_id: UUID | None = None
    campaign_name: str | None = None
    subject: str
    latest_activity_at: datetime
    created_at: datetime
    is_read: bool
    read_at: datetime | None = None
    archived_at: datetime | None = None
    reply_status: str = "NONE"
    participant_email: str
    participant_name: str | None = None
    lead_id: UUID | None = None
    lead_company: str | None = None
    messages: list[MessageThreadItem] = Field(default_factory=list)


class ConversationActionResponse(BaseModel):
    id: UUID
    is_read: bool
    read_at: datetime | None = None
    archived_at: datetime | None = None
    updated_at: datetime | None = None


class MailboxSyncStatusItem(BaseModel):
    mailbox_id: UUID
    email_address: str
    provider: str
    connection_status: str
    sync_scope: str = "INBOX"
    sync_status: str = "UNKNOWN"
    last_complete_at: datetime | None = None
    failure_count: int = 0


class InboxSyncStatusResponse(BaseModel):
    mailboxes: list[MailboxSyncStatusItem]
