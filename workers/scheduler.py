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
        self._recover_imports()

    def _recover_imports(self) -> None:
        from sqlalchemy import text
        from app.db.session import session_scope
        from workers.celery_app import celery_app

        with session_scope(self.settings) as session:
            session.execute(text("SET ROLE app_scheduler"))
            rows = session.execute(
                text(
                    """
                    SELECT workspace_id, id FROM import_jobs
                    WHERE (status = 'PENDING' AND next_due_at <= pg_catalog.transaction_timestamp())
                       OR (status = 'PROCESSING' AND lease_expires_at <= pg_catalog.transaction_timestamp())
                    """
                )
            ).all()

            if rows:
                logger.info(f"Discovered {len(rows)} due/stuck import(s). Dispatching recovery tasks.")
                for workspace_id, import_id in rows:
                    celery_app.send_task(
                        "imports.process_chunk",
                        kwargs={"workspace_id": str(workspace_id), "import_id": str(import_id)},
                        queue="imports",
                    )

    def run_forever(self) -> None:
        configure_logging(self.settings, service_name="scheduler")
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        logger.info("Scheduler startup")
        while not self.stopped.is_set():
            try:
                self.run_once()
            except Exception:
                logger.exception("Error in scheduler run_once")
            self.stopped.wait(self.settings.scheduler_poll_seconds)
        logger.info("Scheduler shutdown complete")


def main() -> None:
    SchedulerRuntime().run_forever()


if __name__ == "__main__":
    main()
