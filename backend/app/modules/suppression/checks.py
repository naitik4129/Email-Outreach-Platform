from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session


def is_address_suppressed(
    session: Session, workspace_id: UUID, address_id: UUID
) -> bool:
    """Single authoritative workspace-scoped suppression check, reused by
    mailboxes (controlled test send) and campaigns (audience capture) so no
    caller maintains its own copy of this query.

    This checks the workspace-scoped `suppressions` table only (MANUAL,
    UNSUBSCRIBE, HARD_BOUNCE, COMPLAINT, and workspace-level PLATFORM_BLOCK
    rows). It does NOT check the separate global `platform_suppressions`
    table -- callers that must be authoritative for a real send (not just a
    workspace-level UI check) also call `is_platform_suppressed` below.
    """
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


def is_platform_suppressed(session: Session, canonical_address: str) -> bool:
    """Authoritative check against the operator-managed global suppression
    list (`platform_suppressions`), keyed by canonical address rather than
    workspace, per SUPPRESSION.md's separate platform-wide PLATFORM_BLOCK
    dimension. Every real send authorization must check both this and
    `is_address_suppressed` -- neither alone is authoritative.
    """
    row = session.execute(
        text(
            """
            SELECT 1
            FROM public.platform_suppressions
            WHERE canonical_address = :canonical_address
              AND normalization_version = 1
              AND status = 'ACTIVE'
            LIMIT 1
            """
        ),
        {"canonical_address": canonical_address},
    ).first()
    return row is not None
