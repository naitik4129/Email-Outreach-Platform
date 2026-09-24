from __future__ import annotations

import json
import logging
import uuid
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.modules.notifications.schemas import (
    NotificationListOut,
    NotificationOut,
    NotificationPreferencesIn,
    NotificationPreferencesOut,
)

logger = logging.getLogger(__name__)


class NotificationService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def list_notifications(
        self,
        workspace_id: UUID,
        user_id: UUID,
        *,
        unread_only: bool = False,
        limit: int = 50,
        offset: int = 0,
    ) -> NotificationListOut:
        """Retrieves paginated in-app notifications for the user in this workspace."""
        filter_clause = "AND read_at IS NULL" if unread_only else ""

        count_sql = f"""
            SELECT
                count(*) AS total,
                count(*) FILTER (WHERE read_at IS NULL) AS unread_count
            FROM notifications
            WHERE workspace_id = :ws_id AND recipient_user_id = :user_id {filter_clause}
        """
        counts = (
            self.db.execute(
                text(count_sql),
                {"ws_id": str(workspace_id), "user_id": str(user_id)},
            )
            .mappings()
            .first()
        )
        total = counts["total"] if counts else 0
        unread_count = counts["unread_count"] if counts else 0

        items_sql = f"""
            SELECT id, workspace_id, recipient_user_id, type, summary,
                   resource_link, read_at, created_at
            FROM notifications
            WHERE workspace_id = :ws_id AND recipient_user_id = :user_id {filter_clause}
            ORDER BY created_at DESC
            LIMIT :limit OFFSET :offset
        """
        rows = (
            self.db.execute(
                text(items_sql),
                {
                    "ws_id": str(workspace_id),
                    "user_id": str(user_id),
                    "limit": limit,
                    "offset": offset,
                },
            )
            .mappings()
            .all()
        )

        items = [NotificationOut(**row) for row in rows]
        return NotificationListOut(items=items, total=total, unread_count=unread_count)

    def get_unread_count(self, workspace_id: UUID, user_id: UUID) -> int:
        """Returns the unread notifications count efficiently."""
        sql = """
            SELECT count(*)
            FROM notifications
            WHERE workspace_id = :ws_id
              AND recipient_user_id = :user_id
              AND read_at IS NULL
        """
        count = self.db.execute(
            text(sql),
            {"ws_id": str(workspace_id), "user_id": str(user_id)},
        ).scalar()
        return int(count or 0)

    def mark_as_read(
        self, workspace_id: UUID, user_id: UUID, notification_id: UUID
    ) -> bool:
        """Marks a single notification as read."""
        sql = """
            UPDATE notifications
            SET read_at = statement_timestamp()
            WHERE id = :id
              AND workspace_id = :ws_id
              AND recipient_user_id = :user_id
              AND read_at IS NULL
        """
        result = self.db.execute(
            text(sql),
            {
                "id": str(notification_id),
                "ws_id": str(workspace_id),
                "user_id": str(user_id),
            },
        )
        return result.rowcount > 0

    def mark_all_as_read(self, workspace_id: UUID, user_id: UUID) -> int:
        """Marks all notifications in this workspace as read for the calling user."""
        sql = """
            UPDATE notifications
            SET read_at = statement_timestamp()
            WHERE workspace_id = :ws_id
              AND recipient_user_id = :user_id
              AND read_at IS NULL
        """
        result = self.db.execute(
            text(sql),
            {"ws_id": str(workspace_id), "user_id": str(user_id)},
        )
        return int(result.rowcount)

    def create_notification(
        self,
        workspace_id: UUID,
        recipient_user_id: UUID,
        notification_type: str,
        source_event_id: UUID,
        summary: str,
        resource_link: str | None = None,
    ) -> UUID | None:
        """Creates an in-app notification idempotently under domain event constraint."""
        membership_id = self.db.execute(
            text(
                """
                SELECT id FROM workspace_memberships
                WHERE workspace_id = :ws_id AND user_id = :user_id AND status = 'ACTIVE'
                """
            ),
            {"ws_id": str(workspace_id), "user_id": str(recipient_user_id)},
        ).scalar()

        if not membership_id:
            return None

        notif_id = uuid.uuid4()
        sql = """
            INSERT INTO notifications (
                id, workspace_id, recipient_user_id, recipient_membership_id,
                source_event_id, type, summary, resource_link
            ) VALUES (
                :id, :ws_id, :recipient_user_id, :membership_id,
                :source_event_id, :type, :summary, :resource_link
            )
            ON CONFLICT (workspace_id, recipient_user_id, source_event_id, type)
            DO NOTHING
        """
        self.db.execute(
            text(sql),
            {
                "id": str(notif_id),
                "ws_id": str(workspace_id),
                "recipient_user_id": str(recipient_user_id),
                "membership_id": str(membership_id),
                "source_event_id": str(source_event_id),
                "type": notification_type,
                "summary": summary,
                "resource_link": resource_link,
            },
        )
        return notif_id

    def get_user_preferences(self, user_id: UUID) -> NotificationPreferencesOut:
        """Retrieves user notification preferences from profile."""
        prefs_raw = self.db.execute(
            text("SELECT preferences FROM profiles WHERE id = :id"),
            {"id": str(user_id)},
        ).scalar()

        if isinstance(prefs_raw, str):
            try:
                prefs_raw = json.loads(prefs_raw)
            except Exception:
                prefs_raw = {}

        if not prefs_raw or not isinstance(prefs_raw, dict):
            return NotificationPreferencesOut()

        notif_prefs = prefs_raw.get("notifications")
        if not notif_prefs or not isinstance(notif_prefs, dict):
            return NotificationPreferencesOut()

        return NotificationPreferencesOut(**notif_prefs)

    def update_user_preferences(
        self,
        user_id: UUID,
        payload: NotificationPreferencesIn,
    ) -> NotificationPreferencesOut:
        """Updates user notification preferences in profile preferences JSONB."""
        current = (
            self.db.execute(
                text("SELECT preferences FROM profiles WHERE id = :id"),
                {"id": str(user_id)},
            ).scalar()
            or {}
        )

        if isinstance(current, str):
            try:
                current = json.loads(current)
            except Exception:
                current = {}

        if not isinstance(current, dict):
            current = {}

        current["notifications"] = payload.model_dump()

        bind = self.db.get_bind()
        cast_sql = (
            ":prefs"
            if (bind and bind.dialect.name == "sqlite")
            else "CAST(:prefs AS jsonb)"
        )
        self.db.execute(
            text(
                f"UPDATE profiles SET preferences = {cast_sql} WHERE id = :id"
            ),
            {"id": str(user_id), "prefs": json.dumps(current)},
        )
        return NotificationPreferencesOut(**payload.model_dump())
