from __future__ import annotations

import logging
import signal
from threading import Event

from app.core.config import Settings
from app.core.logging import configure_logging

logger = logging.getLogger(__name__)


class SchedulerRuntime:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings.current()
        self.stopped = Event()

    def stop(self, signum: int | None = None, frame: object | None = None) -> None:
        logger.info("Scheduler shutdown requested")
        self.stopped.set()

    def run_once(self) -> None:
        logger.info("Scheduler heartbeat")

    def run_forever(self) -> None:
        configure_logging(self.settings, service_name="scheduler")
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        logger.info("Scheduler startup")
        while not self.stopped.is_set():
            self.run_once()
            self.stopped.wait(self.settings.scheduler_poll_seconds)
        logger.info("Scheduler shutdown complete")


def main() -> None:
    SchedulerRuntime().run_forever()


if __name__ == "__main__":
    main()
