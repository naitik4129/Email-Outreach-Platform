from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from app.core.config import Settings
from app.db.session import session_scope
from app.modules.replies.service import ReplySyncService

from workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="mailbox.sync", queue="mailbox.sync", bind=True, acks_late=True)
def sync_mailbox_task(
    self: Any,
    workspace_id: str,
    mailbox_id: str,
    lease_owner: str | None = None,
) -> dict[str, Any]:
    """Phase 13 reply synchronization worker task.

    Invokes ReplySyncService.sync_mailbox to sync inbound messages,
    correlate replies, and enforce campaign safety under distributed leases.
    """
    settings = Settings.current()
    if not settings.reply_sync_enabled:
        logger.info(
            "reply_sync_enabled=False; skipping mailbox.sync for mailbox %s",
            mailbox_id,
        )
        return {"status": "SKIPPED", "mailbox_id": mailbox_id, "reason": "disabled"}

    effective_lease_owner = lease_owner or f"worker-{self.request.id or 'sync'}"

    with session_scope(settings) as session:
        service = ReplySyncService(session)
        result = service.sync_mailbox(
            workspace_id=UUID(workspace_id),
            mailbox_id=UUID(mailbox_id),
            lease_owner=effective_lease_owner,
            max_pages=settings.reply_sync_max_pages_per_run,
            lease_duration_seconds=settings.reply_sync_lease_seconds,
            sync_interval_seconds=settings.reply_sync_interval_seconds,
        )

    logger.info(
        "mailbox.sync completed for mailbox %s: status=%s, discovered=%d, persisted=%d, matched=%d, stopped=%d",
        mailbox_id,
        result.status,
        result.messages_discovered,
        result.messages_persisted,
        result.replies_matched,
        result.enrollments_stopped,
    )
    return {
        "status": result.status,
        "mailbox_id": str(result.mailbox_id),
        "workspace_id": str(result.workspace_id),
        "messages_discovered": result.messages_discovered,
        "messages_persisted": result.messages_persisted,
        "messages_deduplicated": result.messages_deduplicated,
        "replies_matched": result.replies_matched,
        "replies_unresolved": result.replies_unresolved,
        "enrollments_stopped": result.enrollments_stopped,
        "future_messages_cancelled": result.future_messages_cancelled,
        "resync_required": result.resync_required,
        "error": result.error,
    }
