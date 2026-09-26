from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from app.modules.events.processor import InboundEventProcessor
from app.modules.events.schemas import ReceiptStatus


def _receipt_row(
    receipt_id: object,
    workspace_id: object,
    event_type: str,
    payload_data: dict,
    receipt_status: str = ReceiptStatus.RECEIVED.value,
) -> dict:
    return {
        "receipt_id": receipt_id,
        "workspace_id": workspace_id,
        "provider": "SMTP",
        "event_identity": "test:event:123",
        "source_schema": "provider.event.v1",
        "receipt_status": receipt_status,
        "payload_ref": json.dumps(payload_data),
    }


class TestInboundEventProcessor:
    def test_process_hard_bounce_creates_suppression_and_cancels_messages(self) -> None:
        session = MagicMock()
        processor = InboundEventProcessor(session)
        processor.repo = MagicMock()

        receipt_id = uuid4()
        workspace_id = uuid4()
        address_id = uuid4()
        enrollment_id = uuid4()

        payload = {
            "event_type": "BOUNCE",
            "bounce_classification": "HARD",
            "recipient_email": "bounced@target.com",
            "reason": "5.1.1 User unknown",
        }
        processor.repo.find_message_for_bounce.return_value = (None, None)
        processor.repo.claim_receipt_for_processing.return_value = _receipt_row(
            receipt_id, workspace_id, "BOUNCE", payload
        )
        processor.repo.ensure_recipient_address.return_value = address_id
        processor.repo.upsert_suppression.return_value = (uuid4(), True)
        processor.repo.stop_active_enrollments.return_value = [enrollment_id]
        processor.repo.cancel_non_terminal_messages.return_value = 2

        result = processor.process_receipt(receipt_id)

        assert result.status == "processed"
        assert result.suppression_created is True
        assert result.enrollments_stopped == 1
        assert result.messages_cancelled == 2

        processor.repo.upsert_suppression.assert_called_once_with(
            workspace_id=workspace_id,
            address_id=address_id,
            reason="HARD_BOUNCE",
            provider_receipt_id=receipt_id,
            domain_event_id=None,
            source_key="test:event:123",
            evidence=payload,
        )
        processor.repo.stop_active_enrollments.assert_called_once_with(
            workspace_id=workspace_id,
            address_id=address_id,
            stop_reason="HARD_BOUNCE",
        )
        processor.repo.record_recipient_outcome.assert_called_once()
        processor.repo.cancel_non_terminal_messages.assert_called_once_with(
            workspace_id=workspace_id,
            address_id=address_id,
            terminal_reason="hard_bounce",
        )
        processor.repo.record_domain_event.assert_called_once()
        processor.repo.resolve_safety_hold.assert_called_once_with(
            workspace_id=workspace_id,
            source_receipt_id=receipt_id,
        )
        processor.repo.mark_receipt_processed.assert_called_once_with(receipt_id)
        session.commit.assert_called_once()

    def test_process_soft_bounce_does_not_suppress_or_cancel(self) -> None:
        session = MagicMock()
        processor = InboundEventProcessor(session)
        processor.repo = MagicMock()

        receipt_id = uuid4()
        workspace_id = uuid4()
        address_id = uuid4()

        payload = {
            "event_type": "BOUNCE",
            "bounce_classification": "SOFT",
            "recipient_email": "full@target.com",
            "reason": "4.2.2 Mailbox quota exceeded",
        }
        processor.repo.find_message_for_bounce.return_value = (None, None)
        processor.repo.claim_receipt_for_processing.return_value = _receipt_row(
            receipt_id, workspace_id, "BOUNCE", payload
        )
        processor.repo.ensure_recipient_address.return_value = address_id

        result = processor.process_receipt(receipt_id)

        assert result.status == "processed_soft_bounce"
        assert result.suppression_created is False
        assert result.messages_cancelled == 0

        processor.repo.upsert_suppression.assert_not_called()
        processor.repo.cancel_non_terminal_messages.assert_not_called()
        processor.repo.resolve_safety_hold.assert_called_once_with(
            workspace_id=workspace_id,
            source_receipt_id=receipt_id,
        )
        processor.repo.mark_receipt_processed.assert_called_once_with(receipt_id)
        session.commit.assert_called_once()

    def test_process_complaint_creates_suppression_and_cancels(self) -> None:
        session = MagicMock()
        processor = InboundEventProcessor(session)
        processor.repo = MagicMock()

        receipt_id = uuid4()
        workspace_id = uuid4()
        address_id = uuid4()

        payload = {
            "event_type": "COMPLAINT",
            "recipient_email": "complainer@target.com",
        }
        processor.repo.claim_receipt_for_processing.return_value = _receipt_row(
            receipt_id, workspace_id, "COMPLAINT", payload
        )
        processor.repo.ensure_recipient_address.return_value = address_id
        processor.repo.upsert_suppression.return_value = (uuid4(), True)
        processor.repo.stop_active_enrollments.return_value = []
        processor.repo.cancel_non_terminal_messages.return_value = 1

        result = processor.process_receipt(receipt_id)

        assert result.status == "processed"
        assert result.suppression_created is True
        assert result.messages_cancelled == 1

        processor.repo.upsert_suppression.assert_called_once_with(
            workspace_id=workspace_id,
            address_id=address_id,
            reason="COMPLAINT",
            provider_receipt_id=receipt_id,
            source_key="test:event:123",
            evidence=payload,
        )
        processor.repo.cancel_non_terminal_messages.assert_called_once_with(
            workspace_id=workspace_id,
            address_id=address_id,
            terminal_reason="complaint",
        )

    def test_process_unsubscribe_creates_suppression_and_cancels(self) -> None:
        session = MagicMock()
        processor = InboundEventProcessor(session)
        processor.repo = MagicMock()

        receipt_id = uuid4()
        workspace_id = uuid4()
        address_id = uuid4()

        payload = {
            "event_type": "UNSUBSCRIBE",
            "recipient_email": "optout@target.com",
        }
        processor.repo.claim_receipt_for_processing.return_value = _receipt_row(
            receipt_id, workspace_id, "UNSUBSCRIBE", payload
        )
        processor.repo.ensure_recipient_address.return_value = address_id
        processor.repo.upsert_suppression.return_value = (uuid4(), True)
        processor.repo.stop_active_enrollments.return_value = []
        processor.repo.cancel_non_terminal_messages.return_value = 3

        result = processor.process_receipt(receipt_id)

        assert result.status == "processed"
        assert result.suppression_created is True
        assert result.messages_cancelled == 3

        processor.repo.upsert_suppression.assert_called_once_with(
            workspace_id=workspace_id,
            address_id=address_id,
            reason="UNSUBSCRIBE",
            provider_receipt_id=receipt_id,
            source_key="test:event:123",
            evidence=payload,
        )
        processor.repo.cancel_non_terminal_messages.assert_called_once_with(
            workspace_id=workspace_id,
            address_id=address_id,
            terminal_reason="unsubscribed",
        )

    def test_idempotent_replay_already_processed_receipt(self) -> None:
        session = MagicMock()
        processor = InboundEventProcessor(session)
        processor.repo = MagicMock()

        receipt_id = uuid4()
        workspace_id = uuid4()

        processor.repo.claim_receipt_for_processing.return_value = _receipt_row(
            receipt_id,
            workspace_id,
            "BOUNCE",
            {"event_type": "BOUNCE"},
            receipt_status=ReceiptStatus.PROCESSED.value,
        )

        result = processor.process_receipt(receipt_id)

        assert result.status == "already_processed"
        processor.repo.upsert_suppression.assert_not_called()
        processor.repo.cancel_non_terminal_messages.assert_not_called()
        processor.repo.mark_receipt_processed.assert_not_called()

    def test_locked_or_missing_receipt_returns_locked_status(self) -> None:
        session = MagicMock()
        processor = InboundEventProcessor(session)
        processor.repo = MagicMock()

        receipt_id = uuid4()
        processor.repo.claim_receipt_for_processing.return_value = None

        result = processor.process_receipt(receipt_id)

        assert result.status == "locked_or_missing"
        processor.repo.upsert_suppression.assert_not_called()

    def test_missing_recipient_email_returns_processed_no_recipient(self) -> None:
        session = MagicMock()
        processor = InboundEventProcessor(session)
        processor.repo = MagicMock()

        receipt_id = uuid4()
        workspace_id = uuid4()

        payload = {"event_type": "BOUNCE", "bounce_classification": "HARD"}
        processor.repo.find_message_for_bounce.return_value = (None, None)
        processor.repo.claim_receipt_for_processing.return_value = _receipt_row(
            receipt_id, workspace_id, "BOUNCE", payload
        )

        result = processor.process_receipt(receipt_id)

        assert result.status == "processed_no_recipient"
        processor.repo.upsert_suppression.assert_not_called()
        processor.repo.resolve_safety_hold.assert_called_once()
        processor.repo.mark_receipt_processed.assert_called_once()
        session.commit.assert_called_once()

    def test_processing_failure_rolls_back_and_marks_failed(self) -> None:
        session = MagicMock()
        processor = InboundEventProcessor(session)
        processor.repo = MagicMock()

        receipt_id = uuid4()
        workspace_id = uuid4()

        payload = {
            "event_type": "BOUNCE",
            "bounce_classification": "HARD",
            "recipient_email": "error@target.com",
        }
        processor.repo.find_message_for_bounce.return_value = (None, None)
        processor.repo.claim_receipt_for_processing.return_value = _receipt_row(
            receipt_id, workspace_id, "BOUNCE", payload
        )
        processor.repo.ensure_recipient_address.side_effect = RuntimeError("Database deadlocked")

        with pytest.raises(RuntimeError, match="Database deadlocked"):
            processor.process_receipt(receipt_id)

        session.rollback.assert_called_once()
        processor.repo.mark_receipt_failed.assert_called_once()
        # Ensure error status commit
        assert session.commit.call_count == 1
