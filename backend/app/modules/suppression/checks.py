from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session


def is_address_suppressed(
    session: Session, workspace_id: UUID, address_id: UUID
) -> bool:
    """Single authoritative suppression check, reused by mailboxes (controlled
    test send) and campaigns (audience capture) so no caller maintains its own
    copy of this query."""
    row = session.execute(
        text(
            """
            SELECT 1
            FROM public.suppressions
            WHERE workspace_id = :workspace_id
              AND address_id = :address_id
              AND status = 'ACTIVE'
            LIMIT 1
            """
        ),
        {"workspace_id": str(workspace_id), "address_id": str(address_id)},
    ).first()
    return row is not None
