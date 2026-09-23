from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from sqlalchemy import RowMapping, text
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


def _safe_set_role(session: Session, role_name: str) -> None:
    """Attempt SET LOCAL ROLE if running against PostgreSQL; ignore in SQLite."""
    bind = session.get_bind()
    if bind and getattr(bind.dialect, "name", "") != "sqlite":
        session.execute(text(f"RESET ROLE; SET LOCAL ROLE {role_name}"))


def _safe_set_workspace(session: Session, workspace_id: UUID | None) -> None:
    """Set transaction-local app.workspace_id if running against PostgreSQL."""
    bind = session.get_bind()
    if bind and getattr(bind.dialect, "name", "") != "sqlite":
        if workspace_id is not None:
            session.execute(
                text("SELECT set_config('app.workspace_id', :ws, true)"),
                {"ws": str(workspace_id)},
            )
        else:
            session.execute(text("SELECT set_config('app.workspace_id', '', true)"))


class OutboxRepository:
    """Outbox repository handling durable work leasing, queue publication, and retries.

    Follows EVENT_SYSTEM.md and QUEUES.md:
    - Unpublished deliveries discovered globally using outbox_deliveries_unpublished_idx.
    - Delivery leasing and state transitions occur under workspace-scoped transactions.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def find_pending_deliveries(
        self,
        *,
        consumer: str = "worker-send",
        limit: int = 50,
        authoritative_now: datetime | None = None,
    ) -> list[RowMapping]:
        """Find deliveries in PENDING or RETRY state whose next_attempt_at <= now."""
        _safe_set_role(self.session, "app_scheduler")
        _safe_set_workspace(self.session, None)

        bind = self.session.get_bind()
        is_sqlite = bind and getattr(bind.dialect, "name", "") == "sqlite"

        now_expr = ":now" if authoritative_now else (
            "CURRENT_TIMESTAMP" if is_sqlite else "pg_catalog.transaction_timestamp()"
        )

        query = f"""
            SELECT d.id AS delivery_id, d.workspace_id, d.work_id, d.consumer,
                   d.state, d.lease_generation, d.attempts,
                   w.kind, w.resource_id, w.resource_type, w.semantic_key, w.correlation_id
            FROM outbox_deliveries d
            JOIN outbox_work w
              ON w.workspace_id = d.workspace_id AND w.id = d.work_id
            WHERE d.consumer = :consumer
              AND d.state IN ('PENDING', 'RETRY')
              AND d.next_attempt_at <= {now_expr}
              AND NOT w.superseded
            ORDER BY d.next_attempt_at, d.id
            LIMIT :limit
        """
        params: dict[str, Any] = {"consumer": consumer, "limit": limit}
        if authoritative_now:
            params["now"] = authoritative_now

        rows = self.session.execute(text(query), params).mappings().all()
        return list(rows)

    def lease_delivery(
        self,
        *,
        delivery_id: UUID,
        workspace_id: UUID,
        lease_owner: str,
        lease_seconds: int = 60,
        authoritative_now: datetime | None = None,
    ) -> RowMapping | None:
        """Atomically claim/lease one delivery row for publication."""
        _safe_set_role(self.session, "app_scheduler")
        _safe_set_workspace(self.session, workspace_id)

        bind = self.session.get_bind()
        is_sqlite = bind and getattr(bind.dialect, "name", "") == "sqlite"

        now_expr = ":now" if authoritative_now else (
            "CURRENT_TIMESTAMP" if is_sqlite else "pg_catalog.transaction_timestamp()"
        )
        lease_expiry_expr = (
            f"datetime({now_expr}, '+{lease_seconds} seconds')"
            if is_sqlite
            else f"{now_expr} + make_interval(secs => :lease_seconds)"
        )

        query = f"""
            UPDATE outbox_deliveries
            SET state = 'LEASED',
                lease_owner = :lease_owner,
                lease_generation = lease_generation + 1,
                lease_expires_at = {lease_expiry_expr}
            WHERE workspace_id = :ws AND id = :delivery_id
              AND state IN ('PENDING', 'RETRY')
            RETURNING id, workspace_id, work_id, lease_generation, lease_expires_at
        """
        params: dict[str, Any] = {
            "ws": str(workspace_id),
            "delivery_id": str(delivery_id),
            "lease_owner": lease_owner,
            "lease_seconds": lease_seconds,
        }
        if authoritative_now:
            params["now"] = authoritative_now

        row = self.session.execute(text(query), params).mappings().first()
        return row

    def mark_published(
        self,
        *,
        delivery_id: UUID,
        workspace_id: UUID,
        lease_owner: str,
        lease_generation: int,
        authoritative_now: datetime | None = None,
    ) -> bool:
        """Mark delivery PUBLISHED on successful queue handoff."""
        _safe_set_role(self.session, "app_scheduler")
        _safe_set_workspace(self.session, workspace_id)

        bind = self.session.get_bind()
        is_sqlite = bind and getattr(bind.dialect, "name", "") == "sqlite"

        now_expr = ":now" if authoritative_now else (
            "CURRENT_TIMESTAMP" if is_sqlite else "pg_catalog.transaction_timestamp()"
        )

        query = f"""
            UPDATE outbox_deliveries
            SET state = 'PUBLISHED',
                attempts = attempts + 1,
                published_at = {now_expr},
                lease_owner = NULL,
                lease_expires_at = NULL
            WHERE workspace_id = :ws AND id = :delivery_id
              AND lease_owner = :lease_owner
              AND lease_generation = :lease_generation
        """
        params: dict[str, Any] = {
            "ws": str(workspace_id),
            "delivery_id": str(delivery_id),
            "lease_owner": lease_owner,
            "lease_generation": lease_generation,
        }
        if authoritative_now:
            params["now"] = authoritative_now

        result = self.session.execute(text(query), params)
        return (result.rowcount or 0) > 0

    def mark_retry(
        self,
        *,
        delivery_id: UUID,
        workspace_id: UUID,
        lease_owner: str,
        lease_generation: int,
        backoff_seconds: float,
        safe_error: str,
        authoritative_now: datetime | None = None,
    ) -> bool:
        """Mark delivery RETRY with exponential backoff on publication failure."""
        _safe_set_role(self.session, "app_scheduler")
        _safe_set_workspace(self.session, workspace_id)

        bind = self.session.get_bind()
        is_sqlite = bind and getattr(bind.dialect, "name", "") == "sqlite"

        now_expr = ":now" if authoritative_now else (
            "CURRENT_TIMESTAMP" if is_sqlite else "pg_catalog.transaction_timestamp()"
        )
        backoff_expr = (
            f"datetime({now_expr}, '+{int(backoff_seconds)} seconds')"
            if is_sqlite
            else f"{now_expr} + make_interval(secs => :backoff_seconds)"
        )

        query = f"""
            UPDATE outbox_deliveries
            SET state = 'RETRY',
                attempts = attempts + 1,
                next_attempt_at = {backoff_expr},
                safe_error = :safe_error,
                lease_owner = NULL,
                lease_expires_at = NULL
            WHERE workspace_id = :ws AND id = :delivery_id
              AND lease_owner = :lease_owner
              AND lease_generation = :lease_generation
        """
        params: dict[str, Any] = {
            "ws": str(workspace_id),
            "delivery_id": str(delivery_id),
            "lease_owner": lease_owner,
            "lease_generation": lease_generation,
            "backoff_seconds": backoff_seconds,
            "safe_error": safe_error[:500],
        }
        if authoritative_now:
            params["now"] = authoritative_now

        result = self.session.execute(text(query), params)
        return (result.rowcount or 0) > 0

    def recover_stale_leases(
        self,
        *,
        limit: int = 50,
        authoritative_now: datetime | None = None,
    ) -> int:
        """Reclaim deliveries stuck in LEASED past lease_expires_at back to RETRY."""
        _safe_set_role(self.session, "app_scheduler")
        _safe_set_workspace(self.session, None)

        bind = self.session.get_bind()
        is_sqlite = bind and getattr(bind.dialect, "name", "") == "sqlite"

        now_expr = ":now" if authoritative_now else (
            "CURRENT_TIMESTAMP" if is_sqlite else "pg_catalog.transaction_timestamp()"
        )

        # 1. Discover stale leases globally
        find_query = f"""
            SELECT id, workspace_id
            FROM outbox_deliveries
            WHERE state = 'LEASED'
              AND lease_expires_at <= {now_expr}
            LIMIT :limit
        """
        params: dict[str, Any] = {"limit": limit}
        if authoritative_now:
            params["now"] = authoritative_now

        stale_rows = self.session.execute(text(find_query), params).mappings().all()
        if not stale_rows:
            return 0

        # 2. Reclaim each stale delivery within its workspace context
        recovered = 0
        for row in stale_rows:
            ws_id = row["workspace_id"]
            delivery_id = row["id"]
            _safe_set_workspace(self.session, ws_id)
            update_query = f"""
                UPDATE outbox_deliveries
                SET state = 'RETRY',
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    next_attempt_at = {now_expr}
                WHERE workspace_id = :ws AND id = :delivery_id
                  AND state = 'LEASED'
            """
            upd_params: dict[str, Any] = {
                "ws": str(ws_id),
                "delivery_id": str(delivery_id),
            }
            if authoritative_now:
                upd_params["now"] = authoritative_now
            res = self.session.execute(text(update_query), upd_params)
            recovered += (res.rowcount or 0)

        return recovered

    def get_pending_outbox_backlog_count(self) -> int:
        """Count pending/retry deliveries in the outbox."""
        _safe_set_role(self.session, "app_scheduler")
        _safe_set_workspace(self.session, None)

        query = """
            SELECT COUNT(*) FROM outbox_deliveries
            WHERE state IN ('PENDING', 'RETRY')
        """
        count = self.session.execute(text(query)).scalar()
        return count or 0
