from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import WorkspaceContext
from app.core.errors import AppError
from app.schemas.leads import LeadActivityItem, LeadActivityOut

_BODY_PREVIEW_CHARS = 300


def _as_utc(value: Any) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return cast(datetime, value)


def _sender(participants: Any) -> str | None:
    if isinstance(participants, str):
        try:
            participants = json.loads(participants)
        except ValueError:
            return None
    sender = participants.get("from") if isinstance(participants, dict) else None
    if isinstance(sender, dict):
        return str(sender.get("address") or sender.get("email") or "") or None
    return None


class LeadActivityService:
    """One chronological history of what happened to a lead's emails.

    Built from the same tables the analytics use (messages, message_events,
    inbound messages linked to the emails they answer), scoped to the caller's
    workspace. A lead in another workspace is indistinguishable from a missing
    one.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_activity(
        self, context: WorkspaceContext, lead_id: UUID, *, limit: int = 100
    ) -> LeadActivityOut:
        ws = str(context.workspace_id)
        exists = self.session.execute(
            text("SELECT 1 FROM public.leads WHERE workspace_id = :ws AND id = :lid"),
            {"ws": ws, "lid": str(lead_id)},
        ).first()
        if exists is None:
            raise AppError("not_found", "Lead not found", status_code=404)

        params = {"ws": ws, "lid": str(lead_id)}
        # Every email of this lead, with the campaign and sequence step it was.
        base_from = """
            FROM public.messages m
            JOIN public.campaign_enrollments e
              ON e.workspace_id = m.workspace_id AND e.id = m.enrollment_id
            LEFT JOIN public.campaigns camp
              ON camp.workspace_id = m.workspace_id AND camp.id = m.campaign_id
            LEFT JOIN public.sequence_steps st
              ON st.workspace_id = m.workspace_id AND st.id = m.step_id
        """
        items: list[LeadActivityItem] = []

        for r in self._rows(
            f"""
            SELECT m.id AS message_id, m.accepted_at AS at,
                   m.content_subject AS subject, m.campaign_id,
                   camp.name AS campaign_name, st.position AS step_position
            {base_from}
            WHERE e.workspace_id = :ws AND e.lead_id = :lid
              AND m.status = 'SENT' AND m.accepted_at IS NOT NULL
            """,
            params,
        ):
            items.append(
                LeadActivityItem(
                    kind="EMAIL_SENT",
                    occurred_at=_as_utc(r["at"]),
                    message_id=UUID(str(r["message_id"])),
                    subject=r["subject"],
                    campaign_id=_uuid(r["campaign_id"]),
                    campaign_name=r["campaign_name"],
                    sequence_step_position=r["step_position"],
                )
            )

        for r in self._rows(
            """
            SELECT me.kind, me.bounce_type, me.occurrence_count,
                   me.first_occurred_at AS at, m.id AS message_id,
                   m.content_subject AS subject, m.campaign_id,
                   camp.name AS campaign_name, st.position AS step_position
            FROM public.message_events me
            JOIN public.messages m
              ON m.workspace_id = me.workspace_id AND m.id = me.message_id
            JOIN public.campaign_enrollments e
              ON e.workspace_id = m.workspace_id AND e.id = m.enrollment_id
            LEFT JOIN public.campaigns camp
              ON camp.workspace_id = m.workspace_id AND camp.id = m.campaign_id
            LEFT JOIN public.sequence_steps st
              ON st.workspace_id = m.workspace_id AND st.id = m.step_id
            WHERE e.workspace_id = :ws AND e.lead_id = :lid
            """,
            params,
        ):
            opened = r["kind"] == "OPENED"
            items.append(
                LeadActivityItem(
                    kind="EMAIL_OPENED" if opened else "EMAIL_BOUNCED",
                    occurred_at=_as_utc(r["at"]),
                    message_id=UUID(str(r["message_id"])),
                    subject=r["subject"],
                    campaign_id=_uuid(r["campaign_id"]),
                    campaign_name=r["campaign_name"],
                    sequence_step_position=r["step_position"],
                    occurrence_count=int(r["occurrence_count"]) if opened else None,
                    bounce_type=None if opened else r["bounce_type"],
                )
            )

        for r in self._rows(
            """
            SELECT im.id AS inbound_id, im.received_at AS at, im.subject,
                   im.content_text, im.participants, im.classification,
                   im.conversation_id, l.campaign_id,
                   camp.name AS campaign_name, st.position AS step_position
            FROM public.inbound_outreach_links l
            JOIN public.inbound_messages im
              ON im.workspace_id = l.workspace_id AND im.id = l.inbound_message_id
            JOIN public.campaign_enrollments e
              ON e.workspace_id = l.workspace_id AND e.id = l.enrollment_id
            JOIN public.messages om
              ON om.workspace_id = l.workspace_id AND om.id = l.outbound_message_id
            LEFT JOIN public.campaigns camp
              ON camp.workspace_id = l.workspace_id AND camp.id = l.campaign_id
            LEFT JOIN public.sequence_steps st
              ON st.workspace_id = om.workspace_id AND st.id = om.step_id
            WHERE e.workspace_id = :ws AND e.lead_id = :lid AND l.status = 'CONFIRMED'
            """,
            params,
        ):
            classification = r["classification"] or "HUMAN_REPLY"
            body = (r["content_text"] or "").strip()
            items.append(
                LeadActivityItem(
                    kind=(
                        "REPLY_RECEIVED"
                        if classification == "HUMAN_REPLY"
                        else "AUTO_REPLY_RECEIVED"
                    ),
                    occurred_at=_as_utc(r["at"]),
                    message_id=UUID(str(r["inbound_id"])),
                    subject=r["subject"],
                    campaign_id=_uuid(r["campaign_id"]),
                    campaign_name=r["campaign_name"],
                    sequence_step_position=r["step_position"],
                    conversation_id=_uuid(r["conversation_id"]),
                    sender_email=_sender(r["participants"]),
                    body_preview=body[:_BODY_PREVIEW_CHARS] or None,
                )
            )

        items.sort(key=lambda i: (i.occurred_at, i.kind), reverse=True)
        return LeadActivityOut(lead_id=lead_id, items=items[:limit])

    def _rows(self, sql: str, params: Mapping[str, Any]) -> list[Mapping[str, Any]]:
        result = self.session.execute(text(sql), dict(params)).mappings().all()
        return [dict(row) for row in result]


def _uuid(value: Any) -> UUID | None:
    return UUID(str(value)) if value else None
