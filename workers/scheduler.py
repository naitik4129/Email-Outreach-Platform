from __future__ import annotations

import logging
import signal
from threading import Event

from app.core.config import Settings
from app.core.logging import configure_logging
from app.db.session import session_scope
from app.modules.scheduler.service import OutboxPublisherService, SchedulerService

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

        # 1. Discover due work and claim
        try:
            with session_scope(self.settings) as session:
                service = SchedulerService(session, self.settings)
                disc, claimed = service.discover_and_claim_due_work()
                if claimed > 0:
                    logger.info(f"Scheduler claimed {claimed}/{disc} due messages")
        except Exception:
            logger.exception("Error during scheduler due discovery and claim")

        # 2. Publish pending outbox deliveries to Celery queue
        try:
            with session_scope(self.settings) as session:
                publisher = OutboxPublisherService(session, settings=self.settings)
                published = publisher.publish_pending_deliveries()
                if published > 0:
                    logger.info(f"Outbox publisher published {published} deliveries to email.send")
        except Exception:
            logger.exception("Error during outbox publication")

        # 3. Recover expired message claims
        try:
            with session_scope(self.settings) as session:
                service = SchedulerService(session, self.settings)
                recovered = service.recover_expired_claims()
                if recovered > 0:
                    logger.info(f"Scheduler recovered {recovered} expired message claims")
        except Exception:
            logger.exception("Error during expired claim recovery")

        # 4. Recover stale outbox leases
        try:
            with session_scope(self.settings) as session:
                publisher = OutboxPublisherService(session, settings=self.settings)
                recovered_leases = publisher.recover_stale_leases()
                if recovered_leases > 0:
                    logger.info(f"Outbox publisher recovered {recovered_leases} stale leases")
        except Exception:
            logger.exception("Error during stale outbox lease recovery")

        # 5. Recover imports
        try:
            self._recover_imports()
        except Exception:
            logger.exception("Error during import recovery")

        # 6. Recover stale in-flight executions (workers crashed/stuck in SENDING)
        try:
            with session_scope(self.settings) as session:
                from app.modules.sending.recovery_service import RecoveryService

                recovery_service = RecoveryService(session)
                recovered_stale = recovery_service.recover_stale_executions()
                if recovered_stale > 0:
                    logger.info(
                        f"Scheduler recovered {recovered_stale} stale SENDING messages to UNKNOWN_OUTCOME"
                    )
        except Exception:
            logger.exception("Error during stale execution recovery")

        # 7. Reconcile ambiguous message outcomes
        try:
            with session_scope(self.settings) as session:
                from app.modules.sending.reconciliation_service import (
                    ReconciliationService,
                )

                reconciliation_service = ReconciliationService(session)
                reconciled = reconciliation_service.reconcile_unknown_messages()
                if reconciled.get("reconciled_sent", 0) > 0:
                    logger.info(
                        f"Scheduler reconciled {reconciled['reconciled_sent']} messages to SENT"
                    )
        except Exception:
            logger.exception("Error during message outcome reconciliation")

        # 8. Discover and enqueue due mailbox reply syncs (Phase 13)
        if self.settings.reply_sync_enabled:
            try:
                with session_scope(self.settings) as session:
                    from datetime import UTC, datetime

                    from app.modules.replies.repository import ReplyRepository

                    from workers.celery_app import celery_app

                    repo = ReplyRepository(session)
                    now = datetime.now(UTC)
                    claimed_syncs = repo.claim_due_sync_states(
                        lease_owner="scheduler-sync-dispatcher",
                        now=now,
                        limit=20,
                        lease_duration_seconds=self.settings.reply_sync_lease_seconds,
                    )
                    session.commit()

                    if claimed_syncs:
                        logger.info("Scheduler dispatched %d due mailbox sync tasks", len(claimed_syncs))
                        for sync in claimed_syncs:
                            celery_app.send_task(
                                "mailbox.sync",
                                kwargs={
                                    "workspace_id": str(sync["workspace_id"]),
                                    "mailbox_id": str(sync["mailbox_id"]),
                                    "lease_owner": "scheduler-sync-dispatcher",
                                },
                                queue="mailbox.sync",
                            )
            except Exception:
                logger.exception("Error during mailbox sync due discovery")

            # 9. Recover stale reply sync leases
            try:
                with session_scope(self.settings) as session:
                    from app.modules.replies.service import ReplySyncService

                    service = ReplySyncService(session)
                    recovered_sync_leases = service.recover_stale_leases()
                    if recovered_sync_leases > 0:
                        logger.info("Scheduler recovered %d stale sync leases", recovered_sync_leases)
            except Exception:
                logger.exception("Error during stale sync lease recovery")

    def _recover_imports(self) -> None:
        from app.db.session import session_scope
        from sqlalchemy import text

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
