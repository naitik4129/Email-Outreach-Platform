from __future__ import annotations

import logging
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

MAX_USER_AGENT_LENGTH = 200


def record_open(
    session: Session,
    *,
    workspace_id: UUID,
    message_id: UUID,
    qualified: bool,
    user_agent: str | None = None,
) -> bool:
    """Record one pixel request for an outbound message.

    One row per (message, OPENED). Every request bumps ``occurrence_count``
    (total hits, kept as evidence); only requests that look like a person
    bump ``qualified_count`` and set ``qualified_at``, and only those make the
    email "opened" in analytics. Automated fetches (delivery-time scanners,
    prefetchers) are stored but never counted. Idempotent and safe under
    concurrent requests because the upsert is a single atomic statement.

    Returns False when the message does not exist (deleted or never created):
    the foreign key rejects it and nothing is written.
    """
    now = datetime.now(UTC)
    hit = 1 if qualified else 0
    savepoint = session.begin_nested()
    try:
        session.execute(
            text(
                """
                INSERT INTO public.message_events (
                    id, workspace_id, message_id, kind, source,
                    first_occurred_at, last_occurred_at, occurrence_count,
                    qualified_at, qualified_count, automated_count,
                    last_user_agent
                ) VALUES (
                    :id, :ws, :mid, 'OPENED', 'PIXEL', :now, :now, 1,
                    CASE WHEN :hit = 1 THEN CAST(:now AS timestamptz) END,
                    :hit, 1 - :hit, :ua
                )
                ON CONFLICT (workspace_id, message_id, kind) DO UPDATE
                SET occurrence_count = message_events.occurrence_count + 1,
                    last_occurred_at = EXCLUDED.last_occurred_at,
                    qualified_count = message_events.qualified_count + :hit,
                    automated_count = message_events.automated_count + (1 - :hit),
                    qualified_at = COALESCE(
                        message_events.qualified_at, EXCLUDED.qualified_at
                    ),
                    last_user_agent = EXCLUDED.last_user_agent
                """
            ),
            {
                "id": str(uuid4()),
                "ws": str(workspace_id),
                "mid": str(message_id),
                "now": now,
                "hit": hit,
                "ua": (user_agent or "")[:MAX_USER_AGENT_LENGTH] or None,
            },
        )
        savepoint.commit()
    except IntegrityError:
        savepoint.rollback()
        return False
    return True
