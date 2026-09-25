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


def enter_worker_scope(session: Session, *, workspace_id: UUID, role_name: str) -> None:
    """Bind a worker transaction to one workspace and one least-privilege role.

    Both the GUC and SET LOCAL ROLE are transaction-scoped, so a worker that
    commits or rolls back must call this again before its next statement.
    The role is a fixed constant supplied by the worker, never task input.
    SQLite (unit tests) has no roles.
    """
    set_transaction_context(session, workspace_id=workspace_id)
    bind = session.get_bind()
    if bind is not None and getattr(bind.dialect, "name", "") != "sqlite":
        session.execute(text(f"RESET ROLE; SET LOCAL ROLE {role_name}"))

