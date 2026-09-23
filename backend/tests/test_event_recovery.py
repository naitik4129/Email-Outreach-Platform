from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from app.modules.events.repository import EventRepository
from workers.events import process_event_receipt, recover_stale_event_processing


class TestEventRecoveryAndLeaseManagement:
    def test_recover_stale_processing_receipts_resets_abandoned_leases(self) -> None:
        session = MagicMock()
        bind_mock = MagicMock()
        bind_mock.dialect.name = "postgresql"
        session.get_bind.return_value = bind_mock

        repo = EventRepository(session)

        stale_id_1 = uuid4()
        stale_id_2 = uuid4()
        now = datetime.now(UTC)

        # Mock select candidates returning 2 stale rows
        mock_candidates = [
            {"id": stale_id_1, "version": 1},
            {"id": stale_id_2, "version": 4},
        ]
        select_res = MagicMock()
        select_res.mappings.return_value.all.return_value = mock_candidates

        update_res_1 = MagicMock()
        update_res_1.rowcount = 1
        update_res_2 = MagicMock()
        update_res_2.rowcount = 1

        session.execute.side_effect = [select_res, update_res_1, update_res_2]

        recovered = repo.recover_stale_processing_receipts(now=now, limit=10)

        assert recovered == 2
        # Verify commit occurred
        session.commit.assert_called_once()

    def test_recover_stale_processing_receipts_none_found(self) -> None:
        session = MagicMock()
        bind_mock = MagicMock()
        bind_mock.dialect.name = "postgresql"
        session.get_bind.return_value = bind_mock

        repo = EventRepository(session)

        select_res = MagicMock()
        select_res.mappings.return_value.all.return_value = []
        session.execute.return_value = select_res

        recovered = repo.recover_stale_processing_receipts()

        assert recovered == 0
        session.commit.assert_not_called()

    def test_worker_recover_stale_event_processing_invokes_repo(self) -> None:
        with patch("workers.events.session_scope") as mock_scope:
            mock_session = MagicMock()
            mock_scope.return_value.__enter__.return_value = mock_session
            with patch.object(EventRepository, "recover_stale_processing_receipts", return_value=3) as mock_repo_rec:
                count = recover_stale_event_processing(limit=25)
                assert count == 3
                mock_repo_rec.assert_called_once_with(limit=25)

    def test_celery_task_invalid_uuid_arguments_fails_gracefully(self) -> None:
        result = process_event_receipt("not-a-uuid", str(uuid4()))
        assert result == {"status": "failed", "reason": "invalid_uuid"}

    def test_celery_task_retries_on_transient_failure(self) -> None:
        rid = str(uuid4())
        wsid = str(uuid4())

        with patch.object(process_event_receipt, "retry", side_effect=RuntimeError("Retry scheduled")) as mock_retry:
            process_event_receipt.push_request(id="test-task-123", retries=2)
            try:
                with patch("workers.events.session_scope") as mock_scope:
                    mock_session = MagicMock()
                    mock_scope.return_value.__enter__.return_value = mock_session

                    with patch("workers.events.InboundEventProcessor") as mock_proc_cls:
                        proc_instance = MagicMock()
                        proc_instance.process_receipt.side_effect = Exception("DB Connection timeout")
                        mock_proc_cls.return_value = proc_instance

                        with pytest.raises(RuntimeError, match="Retry scheduled"):
                            process_event_receipt(rid, wsid)

                        mock_retry.assert_called_once()
                        call_kwargs = mock_retry.call_args[1]
                        assert call_kwargs["countdown"] == 40
            finally:
                process_event_receipt.pop_request()

