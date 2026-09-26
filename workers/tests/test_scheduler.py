from __future__ import annotations

from app.core.config import Settings

from workers.scheduler import SchedulerRuntime


def test_scheduler_run_once_does_not_schedule_product_work() -> None:
    scheduler = SchedulerRuntime(Settings())

    scheduler.run_once()

    assert not scheduler.stopped.is_set()


def test_scheduler_stop_sets_shutdown_event() -> None:
    scheduler = SchedulerRuntime(Settings())

    scheduler.stop()

    assert scheduler.stopped.is_set()


def test_scheduler_ensures_sync_states_before_dispatching_reply_sync() -> None:
    """Root cause of "replies are never detected": a mailbox with no sync-state
    row is never dispatched. Every tick must make sure connected mailboxes have
    one (idempotent), and only when reply sync is enabled."""
    from contextlib import contextmanager
    from unittest.mock import MagicMock, patch

    calls: list[str] = []

    @contextmanager
    def fake_scope(settings):  # type: ignore[no-untyped-def]
        yield MagicMock()

    class FakeService:
        def __init__(self, session):  # type: ignore[no-untyped-def]
            pass

        def ensure_sync_states(self) -> int:
            calls.append("ensure")
            return 2

        def recover_stale_leases(self) -> int:
            return 0

    class FakeRepo:
        def __init__(self, session):  # type: ignore[no-untyped-def]
            pass

        def claim_due_sync_states(self, **kwargs):  # type: ignore[no-untyped-def]
            calls.append("claim")
            return []

    enabled = Settings().model_copy(update={"reply_sync_enabled": True})
    disabled = Settings().model_copy(update={"reply_sync_enabled": False})
    with (
        patch("workers.scheduler.session_scope", fake_scope),
        patch("app.modules.replies.service.ReplySyncService", FakeService),
        patch("app.modules.replies.repository.ReplyRepository", FakeRepo),
    ):
        SchedulerRuntime(enabled).run_once()
        assert calls.index("ensure") < calls.index("claim")

        calls.clear()
        SchedulerRuntime(disabled).run_once()
        assert "ensure" not in calls and "claim" not in calls
