from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.modules.mailboxes.providers.ssrf import ALLOWED_PORTS, IMAP_ALLOWED_PORTS


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


class SmtpSecurityMode(StrEnum):
    STARTTLS = "STARTTLS"
    IMPLICIT_TLS = "IMPLICIT_TLS"


class SmtpConfigView(BaseModel):
    """Safe, non-secret SMTP configuration for display in mailbox detail.

    Never includes password or any ciphertext -- populated only from
    mailbox_connections.protected_config.
    """

    host: str
    port: int
    security_mode: SmtpSecurityMode
    username: str
    # Reply sync reads the mailbox over IMAP; all None when not configured.
    imap_host: str | None = None
    imap_port: int | None = None
    imap_security_mode: SmtpSecurityMode | None = None
    imap_username: str | None = None


class MailboxDetail(MailboxListItem):
    signature_html: str | None = None
    blocked_until: datetime | None = None
    pending_safety_count: int = 0
    current_connection_generation: int = 1
    config_version: int = 1
    version: int = 1
    smtp_config: SmtpConfigView | None = None
    # ENABLED | RECONNECT_REQUIRED | IMAP_NOT_CONFIGURED | UNSUPPORTED
    reply_sync_status: str = "UNSUPPORTED"


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


# -----------------------------------------------------------------------
# Microsoft OAuth
# -----------------------------------------------------------------------


class MicrosoftConnectStartRequest(BaseModel):
    return_path: str = Field(default="/app/mailboxes", max_length=1024)


class MicrosoftConnectStartResponse(BaseModel):
    authorization_url: str
    expires_at: datetime


class MicrosoftConnectCompleteRequest(BaseModel):
    code: str = Field(min_length=1)
    state: str = Field(min_length=1)


class MicrosoftConnectCompleteResponse(BaseModel):
    mailbox_id: UUID
    provider: str
    email_address: str
    connection_state: str
    health_state: str


# -----------------------------------------------------------------------
# Custom SMTP
# -----------------------------------------------------------------------


class SmtpConnectRequest(BaseModel):
    host: str = Field(min_length=1, max_length=255)
    port: int = Field(ge=1, le=65535)
    security_mode: SmtpSecurityMode
    username: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=500)
    email_address: str = Field(min_length=3, max_length=320)
    sender_display_name: str | None = Field(default=None, max_length=200)

    # Optional IMAP settings enabling reply sync for this SMTP mailbox. The
    # IMAP login defaults to the SMTP username/password when omitted.
    imap_host: str | None = Field(default=None, min_length=1, max_length=255)
    imap_port: int | None = Field(default=None, ge=1, le=65535)
    imap_security_mode: SmtpSecurityMode | None = None
    imap_username: str | None = Field(default=None, min_length=1, max_length=320)
    imap_password: str | None = Field(default=None, min_length=1, max_length=500)

    @field_validator("imap_port")
    @classmethod
    def imap_port_must_be_allowed(cls, value: int | None) -> int | None:
        if value is not None and value not in IMAP_ALLOWED_PORTS:
            raise ValueError(
                f"IMAP port must be one of {sorted(IMAP_ALLOWED_PORTS)}"
            )
        return value

    @field_validator("port")
    @classmethod
    def port_must_be_allowed(cls, value: int) -> int:
        if value not in ALLOWED_PORTS:
            raise ValueError(
                f"SMTP port must be one of {sorted(ALLOWED_PORTS)}"
            )
        return value

    @field_validator("email_address")
    @classmethod
    def email_must_look_valid(cls, value: str) -> str:
        if "@" not in value or any(c in value for c in ("\r", "\n")):
            raise ValueError("Invalid email address")
        return value


class SmtpConnectResponse(BaseModel):
    mailbox_id: UUID
    provider: str
    email_address: str
    connection_state: str
    health_state: str


class SmtpUpdateRequest(BaseModel):
    host: str | None = Field(default=None, min_length=1, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    security_mode: SmtpSecurityMode | None = None
    username: str | None = Field(default=None, min_length=1, max_length=320)
    # Omitted (None) means "keep the existing credential" -- never a literal
    # placeholder string. An explicit empty string is rejected by min_length.
    password: str | None = Field(default=None, min_length=1, max_length=500)
    sender_display_name: str | None = Field(default=None, max_length=200)

    # Optional IMAP settings enabling reply sync for this SMTP mailbox. The
    # IMAP login defaults to the SMTP username/password when omitted.
    imap_host: str | None = Field(default=None, min_length=1, max_length=255)
    imap_port: int | None = Field(default=None, ge=1, le=65535)
    imap_security_mode: SmtpSecurityMode | None = None
    imap_username: str | None = Field(default=None, min_length=1, max_length=320)
    imap_password: str | None = Field(default=None, min_length=1, max_length=500)

    @field_validator("imap_port")
    @classmethod
    def imap_port_must_be_allowed(cls, value: int | None) -> int | None:
        if value is not None and value not in IMAP_ALLOWED_PORTS:
            raise ValueError(
                f"IMAP port must be one of {sorted(IMAP_ALLOWED_PORTS)}"
            )
        return value

    @field_validator("port")
    @classmethod
    def port_must_be_allowed(cls, value: int | None) -> int | None:
        if value is not None and value not in ALLOWED_PORTS:
            raise ValueError(
                f"SMTP port must be one of {sorted(ALLOWED_PORTS)}"
            )
        return value
