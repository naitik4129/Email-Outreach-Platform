from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.metrics import (
    record_bounce_processed,
    record_complaint_processed,
    record_event_failed,
    record_event_processed,
    record_message_cancelled_due_to_event,
    record_suppression_created,
    record_unsubscribe_processed,
)
from app.modules.events.repository import EventRepository
from app.modules.events.schemas import (
    BounceClassification,
    EventProcessResult,
    InboundEventType,
    ReceiptStatus,
)
from app.modules.leads.normalization import normalize_email

logger = logging.getLogger(__name__)


class InboundEventProcessor:
    """Authoritative event processor executing domain side effects.

    Guarantees:
    1. Deduplication & Idempotency: Duplicate processing produces exactly one logical side effect.
    2. Tenant Isolation: All mutations are workspace-scoped.
    3. Monotonic Safety: Bounces, complaints, and unsubscribes permanently suppress and cancel future sends.
    4. Safety Hold Resolution: Pending safety counters are decremented atomically with effect commit.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = EventRepository(session)

    def process_receipt(
        self,
        receipt_id: UUID,
        *,
        worker_owner: str = "worker-general",
        now: datetime | None = None,
    ) -> EventProcessResult:
        now = now or datetime.now(UTC)

        # 1. Acquire processing lease under row lock
        receipt = self.repo.claim_receipt_for_processing(
            receipt_id=receipt_id,
            worker_owner=worker_owner,
            now=now,
        )
        if receipt is None:
            return EventProcessResult(
                receipt_id=receipt_id,
                status="locked_or_missing",
                event_type="UNKNOWN",
                reason="Receipt not found or locked by concurrent worker",
            )

        if receipt["receipt_status"] == ReceiptStatus.PROCESSED.value:
            # Idempotent no-op: already successfully processed
            return EventProcessResult(
                receipt_id=receipt_id,
                status="already_processed",
                event_type="UNKNOWN",
                reason="Receipt already processed",
            )

        workspace_id = UUID(str(receipt["workspace_id"]))
        provider = receipt["provider"]
        event_identity = receipt["event_identity"]
        source_schema = receipt["source_schema"]

        # Parse payload ref or cached event data
        payload_data: dict[str, Any] = {}
        if receipt.get("payload_ref"):
            try:
                payload_data = json.loads(receipt["payload_ref"])
            except Exception:
                pass

        raw_event_type = payload_data.get("event_type") or "NOTIFICATION"
        try:
            event_type = InboundEventType(raw_event_type)
        except ValueError:
            event_type = InboundEventType.NOTIFICATION

        try:
            # 2. Execute domain effects based on event type
            result = self._apply_business_effect(
                workspace_id=workspace_id,
                receipt_id=receipt_id,
                provider=provider,
                event_identity=event_identity,
                event_type=event_type,
                payload_data=payload_data,
                now=now,
            )

            # 3. Resolve safety hold
            self.repo.resolve_safety_hold(
                workspace_id=workspace_id,
                source_receipt_id=receipt_id,
            )

            # 4. Mark receipt PROCESSED and commit
            self.repo.mark_receipt_processed(receipt_id)
            self.session.commit()

            record_event_processed(provider, event_type.value)
            return result

        except Exception as exc:
            self.session.rollback()
            safe_error = f"{type(exc).__name__}: {str(exc)}"
            logger.exception(
                "Event processing failed",
                extra={
                    "receipt_id": str(receipt_id),
                    "workspace_id": str(workspace_id),
                    "provider": provider,
                    "event_identity": event_identity,
                },
            )
            # Mark receipt retry or failed in a new transaction
            self.repo.mark_receipt_failed(receipt_id, safe_error)
            self.session.commit()
            record_event_failed(provider, type(exc).__name__)
            raise

    def _apply_business_effect(
        self,
        *,
        workspace_id: UUID,
        receipt_id: UUID,
        provider: str,
        event_identity: str,
        event_type: InboundEventType,
        payload_data: dict[str, Any],
        now: datetime,
    ) -> EventProcessResult:
        # A. Notification / Sync hint (e.g. Gmail Pub/Sub or Graph change)
        if event_type == InboundEventType.NOTIFICATION:
            return EventProcessResult(
                receipt_id=receipt_id,
                status="processed",
                event_type=event_type.value,
                suppression_created=False,
                reason="Notification processed; sync hint recorded",
            )

        # B. Safety events: BOUNCE, COMPLAINT, UNSUBSCRIBE
        raw_email = payload_data.get("recipient_email")
        if not raw_email:
            # If no recipient email is present in the payload, we cannot suppress an address
            return EventProcessResult(
                receipt_id=receipt_id,
                status="processed_no_recipient",
                event_type=event_type.value,
                reason="Event payload missing recipient email",
            )

        normalized = normalize_email(raw_email)
        canonical_address = normalized.canonical

        # Ensure recipient_addresses record exists in this workspace
        address_id = self.repo.ensure_recipient_address(
            workspace_id=workspace_id,
            canonical_address=canonical_address,
            original_display=normalized.original,
        )

        suppression_created = False
        enrollments_stopped = 0
        messages_cancelled = 0

        # Handle BOUNCE
        if event_type == InboundEventType.BOUNCE:
            raw_bounce = payload_data.get("bounce_classification") or "UNKNOWN"
            try:
                bounce_cls = BounceClassification(raw_bounce)
            except ValueError:
                bounce_cls = BounceClassification.UNKNOWN

            if bounce_cls == BounceClassification.HARD:
                # Permanent delivery failure: durable suppression and cancellation
                _, suppression_created = self.repo.upsert_suppression(
                    workspace_id=workspace_id,
                    address_id=address_id,
                    reason="HARD_BOUNCE",
                    provider_receipt_id=receipt_id,
                    source_key=event_identity,
                    evidence=payload_data,
                )
                stopped_ids = self.repo.stop_active_enrollments(
                    workspace_id=workspace_id,
                    address_id=address_id,
                    stop_reason="HARD_BOUNCE",
                )
                enrollments_stopped = len(stopped_ids)
                for eid in stopped_ids:
                    self.repo.record_recipient_outcome(
                        workspace_id=workspace_id,
                        enrollment_id=eid,
                        kind="HARD_BOUNCE",
                        source_key=event_identity,
                        occurred_at=now,
                    )

                messages_cancelled = self.repo.cancel_non_terminal_messages(
                    workspace_id=workspace_id,
                    address_id=address_id,
                    terminal_reason="hard_bounce",
                )

                self.repo.record_domain_event(
                    workspace_id=workspace_id,
                    event_type="recipient.suppressed",
                    aggregate_type="recipient_address",
                    aggregate_id=address_id,
                    semantic_key=f"hard_bounce:{event_identity}",
                    occurred_at=now,
                    payload={"reason": "HARD_BOUNCE", "receipt_id": str(receipt_id)},
                )
                record_bounce_processed(provider, "HARD")
                if suppression_created:
                    record_suppression_created("HARD_BOUNCE")
                if messages_cancelled > 0:
                    record_message_cancelled_due_to_event("hard_bounce")

            else:
                # Temporary / Soft bounce: record diagnostic evidence without permanent suppression
                record_bounce_processed(provider, "SOFT")
                return EventProcessResult(
                    receipt_id=receipt_id,
                    status="processed_soft_bounce",
                    event_type=event_type.value,
                    suppression_created=False,
                    reason="Soft bounce recorded without permanent suppression",
                )

        # Handle COMPLAINT
        elif event_type == InboundEventType.COMPLAINT:
            _, suppression_created = self.repo.upsert_suppression(
                workspace_id=workspace_id,
                address_id=address_id,
                reason="COMPLAINT",
                provider_receipt_id=receipt_id,
                source_key=event_identity,
                evidence=payload_data,
            )
            stopped_ids = self.repo.stop_active_enrollments(
                workspace_id=workspace_id,
                address_id=address_id,
                stop_reason="COMPLAINT",
            )
            enrollments_stopped = len(stopped_ids)
            for eid in stopped_ids:
                self.repo.record_recipient_outcome(
                    workspace_id=workspace_id,
                    enrollment_id=eid,
                    kind="COMPLAINT",
                    source_key=event_identity,
                    occurred_at=now,
                )

            messages_cancelled = self.repo.cancel_non_terminal_messages(
                workspace_id=workspace_id,
                address_id=address_id,
                terminal_reason="complaint",
            )

            self.repo.record_domain_event(
                workspace_id=workspace_id,
                event_type="recipient.suppressed",
                aggregate_type="recipient_address",
                aggregate_id=address_id,
                semantic_key=f"complaint:{event_identity}",
                occurred_at=now,
                payload={"reason": "COMPLAINT", "receipt_id": str(receipt_id)},
            )
            record_complaint_processed(provider)
            if suppression_created:
                record_suppression_created("COMPLAINT")
            if messages_cancelled > 0:
                record_message_cancelled_due_to_event("complaint")

        # Handle UNSUBSCRIBE
        elif event_type == InboundEventType.UNSUBSCRIBE:
            _, suppression_created = self.repo.upsert_suppression(
                workspace_id=workspace_id,
                address_id=address_id,
                reason="UNSUBSCRIBE",
                provider_receipt_id=receipt_id,
                source_key=event_identity,
                evidence=payload_data,
            )
            stopped_ids = self.repo.stop_active_enrollments(
                workspace_id=workspace_id,
                address_id=address_id,
                stop_reason="UNSUBSCRIBED",
            )
            enrollments_stopped = len(stopped_ids)
            for eid in stopped_ids:
                self.repo.record_recipient_outcome(
                    workspace_id=workspace_id,
                    enrollment_id=eid,
                    kind="UNSUBSCRIBED",
                    source_key=event_identity,
                    occurred_at=now,
                )

            messages_cancelled = self.repo.cancel_non_terminal_messages(
                workspace_id=workspace_id,
                address_id=address_id,
                terminal_reason="unsubscribed",
            )

            self.repo.record_domain_event(
                workspace_id=workspace_id,
                event_type="recipient.suppressed",
                aggregate_type="recipient_address",
                aggregate_id=address_id,
                semantic_key=f"unsubscribe:{event_identity}",
                occurred_at=now,
                payload={"reason": "UNSUBSCRIBE", "receipt_id": str(receipt_id)},
            )
            record_unsubscribe_processed(provider)
            if suppression_created:
                record_suppression_created("UNSUBSCRIBE")
            if messages_cancelled > 0:
                record_message_cancelled_due_to_event("unsubscribed")

        return EventProcessResult(
            receipt_id=receipt_id,
            status="processed",
            event_type=event_type.value,
            suppression_created=suppression_created,
            enrollments_stopped=enrollments_stopped,
            messages_cancelled=messages_cancelled,
        )
