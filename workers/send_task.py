from __future__ import annotations

import logging
from typing import Any

from app.core.config import Settings
from app.db.session import session_scope
from app.modules.scheduler.schemas import SendTaskPayload
from app.modules.sending.service import SendingService
from workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="email.send", queue="email.send", bind=True, acks_late=True)
def send_email_task(self: Any, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Phase 10 sending worker entry point.

    CRITICAL INVARIANT: this task never decides whether to send from the
    payload alone. `SendingService.execute` reloads authoritative state from
    PostgreSQL, re-verifies every safety gate, acquires distributed rate
    capacity, and only then invokes a provider adapter.

    `acks_late=True` (a per-task override of the celery_app-wide
    `task_acks_late = False`): safe specifically because idempotency here is
    enforced by the database (the message_attempts_unresolved_idx unique
    partial index), not by Celery's ack timing -- a redelivered task after a
    worker crash is a correctness no-op, never a duplicate send, so late ack
    (only removing the task from the queue after this function returns) is
    the right choice to avoid losing a task to a mid-processing crash.

    No `autoretry_for`: the message state machine (RETRY_SCHEDULED /
    next_retry_at / retry_count) owns retry timing, not Celery -- see
    docs/architecture/WORKERS.md.
    """
    if payload is None:
        raise ValueError("email.send payload must not be empty")

    validated = SendTaskPayload.model_validate(payload)

    settings = Settings.current()
    if not settings.sending_worker_enabled:
        logger.info(
            "sending_worker_enabled=False; Phase 9 placeholder behavior "
            "(no provider call)",
            extra={
                "work_id": validated.work_id,
                "message_id": validated.message_id,
                "workspace_id": validated.workspace_id,
                "dispatch_generation": validated.dispatch_generation,
            },
        )
        return {
            "status": "received",
            "task": "email.send",
            "message_id": validated.message_id,
            "delivery_id": validated.delivery_id,
            "workspace_id": validated.workspace_id,
            "dispatch_generation": validated.dispatch_generation,
        }

    with session_scope(settings) as session:
        service = SendingService(session, settings=settings)
        outcome = service.execute(validated)

    logger.info(
        "email.send task completed",
        extra={
            "message_id": str(outcome.message_id),
            "outcome": outcome.outcome,
            "reason": outcome.reason,
        },
    )
    return {
        "status": outcome.outcome,
        "task": "email.send",
        "message_id": str(outcome.message_id),
        "reason": outcome.reason,
    }
