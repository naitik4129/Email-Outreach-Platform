from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class InboundEventType(StrEnum):
    BOUNCE = "BOUNCE"
    COMPLAINT = "COMPLAINT"
    UNSUBSCRIBE = "UNSUBSCRIBE"
    NOTIFICATION = "NOTIFICATION"


class BounceClassification(StrEnum):
    HARD = "HARD"
    SOFT = "SOFT"
    UNKNOWN = "UNKNOWN"


class ReceiptStatus(StrEnum):
    RECEIVED = "RECEIVED"
    PROCESSING = "PROCESSING"
    PROCESSED = "PROCESSED"
    RETRY = "RETRY"
    FAILED = "FAILED"


class ScopeKind(StrEnum):
    MAILBOX = "MAILBOX"
    WORKSPACE = "WORKSPACE"


@dataclass(frozen=True)
class NormalizedEvent:
    """Canonical domain representation of an inbound event.

    Provider-specific payload schemas are mapped to this structure by
    provider adapters. Business services consume only this normalized
    representation rather than raw provider payloads.
    """

    event_id: UUID
    provider: str
    provider_event_id: str
    scope_kind: ScopeKind
    workspace_id: UUID
    mailbox_id: UUID | None
    event_type: InboundEventType
    occurred_at: datetime
    received_at: datetime
    recipient_email: str | None = None
    sender_email: str | None = None
    provider_message_id: str | None = None
    provider_thread_id: str | None = None
    bounce_classification: BounceClassification | None = None
    reason: str | None = None
    raw_payload_ref: str | None = None
    raw_payload_digest: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class EventProcessResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    receipt_id: UUID
    status: str
    event_type: str
    suppression_created: bool = False
    enrollments_stopped: int = 0
    messages_cancelled: int = 0
    reason: str | None = None


class ProviderReceiptOut(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    workspace_id: UUID
    mailbox_id: UUID | None
    provider: str
    scope_kind: str
    event_identity: str
    source_schema: str
    receipt_status: str
    retry_count: int
    received_at: datetime
    created_at: datetime
