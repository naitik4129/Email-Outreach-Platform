from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def record_open(session: Session, *, workspace_id: UUID, message_id: UUID) -> bool:
    """Record one open of an outbound message.

    One row per (message, OPENED): repeated opens only bump ``occurrence_count``
    and ``last_occurred_at``. "Opened" analytics count distinct messages;
    total opens is the sum of ``occurrence_count``. Idempotent and safe under
    concurrent requests because the upsert is a single atomic statement.

    Returns False when the message does not exist (deleted or never created):
    the foreign key rejects it and nothing is written.
    """
    now = datetime.now(UTC)
    savepoint = session.begin_nested()
    try:
        session.execute(
            text(
                """
                INSERT INTO public.message_events (
                    id, workspace_id, message_id, kind, source,
                    first_occurred_at, last_occurred_at, occurrence_count
                ) VALUES (
                    :id, :ws, :mid, 'OPENED', 'PIXEL', :now, :now, 1
                )
                ON CONFLICT (workspace_id, message_id, kind) DO UPDATE
                SET occurrence_count = message_events.occurrence_count + 1,
                    last_occurred_at = EXCLUDED.last_occurred_at
                """
            ),
            {
                "id": str(uuid4()),
                "ws": str(workspace_id),
                "mid": str(message_id),
                "now": now,
            },
        )
        savepoint.commit()
    except IntegrityError:
        savepoint.rollback()
        return False
    return True
