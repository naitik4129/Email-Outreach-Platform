from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.modules.replies.dsn import DeliveryReport


class ReplyEvidenceType(StrEnum):
    RFC_HEADER_MATCH = "RFC_HEADER_MATCH"
    PROVIDER_THREAD_CORROBORATED = "PROVIDER_THREAD_CORROBORATED"
    AMBIGUOUS_CANDIDATE = "AMBIGUOUS_CANDIDATE"


class ReplyConfidence(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class InboundClassification(StrEnum):
    HUMAN_REPLY = "HUMAN_REPLY"
    OUT_OF_OFFICE = "OUT_OF_OFFICE"
    AUTOMATED = "AUTOMATED"
    # A delivery-status notification: bounce evidence, never a reply.
    BOUNCE = "BOUNCE"
    UNRESOLVED = "UNRESOLVED"


@dataclass(frozen=True)
class NormalizedInboundMessage:
    """Internal, sanitized, provider-independent inbound message model."""

    provider: str
    provider_message_id: str
    provider_thread_id: str | None
    rfc_message_id: str | None
    in_reply_to: str | None
    references: list[str]
    from_address: str
    from_name: str | None
    to_addresses: list[str]
    cc_addresses: list[str]
    bcc_addresses: list[str]
    subject: str
    content_text: str | None
    content_html: str | None
    received_at: datetime
    headers: dict[str, str] = field(default_factory=dict)
    is_automated: bool = False
    classification: str = InboundClassification.HUMAN_REPLY.value
    delivery_report: DeliveryReport | None = None


class SyncCheckpoint(BaseModel):
    """Durable state saved in mailbox_sync_states.cursor_data."""

    provider: str
    confirmed_cursor: str | None = None
    pending_page_token: str | None = None
    last_sync_at: str | None = None
    resync_count: int = 0
    extra: dict[str, Any] = Field(default_factory=dict)


@dataclass(frozen=True)
class ReplyMatchCandidate:
    outbound_message_id: UUID
    campaign_id: UUID
    enrollment_id: UUID
    evidence_type: str
    confidence: str


@dataclass(frozen=True)
class ReplyMatchResult:
    is_matched: bool
    association_status: Literal["MATCHED", "UNRESOLVED"]
    matched_candidate: ReplyMatchCandidate | None = None
    candidates: list[ReplyMatchCandidate] = field(default_factory=list)
    classification: str = InboundClassification.HUMAN_REPLY.value


@dataclass(frozen=True)
class MailboxSyncResult:
    mailbox_id: UUID
    workspace_id: UUID
    status: str
    messages_discovered: int = 0
    messages_persisted: int = 0
    messages_deduplicated: int = 0
    replies_matched: int = 0
    replies_unresolved: int = 0
    enrollments_stopped: int = 0
    future_messages_cancelled: int = 0
    resync_required: bool = False
    error: str | None = None
