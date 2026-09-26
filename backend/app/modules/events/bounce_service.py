from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from app.core.metrics import (
    record_bounce_processed,
    record_bounce_recorded,
    record_message_cancelled_due_to_event,
    record_suppression_created,
)
from app.modules.events.repository import EventRepository
from app.modules.leads.normalization import normalize_email

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BounceResult:
    message_id: UUID | None
    evidence: str | None
    bounce_type: str
    suppression_created: bool = False
    enrollments_stopped: int = 0
    messages_cancelled: int = 0


class BounceService:
    """The single owner of "an email bounced".

    Used by the provider webhook processor and by delivery-status notifications
    found during inbound mailbox sync, so both apply identical rules:

    - the bounce is recorded once per message (duplicates and late notifications
      only bump counters, so Bounced counts each email once);
    - only a HARD bounce suppresses the address, stops its active sequences and
      cancels its unsent follow-ups; a SOFT/UNKNOWN bounce is evidence only.

    The caller must already be running under a role allowed to write these
    tables (app_worker_general) inside the workspace's transaction scope.
    """

    def __init__(self, repo: EventRepository) -> None:
        self.repo = repo

    def record(
        self,
        *,
        workspace_id: UUID,
        recipient_email: str | None,
        bounce_type: str,
        source: str,
        source_key: str,
        occurred_at: datetime,
        mailbox_id: UUID | None = None,
        original_message_ids: Sequence[str] = (),
        referenced_message_ids: Sequence[str] = (),
        provider_message_id: str | None = None,
        status_code: str | None = None,
        detail: str | None = None,
        provider_receipt_id: UUID | None = None,
        evidence: Mapping[str, Any] | None = None,
        trust_recipient: bool = True,
    ) -> BounceResult:
        message, message_evidence = self.repo.find_message_for_bounce(
            workspace_id=workspace_id,
            mailbox_id=mailbox_id,
            original_message_ids=original_message_ids,
            referenced_message_ids=referenced_message_ids,
            provider_message_id=provider_message_id,
            recipient_email=recipient_email,
            before=occurred_at,
        )
        message_id = UUID(str(message["id"])) if message else None

        # Association by recipient alone is weaker evidence; say so in the source.
        stored_source = (
            f"{source}_RECIPIENT" if message_evidence == "RECIPIENT_RECENT" else source
        )
        if message_id is not None:
            self.repo.upsert_message_bounce(
                workspace_id=workspace_id,
                message_id=message_id,
                bounce_type=bounce_type,
                bounce_code=status_code,
                source=stored_source,
                detail=detail,
                occurred_at=occurred_at,
            )
            record_bounce_recorded(source, bounce_type)
        else:
            logger.warning(
                "bounce_without_message",
                extra={
                    "workspace_id": str(workspace_id),
                    "source": source,
                    "bounce_type": bounce_type,
                    "has_recipient": bool(recipient_email),
                },
            )

        if bounce_type != "HARD":
            record_bounce_processed(source, bounce_type)
            return BounceResult(message_id, message_evidence, bounce_type)

        # A notification found in a mailbox is unauthenticated text anyone can
        # send: its claimed recipient must not be able to suppress an arbitrary
        # address. Callers with such evidence pass trust_recipient=False, and the
        # address suppressed is then the one WE sent the matched message to.
        address_id = self._resolve_address(
            workspace_id, recipient_email if trust_recipient else None, message
        )
        if address_id is None:
            logger.warning(
                "hard_bounce_not_applied",
                extra={
                    "workspace_id": str(workspace_id),
                    "reason": "no_matched_message",
                },
            )
            return BounceResult(message_id, message_evidence, bounce_type)

        domain_event_id = self.repo.record_domain_event(
            workspace_id=workspace_id,
            event_type="recipient.suppressed",
            aggregate_type="recipient_address",
            aggregate_id=address_id,
            semantic_key=f"hard_bounce:{source_key}",
            occurred_at=occurred_at,
            payload={"reason": "HARD_BOUNCE", "source": source},
        )
        _, suppression_created = self.repo.upsert_suppression(
            workspace_id=workspace_id,
            address_id=address_id,
            reason="HARD_BOUNCE",
            provider_receipt_id=provider_receipt_id,
            domain_event_id=None if provider_receipt_id else domain_event_id,
            source_key=source_key,
            evidence=dict(evidence or {"source": source}),
        )
        stopped = self.repo.stop_active_enrollments(
            workspace_id=workspace_id,
            address_id=address_id,
            stop_reason="HARD_BOUNCE",
        )
        outcome_enrollments = {*stopped}
        # An enrollment that already finished (a single-step campaign, say) is
        # not "active", but its bounce still belongs to it.
        if message is not None and message.get("enrollment_id"):
            outcome_enrollments.add(UUID(str(message["enrollment_id"])))
        for enrollment_id in outcome_enrollments:
            self.repo.record_recipient_outcome(
                workspace_id=workspace_id,
                enrollment_id=enrollment_id,
                kind="HARD_BOUNCE",
                source_key=source_key,
                occurred_at=occurred_at,
            )
        cancelled = self.repo.cancel_non_terminal_messages(
            workspace_id=workspace_id,
            address_id=address_id,
            terminal_reason="hard_bounce",
        )

        record_bounce_processed(source, "HARD")
        if suppression_created:
            record_suppression_created("HARD_BOUNCE")
        if cancelled > 0:
            record_message_cancelled_due_to_event("hard_bounce")
        return BounceResult(
            message_id=message_id,
            evidence=message_evidence,
            bounce_type=bounce_type,
            suppression_created=suppression_created,
            enrollments_stopped=len(stopped),
            messages_cancelled=cancelled,
        )

    def _resolve_address(
        self,
        workspace_id: UUID,
        recipient_email: str | None,
        message: Mapping[str, Any] | None,
    ) -> UUID | None:
        if recipient_email:
            normalized = normalize_email(recipient_email)
            return self.repo.ensure_recipient_address(
                workspace_id=workspace_id,
                canonical_address=normalized.canonical,
                original_display=normalized.original,
            )
        if message is not None and message.get("address_id"):
            return UUID(str(message["address_id"]))
        return None
