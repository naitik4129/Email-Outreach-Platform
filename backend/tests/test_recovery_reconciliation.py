from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

from app.core.metrics import metrics
from app.modules.mailboxes.providers.base import (
    ProviderCapability,
    ProviderSendResult,
)
from app.modules.mailboxes.providers.registry import ProviderRegistry
from app.modules.sending.reconciliation_service import ReconciliationService
from app.modules.sending.recovery_service import RecoveryService


def test_recover_stale_execution_after_worker_crash() -> None:
    """When a worker crashes and the authorization deadline passes,
    RecoveryService transitions attempt to UNKNOWN and message to UNKNOWN_OUTCOME."""
    metrics.reset()
    now = datetime.now(UTC)
    stale_deadline = now - timedelta(seconds=60)
    ws_id = uuid.uuid4()
    msg_id = uuid.uuid4()
    attempt_id = uuid.uuid4()

    session = MagicMock()
    # Mock discovering 1 candidate row
    row = {
        "attempt_id": str(attempt_id),
        "workspace_id": str(ws_id),
        "message_id": str(msg_id),
        "mailbox_id": str(uuid.uuid4()),
        "version": 3,
        "authorization_deadline": stale_deadline,
    }
    session.execute.return_value.mappings.return_value.all.return_value = [row]

    # UPDATE attempts and UPDATE messages both return rowcount = 1
    mock_update_res = MagicMock()
    mock_update_res.rowcount = 1
    session.execute.return_value = mock_update_res
    session.execute.return_value.mappings.return_value.all.return_value = [row]

    recovery = RecoveryService(session)
    recovery.repository = MagicMock()

    recovered = recovery.recover_stale_executions(workspace_id=ws_id, now=now)

    assert recovered == 1
    assert metrics.get_count("email_stale_execution_recovered_total") == 1
    assert metrics.get_count("email_worker_crash_recovery_total") == 1

    # Verify audit event recorded
    recovery.repository.record_audit_event.assert_called_once_with(
        workspace_id=ws_id,
        action="message.stale_execution_recovered",
        target_id=msg_id,
        after_state={
            "status": "UNKNOWN_OUTCOME",
            "attempt_id": str(attempt_id),
            "authorization_deadline": str(stale_deadline),
        },
        reason="execution_lease_expired",
    )


def test_active_lease_protection_never_stolen() -> None:
    """If an execution is active and within its authorization deadline,
    RecoveryService does NOT touch or recover it."""
    metrics.reset()
    now = datetime.now(UTC)
    session = MagicMock()
    # Query with deadline <= now returns no rows
    session.execute.return_value.mappings.return_value.all.return_value = []

    recovery = RecoveryService(session)
    recovery.repository = MagicMock()

    recovered = recovery.recover_stale_executions(now=now)

    assert recovered == 0
    assert metrics.get_count("email_stale_execution_recovered_total") == 0
    recovery.repository.record_audit_event.assert_not_called()


def test_concurrent_recovery_race_exactly_one_wins() -> None:
    """If two workers race to recover the same stale execution, the first
    updates the attempt; the second observes rowcount=0 and safely skips."""
    metrics.reset()
    now = datetime.now(UTC)
    ws_id = uuid.uuid4()
    msg_id = uuid.uuid4()
    attempt_id = uuid.uuid4()

    session = MagicMock()
    row = {
        "attempt_id": str(attempt_id),
        "workspace_id": str(ws_id),
        "message_id": str(msg_id),
        "mailbox_id": str(uuid.uuid4()),
        "version": 3,
        "authorization_deadline": now - timedelta(seconds=60),
    }
    session.execute.return_value.mappings.return_value.all.return_value = [row]

    # Simulate losing the update race (rowcount = 0)
    mock_losing_update = MagicMock()
    mock_losing_update.rowcount = 0
    session.execute.return_value = mock_losing_update
    session.execute.return_value.mappings.return_value.all.return_value = [row]

    recovery = RecoveryService(session)
    recovery.repository = MagicMock()

    recovered = recovery.recover_stale_executions(workspace_id=ws_id, now=now)

    assert recovered == 0
    assert metrics.get_count("email_stale_execution_recovered_total") == 0


def test_reconciliation_positive_lookup_gmail_microsoft() -> None:
    """When a message in UNKNOWN_OUTCOME was actually delivered remotely,
    ReconciliationService confirms it via provider lookup and transitions it to SENT."""
    metrics.reset()
    now = datetime.now(UTC)
    ws_id = uuid.uuid4()
    mid = uuid.uuid4()
    mb_id = uuid.uuid4()
    attempt_id = uuid.uuid4()
    rfc_id = "<msg-reconcile-test@example.com>"

    session = MagicMock()
    row = {
        "message_id": str(mid),
        "workspace_id": str(ws_id),
        "mailbox_id": str(mb_id),
        "rfc_message_id": rfc_id,
        "version": 4,
        "provider": "GMAIL",
        "current_connection_generation": 1,
        "attempt_id": str(attempt_id),
        "reconciliation_metadata": {},
    }
    session.execute.return_value.mappings.return_value.all.return_value = [row]

    reconciler = ReconciliationService(session)
    reconciler.repository = MagicMock()
    reconciler.repository.get_mailbox_connection.return_value = {
        "credential_ciphertext": b"ciphertext",
        "nonce": b"nonce",
        "encryption_key_id": "k1",
        "protected_config": {},
    }

    mock_provider = MagicMock()
    mock_provider.capabilities = frozenset({ProviderCapability.LOOKUP_MESSAGE})
    mock_provider.lookup_message.return_value = ProviderSendResult(
        status="ACCEPTED",
        provider_message_id="gmail-msg-confirmed-123",
        provider_thread_id="gmail-th-123",
        accepted_at=now,
    )

    with patch.object(ProviderRegistry, "get", return_value=mock_provider):
        with patch(
            "app.modules.sending.reconciliation_service.decrypt_credentials",
            return_value={"access_token": "valid-token"},
        ):
            counts = reconciler.reconcile_unknown_messages(now=now)

            assert counts["reconciled_sent"] == 1
            assert counts["pending"] == 0
            assert counts["unsupported"] == 0
            assert (
                metrics.get_count(
                    "email_reconciliation_total", provider="GMAIL", result="accepted"
                )
                == 1
            )
            mock_provider.lookup_message.assert_called_once_with(
                {"access_token": "valid-token"}, rfc_id
            )
            # Must NOT call send_message
            mock_provider.send_message.assert_not_called()
            reconciler.repository.record_audit_event.assert_called_once()


def test_reconciliation_negative_lookup_stays_unknown_never_blindly_retried() -> None:
    """When a message in UNKNOWN_OUTCOME is not found on provider lookup,
    it remains in UNKNOWN_OUTCOME and is NEVER blindly retried."""
    metrics.reset()
    now = datetime.now(UTC)
    ws_id = uuid.uuid4()
    mid = uuid.uuid4()
    mb_id = uuid.uuid4()
    attempt_id = uuid.uuid4()
    rfc_id = "<msg-reconcile-test@example.com>"

    session = MagicMock()
    row = {
        "message_id": str(mid),
        "workspace_id": str(ws_id),
        "mailbox_id": str(mb_id),
        "rfc_message_id": rfc_id,
        "version": 4,
        "provider": "GMAIL",
        "current_connection_generation": 1,
        "attempt_id": str(attempt_id),
        "reconciliation_metadata": {},
    }
    session.execute.return_value.mappings.return_value.all.return_value = [row]

    reconciler = ReconciliationService(session)
    reconciler.repository = MagicMock()
    reconciler.repository.get_mailbox_connection.return_value = {
        "credential_ciphertext": b"ciphertext",
        "nonce": b"nonce",
        "encryption_key_id": "k1",
        "protected_config": {},
    }

    mock_provider = MagicMock()
    mock_provider.capabilities = frozenset({ProviderCapability.LOOKUP_MESSAGE})
    # Not found on provider
    mock_provider.lookup_message.return_value = None

    with patch.object(ProviderRegistry, "get", return_value=mock_provider):
        with patch(
            "app.modules.sending.reconciliation_service.decrypt_credentials",
            return_value={"access_token": "valid-token"},
        ):
            counts = reconciler.reconcile_unknown_messages(now=now)

            assert counts["reconciled_sent"] == 0
            assert counts["pending"] == 1
            assert (
                metrics.get_count(
                    "email_reconciliation_total", provider="GMAIL", result="pending"
                )
                == 1
            )
            # Never blindly sends
            mock_provider.send_message.assert_not_called()


def test_reconciliation_smtp_unsupported_protocol_safe() -> None:
    """When an UNKNOWN_OUTCOME message was dispatched via SMTP (which cannot lookup
    sent messages by RFC Message ID without IMAP), it is preserved in UNKNOWN_OUTCOME
    with audit metadata, never blindly resent."""
    metrics.reset()
    now = datetime.now(UTC)
    ws_id = uuid.uuid4()
    mid = uuid.uuid4()
    mb_id = uuid.uuid4()
    attempt_id = uuid.uuid4()
    rfc_id = "<smtp-ambiguous@example.com>"

    session = MagicMock()
    row = {
        "message_id": str(mid),
        "workspace_id": str(ws_id),
        "mailbox_id": str(mb_id),
        "rfc_message_id": rfc_id,
        "version": 4,
        "provider": "SMTP",
        "current_connection_generation": 1,
        "attempt_id": str(attempt_id),
        "reconciliation_metadata": {},
    }
    session.execute.return_value.mappings.return_value.all.return_value = [row]

    reconciler = ReconciliationService(session)
    reconciler.repository = MagicMock()

    mock_provider = MagicMock()
    # SMTP lacks LOOKUP_MESSAGE capability
    mock_provider.capabilities = frozenset({ProviderCapability.SEND})

    with patch.object(ProviderRegistry, "get", return_value=mock_provider):
        counts = reconciler.reconcile_unknown_messages(now=now)

        assert counts["unsupported"] == 1
        assert counts["reconciled_sent"] == 0
        assert counts["pending"] == 0
        assert (
            metrics.get_count(
                "email_reconciliation_total", provider="SMTP", result="unsupported"
            )
            == 1
        )
        mock_provider.lookup_message.assert_not_called()
        mock_provider.send_message.assert_not_called()
