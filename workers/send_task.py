from __future__ import annotations

import logging
from typing import Any

from app.modules.scheduler.schemas import SendTaskPayload
from workers.celery_app import celery_app

logger = logging.getLogger(__name__)


@celery_app.task(name="email.send", queue="email.send", bind=True)
def send_email_task(self: Any, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Safe placeholder consumer for email.send queue during Phase 9.

    CRITICAL PHASE 9 INVARIANT:
    This task MUST NOT execute email sending.
    Zero Gmail, zero Microsoft Graph, zero SMTP calls.
    Phase 10 will attach the full rate-limiting and send worker execution pipeline.
    """
    if payload is None:
        raise ValueError("email.send payload must not be empty")

    # Validate minimal envelope contract
    validated = SendTaskPayload.model_validate(payload)

    logger.info(
        "Received email.send work item in Phase 9 placeholder consumer",
        extra={
            "work_id": validated.work_id,
            "delivery_id": validated.delivery_id,
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
