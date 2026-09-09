from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session


def set_transaction_context(
    session: Session,
    *,
    user_id: UUID | None = None,
    workspace_id: UUID | None = None,
) -> None:
    """Bind future RLS context to the current transaction only.

    Phase 0 has no authenticated principal yet, so callers must supply real
    identifiers explicitly. The third `set_config` argument keeps values local to
    the transaction, which prevents pooled connection context leakage.
    """
    if user_id is not None:
        session.execute(
            text("select set_config(:name, :value, true)"),
            {"name": "app.user_id", "value": str(user_id)},
        )
    if workspace_id is not None:
        session.execute(
            text("select set_config(:name, :value, true)"),
            {"name": "app.workspace_id", "value": str(workspace_id)},
        )

