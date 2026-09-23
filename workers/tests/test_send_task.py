from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest

from app.core.config import Settings, reset_settings_cache
from app.modules.sending.schemas import SendOutcome
from workers.celery_app import celery_app
from workers.send_task import send_email_task


def _payload() -> dict[str, object]:
    message_id = str(uuid.uuid4())
    return {
        "schema_version": 1,
        "work_id": str(uuid.uuid4()),
        "delivery_id": str(uuid.uuid4()),
        "message_id": message_id,
        "dispatch_id": str(uuid.uuid4()),
        "resource_id": message_id,
        "workspace_id": str(uuid.uuid4()),
        "dispatch_generation": 1,
    }


def test_task_config_uses_late_ack_and_no_autoretry() -> None:
    """acks_late=True is a deliberate per-task override (idempotency here is
    DB-enforced, not ack-timing-enforced); autoretry_for must remain unset
    since the message state machine -- not Celery -- owns send retries."""
    assert send_email_task.acks_late is True
    assert getattr(send_email_task, "autoretry_for", None) in (None, ())


def test_task_routed_to_email_send_queue() -> None:
    route = celery_app.conf.task_routes["email.send"]
    assert route == {"queue": "email.send"}


def test_missing_payload_raises() -> None:
    with pytest.raises(ValueError):
        send_email_task.run(None)


def test_malformed_payload_rejected_before_any_db_work() -> None:
    with patch("workers.send_task.session_scope") as session_scope_mock:
        with pytest.raises(Exception):
            send_email_task.run({"not_a_valid_field": True})
        session_scope_mock.assert_not_called()


def test_feature_flag_off_keeps_placeholder_behavior(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SENDING_WORKER_ENABLED", "false")
    reset_settings_cache()
    try:
        with patch("workers.send_task.SendingService") as service_cls:
            result = send_email_task.run(_payload())
            service_cls.assert_not_called()
        assert result["status"] == "received"
    finally:
        reset_settings_cache()


def test_feature_flag_on_invokes_sending_service(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SENDING_WORKER_ENABLED", "true")
    reset_settings_cache()
    payload = _payload()
    try:
        fake_outcome = SendOutcome(
            message_id=uuid.UUID(payload["message_id"]), outcome="SENT", reason=None
        )
        with patch("workers.send_task.session_scope") as session_scope_mock:
            session_scope_mock.return_value.__enter__.return_value = MagicMock()
            with patch("workers.send_task.SendingService") as service_cls:
                service_cls.return_value.execute.return_value = fake_outcome
                result = send_email_task.run(payload)
                service_cls.return_value.execute.assert_called_once()
        assert result["status"] == "SENT"
    finally:
        reset_settings_cache()
