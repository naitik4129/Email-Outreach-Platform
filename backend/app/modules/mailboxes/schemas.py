from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class MailboxListItem(BaseModel):
    id: UUID
    provider: str
    email_address: str
    sender_display_name: str | None = None
    connection_state: str
    health_state: str
    policy_state: str
    policy_reason: str | None = None
    circuit_state: str
    sync_state: str
    created_at: datetime
    updated_at: datetime


class MailboxDetail(MailboxListItem):
    signature_html: str | None = None
    blocked_until: datetime | None = None
    pending_safety_count: int = 0
    current_connection_generation: int = 1
    config_version: int = 1
    version: int = 1


class MailboxUpdate(BaseModel):
    sender_display_name: str | None = Field(default=None, max_length=200)
    signature_html: str | None = Field(default=None, max_length=20000)


class GmailConnectStartRequest(BaseModel):
    return_path: str = Field(default="/app/mailboxes", max_length=1024)


class GmailConnectStartResponse(BaseModel):
    authorization_url: str
    expires_at: datetime


class GmailConnectCompleteRequest(BaseModel):
    code: str = Field(min_length=1)
    state: str = Field(min_length=1)


class GmailConnectCompleteResponse(BaseModel):
    mailbox_id: UUID
    provider: str
    email_address: str
    connection_state: str
    health_state: str


class MailboxTestSendRequest(BaseModel):
    recipient_email: str | None = Field(default=None, max_length=320)


class MailboxTestSendResult(BaseModel):
    message_id: UUID
    status: str
    recipient_email: str
    provider_message_id: str | None = None
    accepted_at: datetime | None = None
    error_message: str | None = None


class DisconnectResponse(BaseModel):
    mailbox_id: UUID
    connection_state: str
