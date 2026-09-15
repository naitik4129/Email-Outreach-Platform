from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from app.core.errors import AppError


def assign_mailbox_for_recipient(
    eligible_mailbox_ids: Sequence[UUID], recipient_ordinal: int
) -> UUID:
    """Deterministic positional round-robin over campaign_mailboxes ordered by
    allocation_position.

    recipient_ordinal is the audience member's stable capture_ordinal
    (1-based): assignment depends only on that stable value, never on chunk
    boundaries or which planner attempt actually inserts the row, so a
    retried/redelivered ENROLL chunk always computes the same mailbox for a
    given recipient. Per CAMPAIGN_ENGINE.md, a disconnected/unhealthy mailbox
    still holds its position -- it does not trigger sender rotation.
    """
    if not eligible_mailbox_ids:
        raise AppError(
            "internal_error",
            "No eligible mailboxes available for assignment",
            status_code=500,
        )
    return eligible_mailbox_ids[(recipient_ordinal - 1) % len(eligible_mailbox_ids)]
