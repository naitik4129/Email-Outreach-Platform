from __future__ import annotations

import uuid
from unittest.mock import MagicMock, patch

import pytest
from app.core.config import reset_settings_cache
from app.modules.replies.schemas import MailboxSyncResult

from workers.celery_app import celery_app
from workers.sync_task import sync_mailbox_task


def test_sync_task_config_uses_late_ack() -> None:
    assert sync_mailbox_task.acks_late is True


def test_sync_task_routed_to_mailbox_sync_queue() -> None:
    route = celery_app.conf.task_routes["mailbox.sync"]
    assert route == {"queue": "mailbox.sync"}


def test_sync_task_skipped_when_feature_flag_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REPLY_SYNC_ENABLED", "false")
    reset_settings_cache()
    try:
        with patch("workers.sync_task.session_scope") as session_mock:
            res = sync_mailbox_task.run(
                workspace_id=str(uuid.uuid4()),
                mailbox_id=str(uuid.uuid4()),
            )
            session_mock.assert_not_called()
        assert res["status"] == "SKIPPED"
        assert res["reason"] == "disabled"
    finally:
        reset_settings_cache()


def test_sync_task_executes_service_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REPLY_SYNC_ENABLED", "true")
    reset_settings_cache()
    try:
        workspace_id = uuid.uuid4()
        mailbox_id = uuid.uuid4()
        fake_result = MailboxSyncResult(
            mailbox_id=mailbox_id,
            workspace_id=workspace_id,
            status="COMPLETED",
            messages_discovered=3,
            messages_persisted=2,
            messages_deduplicated=1,
            replies_matched=1,
            enrollments_stopped=1,
            future_messages_cancelled=2,
        )

        with (
            patch("workers.sync_task.session_scope") as mock_scope,
            patch("workers.sync_task.ReplySyncService") as mock_svc_cls,
        ):
            mock_session = MagicMock()
            mock_scope.return_value.__enter__.return_value = mock_session
            mock_svc = MagicMock()
            mock_svc.sync_mailbox.return_value = fake_result
            mock_svc_cls.return_value = mock_svc

            res = sync_mailbox_task.run(
                workspace_id=str(workspace_id),
                mailbox_id=str(mailbox_id),
                lease_owner="test-worker-1",
            )

            mock_svc_cls.assert_called_once_with(mock_session)
            mock_svc.sync_mailbox.assert_called_once_with(
                workspace_id=workspace_id,
                mailbox_id=mailbox_id,
                lease_owner="test-worker-1",
                max_pages=10,
                lease_duration_seconds=120,
                sync_interval_seconds=300,
            )
            assert res["status"] == "COMPLETED"
            assert res["messages_discovered"] == 3
            assert res["messages_persisted"] == 2
            assert res["enrollments_stopped"] == 1
    finally:
        reset_settings_cache()
