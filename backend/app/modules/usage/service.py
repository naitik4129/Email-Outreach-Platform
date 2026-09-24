from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.usage.schemas import DimensionUsage, WorkspaceUsageOut

logger = logging.getLogger(__name__)

DEFAULT_WORKSPACE_LIMITS: dict[str, int] = {
    "sent_messages_today": 1000,
    "active_mailboxes": 10,
    "leads": 5000,
    "active_campaigns": 10,
    "team_members": 10,
}


class UsageService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_workspace_usage(self, workspace_id: UUID) -> WorkspaceUsageOut:
        """Calculates usage from PostgreSQL and compares with limits."""
        ws = (
            self.db.execute(
                text("SELECT id, name, defaults FROM workspaces WHERE id = :id"),
                {"id": str(workspace_id)},
            )
            .mappings()
            .first()
        )
        if not ws:
            raise AppError("not_found", "Workspace not found", status_code=404)

        defaults = ws.get("defaults") or {}
        if isinstance(defaults, str):
            try:
                defaults = json.loads(defaults)
            except Exception:
                defaults = {}
        custom_limits = defaults.get("limits") or {}

        # 1. Sent messages today (UTC calendar day)
        sent_messages = self.db.execute(
            text(
                """
                SELECT count(*)
                FROM messages
                WHERE workspace_id = :ws_id
                  AND status = 'SENT'
                  AND accepted_at >= CURRENT_DATE
                """
            ),
            {"ws_id": str(workspace_id)},
        ).scalar() or 0

        # 2. Active mailboxes
        active_mailboxes = self.db.execute(
            text(
                """
                SELECT count(*)
                FROM mailboxes
                WHERE workspace_id = :ws_id
                  AND status = 'CONNECTED'
                """
            ),
            {"ws_id": str(workspace_id)},
        ).scalar() or 0

        # 3. Leads stored
        leads_count = self.db.execute(
            text(
                """
                SELECT count(*)
                FROM leads
                WHERE workspace_id = :ws_id
                  AND archived_at IS NULL
                """
            ),
            {"ws_id": str(workspace_id)},
        ).scalar() or 0

        # 4. Active campaigns
        active_campaigns = self.db.execute(
            text(
                """
                SELECT count(*)
                FROM campaigns
                WHERE workspace_id = :ws_id
                  AND status IN ('RUNNING', 'SCHEDULED')
                """
            ),
            {"ws_id": str(workspace_id)},
        ).scalar() or 0

        # 5. Team members
        team_members = self.db.execute(
            text(
                """
                SELECT count(*)
                FROM workspace_memberships
                WHERE workspace_id = :ws_id
                  AND status = 'ACTIVE'
                """
            ),
            {"ws_id": str(workspace_id)},
        ).scalar() or 0

        plan_name = defaults.get("plan_name", "Standard")

        dimensions: dict[str, DimensionUsage] = {
            "sent_messages_today": DimensionUsage(
                used=int(sent_messages),
                limit=custom_limits.get(
                    "sent_messages_today",
                    DEFAULT_WORKSPACE_LIMITS["sent_messages_today"],
                ),
                unit="messages",
            ),
            "active_mailboxes": DimensionUsage(
                used=int(active_mailboxes),
                limit=custom_limits.get(
                    "active_mailboxes",
                    DEFAULT_WORKSPACE_LIMITS["active_mailboxes"],
                ),
                unit="mailboxes",
            ),
            "leads": DimensionUsage(
                used=int(leads_count),
                limit=custom_limits.get(
                    "leads",
                    DEFAULT_WORKSPACE_LIMITS["leads"],
                ),
                unit="leads",
            ),
            "active_campaigns": DimensionUsage(
                used=int(active_campaigns),
                limit=custom_limits.get(
                    "active_campaigns",
                    DEFAULT_WORKSPACE_LIMITS["active_campaigns"],
                ),
                unit="campaigns",
            ),
            "team_members": DimensionUsage(
                used=int(team_members),
                limit=custom_limits.get(
                    "team_members",
                    DEFAULT_WORKSPACE_LIMITS["team_members"],
                ),
                unit="members",
            ),
        }

        return WorkspaceUsageOut(
            workspace_id=workspace_id,
            plan_name=plan_name,
            dimensions=dimensions,
            as_of=datetime.now(UTC),
        )

    def check_entitlement(
        self, workspace_id: UUID, dimension: str, delta: int = 1
    ) -> None:
        """Verifies that increasing usage by delta does not exceed workspace quota."""
        usage = self.get_workspace_usage(workspace_id)
        dim = usage.dimensions.get(dimension)
        if not dim or dim.limit is None:
            return

        if dim.used + delta > dim.limit:
            raise AppError(
                "entitlement_exceeded",
                f"Workspace quota exceeded for {dimension}: "
                f"limit is {dim.limit}, current usage is {dim.used}",
                status_code=403,
            )
