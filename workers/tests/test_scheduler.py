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
