from __future__ import annotations

import json
import logging
import uuid
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.admin.schemas import AdminWorkspaceOut, PlatformAuditLogOut

logger = logging.getLogger(__name__)


class AdminService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def list_workspaces(self) -> list[AdminWorkspaceOut]:
        """Lists workspaces across the platform with operational metrics."""
        sql = """
            SELECT
                w.id,
                w.name,
                w.status,
                w.sending_restriction_reason,
                w.pending_safety_count,
                w.created_at,
                (
                    SELECT count(*)
                    FROM workspace_memberships m
                    WHERE m.workspace_id = w.id AND m.status = 'ACTIVE'
                ) AS member_count,
                (
                    SELECT count(*)
                    FROM mailboxes mb
                    WHERE mb.workspace_id = w.id AND mb.status = 'CONNECTED'
                ) AS mailbox_count
            FROM workspaces w
            ORDER BY w.created_at DESC
        """
        rows = self.db.execute(text(sql)).mappings().all()
        return [AdminWorkspaceOut(**row) for row in rows]

    def _jsonb_cast(self, param: str) -> str:
        bind = self.db.get_bind()
        if bind and bind.dialect.name == "sqlite":
            return f":{param}"
        return f"CAST(:{param} AS jsonb)"

    @property
    def _for_update(self) -> str:
        bind = self.db.get_bind()
        if bind and bind.dialect.name == "sqlite":
            return ""
        return "FOR UPDATE"

    def restrict_workspace(
        self,
        workspace_id: UUID,
        reason: str,
        operator_id: UUID | None = None,
    ) -> bool:
        """Applies a sending restriction / suspension to a workspace."""
        ws_sql = (
            f"SELECT id, status FROM workspaces "
            f"WHERE id = :id {self._for_update}"
        )
        ws = (
            self.db.execute(text(ws_sql), {"id": str(workspace_id)})
            .mappings()
            .first()
        )
        if not ws:
            raise AppError("not_found", "Workspace not found", status_code=404)

        self.db.execute(
            text(
                """
                UPDATE workspaces
                SET status = 'RESTRICTED',
                    sending_restriction_reason = :reason,
                    sending_restriction_version = sending_restriction_version + 1,
                    updated_at = statement_timestamp()
                WHERE id = :id
                """
            ),
            {"id": str(workspace_id), "reason": reason},
        )

        event_id = uuid.uuid4()
        b_cast = self._jsonb_cast("before")
        a_cast = self._jsonb_cast("after")
        self.db.execute(
            text(
                f"""
                INSERT INTO platform_audit_events (
                    id, actor_kind, actor_id, action, target_type,
                    target_id, before_state, after_state, reason, recorded_at
                )
                VALUES (
                    :id, 'OPERATOR', :actor_id, 'workspace.restrict', 'workspace',
                    :target_id, {b_cast}, {a_cast}, :reason, statement_timestamp()
                )
                """
            ),
            {
                "id": str(event_id),
                "actor_id": str(operator_id) if operator_id else None,
                "target_id": str(workspace_id),
                "before": json.dumps({"status": ws["status"]}),
                "after": json.dumps({"status": "RESTRICTED"}),
                "reason": reason,
            },
        )
        return True

    def restore_workspace(
        self,
        workspace_id: UUID,
        operator_id: UUID | None = None,
    ) -> bool:
        """Restores a restricted workspace to ACTIVE status."""
        ws_sql = (
            f"SELECT id, status FROM workspaces "
            f"WHERE id = :id {self._for_update}"
        )
        ws = (
            self.db.execute(text(ws_sql), {"id": str(workspace_id)})
            .mappings()
            .first()
        )
        if not ws:
            raise AppError("not_found", "Workspace not found", status_code=404)

        self.db.execute(
            text(
                """
                UPDATE workspaces
                SET status = 'ACTIVE',
                    sending_restriction_reason = NULL,
                    sending_restriction_version = sending_restriction_version + 1,
                    updated_at = statement_timestamp()
                WHERE id = :id
                """
            ),
            {"id": str(workspace_id)},
        )

        event_id = uuid.uuid4()
        b_cast = self._jsonb_cast("before")
        a_cast = self._jsonb_cast("after")
        self.db.execute(
            text(
                f"""
                INSERT INTO platform_audit_events (
                    id, actor_kind, actor_id, action, target_type,
                    target_id, before_state, after_state, reason, recorded_at
                )
                VALUES (
                    :id, 'OPERATOR', :actor_id, 'workspace.restore', 'workspace',
                    :target_id, {b_cast}, {a_cast},
                    'Restored by operator', statement_timestamp()
                )
                """
            ),
            {
                "id": str(event_id),
                "actor_id": str(operator_id) if operator_id else None,
                "target_id": str(workspace_id),
                "before": json.dumps({"status": ws["status"]}),
                "after": json.dumps({"status": "ACTIVE"}),
            },
        )
        return True

    def list_platform_audit_logs(
        self, limit: int = 50, offset: int = 0
    ) -> list[PlatformAuditLogOut]:
        """Lists platform audit events."""
        sql = """
            SELECT
                id, actor_kind, actor_id, action, target_type,
                target_id, before_state, after_state, reason, recorded_at
            FROM platform_audit_events
            ORDER BY recorded_at DESC
            LIMIT :limit OFFSET :offset
        """
        rows = (
            self.db.execute(text(sql), {"limit": limit, "offset": offset})
            .mappings()
            .all()
        )
        logs = []
        for row in rows:
            d = dict(row)
            if isinstance(d.get("before_state"), str):
                try:
                    d["before_state"] = json.loads(d["before_state"])
                except Exception:
                    pass
            if isinstance(d.get("after_state"), str):
                try:
                    d["after_state"] = json.loads(d["after_state"])
                except Exception:
                    pass
            logs.append(PlatformAuditLogOut(**d))
        return logs
