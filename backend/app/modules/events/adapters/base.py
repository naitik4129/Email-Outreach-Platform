from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from app.modules.events.schemas import (
    BounceClassification,
    InboundEventType,
    NormalizedEvent,
    ScopeKind,
)


@dataclass(frozen=True)
class VerifiedEventEvidence:
    """Bounded, authenticated evidence produced by a provider event adapter.

    Contains only facts proven by the adapter's verification of signatures,
    tokens, or headers, without tenant or workspace authority claims.
    """

    is_valid: bool
    provider: str
    source_schema: str
    event_identity: str
    provider_account_id: str | None = None
    mailbox_email: str | None = None
    event_type: InboundEventType = InboundEventType.NOTIFICATION
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    recipient_email: str | None = None
    sender_email: str | None = None
    provider_message_id: str | None = None
    provider_thread_id: str | None = None
    bounce_classification: BounceClassification | None = None
    reason: str | None = None
    raw_data: dict[str, Any] = field(default_factory=dict)
    error_message: str | None = None


class ProviderEventAdapter(Protocol):
    """Protocol for provider-specific event adapters.

    Each provider implementation isolates provider payload formats, signature
    verification algorithms, and identifier extraction behind this interface.
    """

    @property
    def provider_name(self) -> str: ...

    def verify_and_parse(
        self,
        *,
        headers: Mapping[str, str],
        body_bytes: bytes,
        query_params: Mapping[str, str],
        secret: str | None = None,
    ) -> VerifiedEventEvidence: ...
