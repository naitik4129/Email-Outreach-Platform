from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from app.core.config import Settings
from app.db.context import set_transaction_context
from app.db.session import session_scope
from app.modules.events.processor import InboundEventProcessor
from app.modules.events.repository import EventRepository, _safe_set_role, _safe_set_workspace
from workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="event.process", queue="webhooks", bind=True, max_retries=5)
def process_event_receipt(
    self: Any,
    receipt_id: str,
    workspace_id: str,
) -> dict[str, Any]:
    """Asynchronous background worker task for processing an inbound event receipt."""
    try:
        rid = UUID(str(receipt_id))
        wsid = UUID(str(workspace_id))
    except (ValueError, TypeError) as exc:
        logger.error(
            "Malformed event.process task arguments",
            extra={"receipt_id": receipt_id, "workspace_id": workspace_id, "error": str(exc)},
        )
        return {"status": "failed", "reason": "invalid_uuid"}

    settings = Settings.current()
    with session_scope(settings) as session:
        _safe_set_role(session, "app_worker_general")
        _safe_set_workspace(session, wsid)
        set_transaction_context(session, workspace_id=wsid)

        processor = InboundEventProcessor(session)
        try:
            result = processor.process_receipt(rid, worker_owner=f"worker-{self.request.id or 'default'}")
            return result.model_dump()
        except Exception as exc:
            # Transient error handling with backoff
            logger.warning(
                "Transient error during event receipt processing; scheduling retry",
                extra={"receipt_id": receipt_id, "attempt": self.request.retries, "error": str(exc)},
            )
            # Exponential backoff with 10s base
            countdown = min(300, 10 * (2**self.request.retries))
            raise self.retry(exc=exc, countdown=countdown)


def recover_stale_event_processing(limit: int = 50) -> int:
    """Recovery sweep reverting abandoned event leases to RETRY."""
    settings = Settings.current()
    with session_scope(settings) as session:
        _safe_set_role(session, "app_worker_general")
        repo = EventRepository(session)
        recovered = repo.recover_stale_processing_receipts(limit=limit)
        if recovered > 0:
            logger.warning("Recovered %d stale event processing lease(s) to RETRY", recovered)
        return recovered
