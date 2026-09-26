from __future__ import annotations

import base64
import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.inbox.schemas import (
    ConversationDetail,
    ConversationFilter,
    ConversationListItem,
    ConversationPage,
    MailboxSyncStatusItem,
    MessageDirection,
    MessageThreadItem,
)
from app.modules.templates.sanitizer import sanitize_html_preview

logger = logging.getLogger(__name__)


def _safe_set_role(session: Session, role_name: str) -> None:
    bind = session.get_bind()
    if bind and getattr(bind.dialect, "name", "") != "sqlite":
        session.execute(text(f"RESET ROLE; SET LOCAL ROLE {role_name}"))


def _safe_set_workspace(session: Session, workspace_id: UUID | None) -> None:
    bind = session.get_bind()
    if bind and getattr(bind.dialect, "name", "") != "sqlite":
        if workspace_id is not None:
            session.execute(
                text("SELECT set_config('app.workspace_id', :ws, true)"),
                {"ws": str(workspace_id)},
            )
        else:
            session.execute(text("SELECT set_config('app.workspace_id', '', true)"))


def encode_inbox_cursor(latest_activity_at: datetime, conv_id: UUID) -> str:
    """Encode (latest_activity_at, id) into an opaque URL-safe base64 cursor."""
    ts_str = latest_activity_at.isoformat()
    payload = json.dumps({"t": ts_str, "id": str(conv_id)}, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def decode_inbox_cursor(cursor: str | None) -> tuple[datetime, UUID] | None:
    """Decode opaque cursor into (latest_activity_at, conversation_id)."""
    if not cursor:
        return None
    try:
        padded = cursor + ("=" * (-len(cursor) % 4))
        raw = json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        ts = datetime.fromisoformat(raw["t"])
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        conv_id = UUID(str(raw["id"]))
        return ts, conv_id
    except Exception as exc:
        raise AppError("validation_error", "Cursor is invalid", status_code=422) from exc


def _safe_parse_json(val: Any) -> dict[str, Any]:
    if isinstance(val, dict):
        return val
    if isinstance(val, str):
        try:
            return json.loads(val)
        except Exception:
            return {}
    return {}


def _address(entry: Any) -> str:
    """Address of a stored participant. The reply pipeline stores
    {"address": ..., "name": ...}; older fixtures used "email"."""
    if isinstance(entry, dict):
        return str(entry.get("address") or entry.get("email") or "")
    if isinstance(entry, str):
        return entry
    return ""


# The campaign of a conversation is never written to conversations, so it is
# derived from the confirmed outreach links of its inbound messages.
_CONVERSATION_CAMPAIGN = (
    "COALESCE(c.campaign_summary_id, ("
    "SELECT l.campaign_id FROM public.inbound_outreach_links l "
    "JOIN public.inbound_messages lim "
    "ON lim.workspace_id = l.workspace_id AND lim.id = l.inbound_message_id "
    "WHERE l.workspace_id = c.workspace_id AND lim.conversation_id = c.id "
    "AND l.status = 'CONFIRMED' ORDER BY l.matched_at DESC LIMIT 1))"
)


class InboxRepository:
    """Repository for workspace-isolated unified inbox queries and conversation operations."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def _is_sqlite(self) -> bool:
        bind = self.session.get_bind()
        return bool(bind and getattr(bind.dialect, "name", "") == "sqlite")

    def count_unread(self, workspace_id: UUID) -> int:
        """Count unread non-archived conversations in the workspace."""
        _safe_set_role(self.session, "app_api")
        _safe_set_workspace(self.session, workspace_id)

        query = text(
            """
            SELECT COUNT(*)
            FROM public.conversations
            WHERE workspace_id = :ws
              AND archived_at IS NULL
              AND (read_at IS NULL OR read_at < latest_activity_at)
            """
        )
        val = self.session.execute(query, {"ws": str(workspace_id)}).scalar()
        return int(val or 0)

    def list_conversations(
        self,
        *,
        workspace_id: UUID,
        filter_mode: ConversationFilter = ConversationFilter.ALL,
        mailbox_id: UUID | None = None,
        campaign_id: UUID | None = None,
        search_query: str | None = None,
        cursor: str | None = None,
        limit: int = 25,
    ) -> ConversationPage:
        """Retrieve paginated conversations with server-side filters and search."""
        _safe_set_role(self.session, "app_api")
        _safe_set_workspace(self.session, workspace_id)

        decoded_cursor = decode_inbox_cursor(cursor)

        where_clauses = ["c.workspace_id = :ws"]
        params: dict[str, Any] = {"ws": str(workspace_id)}

        # Filter mode
        if filter_mode == ConversationFilter.ARCHIVED:
            where_clauses.append("c.archived_at IS NOT NULL")
        else:
            where_clauses.append("c.archived_at IS NULL")

        if filter_mode == ConversationFilter.UNREAD:
            where_clauses.append("(c.read_at IS NULL OR c.read_at < c.latest_activity_at)")
        elif filter_mode == ConversationFilter.READ:
            where_clauses.append("(c.read_at IS NOT NULL AND c.read_at >= c.latest_activity_at)")
        elif filter_mode == ConversationFilter.REPLIED:
            where_clauses.append(
                """
                EXISTS (
                    SELECT 1 FROM public.inbound_messages im
                    WHERE im.workspace_id = c.workspace_id AND im.conversation_id = c.id
                )
                """
            )

        # Mailbox filter
        if mailbox_id is not None:
            where_clauses.append("c.mailbox_id = :mbid")
            params["mbid"] = str(mailbox_id)

        # Campaign filter
        if campaign_id is not None:
            where_clauses.append(f"{_CONVERSATION_CAMPAIGN} = :campid")
            params["campid"] = str(campaign_id)

        # Search query
        if search_query and search_query.strip():
            sq = f"%{search_query.strip()}%"
            params["sq"] = sq
            where_clauses.append(
                f"""
                (
                    EXISTS (
                        SELECT 1 FROM public.inbound_messages im_s
                        WHERE im_s.workspace_id = c.workspace_id AND im_s.conversation_id = c.id
                          AND (
                              im_s.subject LIKE :sq
                              OR CAST(im_s.participants AS TEXT) LIKE :sq
                              OR im_s.content_text LIKE :sq
                          )
                    )
                    OR EXISTS (
                        SELECT 1 FROM public.messages m_s
                        WHERE m_s.workspace_id = c.workspace_id AND m_s.conversation_id = c.id
                          AND (
                              m_s.content_subject LIKE :sq
                              OR m_s.frozen_destination LIKE :sq
                          )
                    )
                    OR EXISTS (
                        SELECT 1 FROM public.campaigns camp_s
                        WHERE camp_s.workspace_id = c.workspace_id AND camp_s.id = {_CONVERSATION_CAMPAIGN}
                          AND camp_s.name LIKE :sq
                    )
                )
                """
            )

        # Cursor pagination
        if decoded_cursor is not None:
            cursor_ts, cursor_id = decoded_cursor
            params["cursor_ts"] = cursor_ts
            params["cursor_id"] = str(cursor_id)
            where_clauses.append(
                "(c.latest_activity_at < :cursor_ts OR (c.latest_activity_at = :cursor_ts AND c.id < :cursor_id))"
            )

        where_sql = " AND ".join(where_clauses)
        params["fetch_limit"] = limit + 1

        sql = f"""
            SELECT c.id, c.workspace_id, c.mailbox_id,
                   {_CONVERSATION_CAMPAIGN} AS campaign_summary_id,
                   c.created_at, c.latest_activity_at, c.archived_at, c.read_at,
                   mb.original_address AS mailbox_address, mb.provider AS mailbox_provider,
                   camp.name AS campaign_name
            FROM public.conversations c
            LEFT JOIN public.mailboxes mb ON mb.workspace_id = c.workspace_id AND mb.id = c.mailbox_id
            LEFT JOIN public.campaigns camp ON camp.workspace_id = c.workspace_id AND camp.id = {_CONVERSATION_CAMPAIGN}
            WHERE {where_sql}
            ORDER BY c.latest_activity_at DESC, c.id DESC
            LIMIT :fetch_limit
        """

        rows = self.session.execute(text(sql), params).mappings().all()

        has_more = len(rows) > limit
        page_rows = rows[:limit]

        conv_items: list[ConversationListItem] = []
        for r in page_rows:
            cid = UUID(str(r["id"]))
            latest_act = r["latest_activity_at"]
            if isinstance(latest_act, str):
                latest_act = datetime.fromisoformat(latest_act)
            if latest_act.tzinfo is None:
                latest_act = latest_act.replace(tzinfo=UTC)

            read_at = r["read_at"]
            if read_at is not None:
                if isinstance(read_at, str):
                    read_at = datetime.fromisoformat(read_at)
                if read_at.tzinfo is None:
                    read_at = read_at.replace(tzinfo=UTC)

            archived_at = r["archived_at"]
            if archived_at is not None:
                if isinstance(archived_at, str):
                    archived_at = datetime.fromisoformat(archived_at)
                if archived_at.tzinfo is None:
                    archived_at = archived_at.replace(tzinfo=UTC)

            is_read = bool(read_at is not None and read_at >= latest_act)

            # Retrieve conversation snippet, participant, and reply status
            snippet_info = self._get_conversation_preview(workspace_id, cid)

            conv_items.append(
                ConversationListItem(
                    id=cid,
                    mailbox_id=UUID(str(r["mailbox_id"])),
                    mailbox_address=str(r["mailbox_address"] or ""),
                    mailbox_provider=str(r["mailbox_provider"] or ""),
                    campaign_id=UUID(str(r["campaign_summary_id"])) if r["campaign_summary_id"] else None,
                    campaign_name=str(r["campaign_name"]) if r["campaign_name"] else None,
                    subject=snippet_info["subject"],
                    snippet=snippet_info["snippet"],
                    latest_activity_at=latest_act,
                    is_read=is_read,
                    read_at=read_at,
                    archived_at=archived_at,
                    participant_email=snippet_info["participant_email"],
                    participant_name=snippet_info["participant_name"],
                    reply_status=snippet_info["reply_status"],
                    message_count=snippet_info["message_count"],
                )
            )

        next_cursor = None
        if has_more and conv_items:
            last_item = conv_items[-1]
            next_cursor = encode_inbox_cursor(last_item.latest_activity_at, last_item.id)

        unread_count = self.count_unread(workspace_id)

        return ConversationPage(
            items=conv_items,
            next_cursor=next_cursor,
            has_more=has_more,
            unread_count=unread_count,
        )

    def _get_conversation_preview(self, workspace_id: UUID, conversation_id: UUID) -> dict[str, Any]:
        """Fetch latest message snippet, participant, and reply state for list item."""
        # 1. Latest inbound message
        inbound_sql = text(
            """
            SELECT id, subject, content_text, participants, association_status, received_at
            FROM public.inbound_messages
            WHERE workspace_id = :ws AND conversation_id = :cid
            ORDER BY received_at DESC, id DESC
            LIMIT 1
            """
        )
        inbound_row = self.session.execute(
            inbound_sql, {"ws": str(workspace_id), "cid": str(conversation_id)}
        ).mappings().first()

        # 2. Count messages
        inbound_count_sql = text(
            "SELECT COUNT(*) FROM public.inbound_messages WHERE workspace_id = :ws AND conversation_id = :cid"
        )
        inbound_count = int(self.session.execute(
            inbound_count_sql, {"ws": str(workspace_id), "cid": str(conversation_id)}
        ).scalar() or 0)

        outbound_count_sql = text(
            """
            SELECT COUNT(*) FROM public.messages
            WHERE workspace_id = :ws
              AND (
                  conversation_id = :cid
                  OR id IN (
                      SELECT outbound_message_id FROM public.inbound_outreach_links
                      WHERE workspace_id = :ws AND inbound_message_id IN (
                          SELECT id FROM public.inbound_messages
                          WHERE workspace_id = :ws AND conversation_id = :cid
                      )
                  )
              )
            """
        )
        outbound_count = int(self.session.execute(
            outbound_count_sql, {"ws": str(workspace_id), "cid": str(conversation_id)}
        ).scalar() or 0)

        total_count = max(1, inbound_count + outbound_count)

        # 3. Latest outbound message
        outbound_sql = text(
            """
            SELECT id, content_subject, frozen_destination, frozen_sender_name, created_at
            FROM public.messages
            WHERE workspace_id = :ws
              AND (
                  conversation_id = :cid
                  OR id IN (
                      SELECT outbound_message_id FROM public.inbound_outreach_links
                      WHERE workspace_id = :ws AND inbound_message_id IN (
                          SELECT id FROM public.inbound_messages
                          WHERE workspace_id = :ws AND conversation_id = :cid
                      )
                  )
              )
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """
        )
        outbound_row = self.session.execute(
            outbound_sql, {"ws": str(workspace_id), "cid": str(conversation_id)}
        ).mappings().first()

        subject = "No Subject"
        snippet = ""
        participant_email = ""
        participant_name: str | None = None
        reply_status = "NONE"

        if inbound_row:
            parts = _safe_parse_json(inbound_row["participants"])
            from_info = parts.get("from", {})
            participant_email = _address(from_info)
            if isinstance(from_info, dict):
                participant_name = from_info.get("name")

            subject = inbound_row["subject"] or "No Subject"
            text_val = inbound_row["content_text"] or ""
            snippet = (text_val[:120] + "...") if len(text_val) > 120 else text_val
            reply_status = str(inbound_row["association_status"] or "UNRESOLVED")
        elif outbound_row:
            subject = outbound_row["content_subject"] or "No Subject"
            snippet = subject
            participant_email = outbound_row["frozen_destination"] or ""
            participant_name = outbound_row["frozen_sender_name"]

        return {
            "subject": subject,
            "snippet": snippet.strip(),
            "participant_email": participant_email,
            "participant_name": participant_name,
            "reply_status": reply_status,
            "message_count": total_count,
        }

    def get_conversation(self, workspace_id: UUID, conversation_id: UUID) -> ConversationDetail | None:
        """Fetch single conversation with full message thread and metadata."""
        _safe_set_role(self.session, "app_api")
        _safe_set_workspace(self.session, workspace_id)

        conv_sql = text(
            f"""
            SELECT c.id, c.workspace_id, c.mailbox_id,
                   {_CONVERSATION_CAMPAIGN} AS campaign_summary_id,
                   c.created_at, c.latest_activity_at, c.archived_at, c.read_at,
                   mb.original_address AS mailbox_address, mb.provider AS mailbox_provider,
                   camp.name AS campaign_name
            FROM public.conversations c
            LEFT JOIN public.mailboxes mb ON mb.workspace_id = c.workspace_id AND mb.id = c.mailbox_id
            LEFT JOIN public.campaigns camp ON camp.workspace_id = c.workspace_id AND camp.id = {_CONVERSATION_CAMPAIGN}
            WHERE c.workspace_id = :ws AND c.id = :cid
            """
        )
        row = self.session.execute(
            conv_sql, {"ws": str(workspace_id), "cid": str(conversation_id)}
        ).mappings().first()

        if not row:
            return None

        latest_act = row["latest_activity_at"]
        if isinstance(latest_act, str):
            latest_act = datetime.fromisoformat(latest_act)
        if latest_act.tzinfo is None:
            latest_act = latest_act.replace(tzinfo=UTC)

        created_at = row["created_at"]
        if created_at is not None:
            if isinstance(created_at, str):
                created_at = datetime.fromisoformat(created_at)
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=UTC)
        else:
            created_at = latest_act

        read_at = row["read_at"]
        if read_at is not None:
            if isinstance(read_at, str):
                read_at = datetime.fromisoformat(read_at)
            if read_at.tzinfo is None:
                read_at = read_at.replace(tzinfo=UTC)

        archived_at = row["archived_at"]
        if archived_at is not None:
            if isinstance(archived_at, str):
                archived_at = datetime.fromisoformat(archived_at)
            if archived_at.tzinfo is None:
                archived_at = archived_at.replace(tzinfo=UTC)

        is_read = bool(read_at is not None and read_at >= latest_act)

        # Inbound messages
        inbound_sql = text(
            """
            SELECT im.id, im.mailbox_id, im.conversation_id, im.rfc_message_id,
                   im.participants, im.subject, im.content_text, im.received_at,
                   im.direction, im.association_status, im.classification,
                   (SELECT st.position
                      FROM public.inbound_outreach_links l
                      JOIN public.messages om
                        ON om.workspace_id = l.workspace_id AND om.id = l.outbound_message_id
                      JOIN public.sequence_steps st
                        ON st.workspace_id = om.workspace_id AND st.id = om.step_id
                     WHERE l.workspace_id = im.workspace_id AND l.inbound_message_id = im.id
                       AND l.status = 'CONFIRMED'
                     LIMIT 1) AS step_position
            FROM public.inbound_messages im
            WHERE im.workspace_id = :ws AND im.conversation_id = :cid
            ORDER BY im.received_at ASC, im.id ASC
            """
        )
        inbound_rows = self.session.execute(
            inbound_sql, {"ws": str(workspace_id), "cid": str(conversation_id)}
        ).mappings().all()

        # Outbound messages
        outbound_sql = text(
            """
            SELECT id, mailbox_id, campaign_id, enrollment_id, sequence_id, step_id,
                   rfc_message_id, content_subject, content_body_html, frozen_destination,
                   frozen_sender_address, frozen_sender_name, status, accepted_at, created_at,
                   (SELECT st.position FROM public.sequence_steps st
                     WHERE st.workspace_id = messages.workspace_id
                       AND st.id = messages.step_id) AS step_position
            FROM public.messages
            WHERE workspace_id = :ws
              AND (
                  conversation_id = :cid
                  OR id IN (
                      SELECT outbound_message_id FROM public.inbound_outreach_links
                      WHERE workspace_id = :ws AND inbound_message_id IN (
                          SELECT id FROM public.inbound_messages
                          WHERE workspace_id = :ws AND conversation_id = :cid
                      )
                  )
              )
            ORDER BY created_at ASC, id ASC
            """
        )
        outbound_rows = self.session.execute(
            outbound_sql, {"ws": str(workspace_id), "cid": str(conversation_id)}
        ).mappings().all()

        messages: list[MessageThreadItem] = []
        participant_email = ""
        participant_name: str | None = None
        reply_status = "NONE"

        # Build Inbound thread items
        for im in inbound_rows:
            parts = _safe_parse_json(im["participants"])
            from_info = parts.get("from", {})
            sender_name = None
            sender_email = _address(from_info)
            if isinstance(from_info, dict):
                sender_name = from_info.get("name")

            if not participant_email:
                participant_email = sender_email
                participant_name = sender_name

            to_list = parts.get("to", [])
            recipient_email = ""
            recipient_name = None
            if isinstance(to_list, list) and to_list:
                first_to = to_list[0]
                recipient_email = _address(first_to)
                if isinstance(first_to, dict):
                    recipient_name = first_to.get("name")

            ts = im["received_at"]
            if isinstance(ts, str):
                ts = datetime.fromisoformat(ts)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)

            reply_status = str(im["association_status"] or "UNRESOLVED")

            messages.append(
                MessageThreadItem(
                    id=UUID(str(im["id"])),
                    direction=MessageDirection.INBOUND,
                    sender_email=sender_email,
                    sender_name=sender_name,
                    recipient_email=recipient_email,
                    recipient_name=recipient_name,
                    subject=str(im["subject"] or ""),
                    content_text=im["content_text"],
                    content_html=None,
                    timestamp=ts,
                    sequence_step_position=im["step_position"],
                    association_status=im["association_status"],
                    classification=im["classification"],
                )
            )

        # Build Outbound thread items
        for om in outbound_rows:
            ts = om["accepted_at"] or om["created_at"]
            if isinstance(ts, str):
                ts = datetime.fromisoformat(ts)
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=UTC)

            if not participant_email and om["frozen_destination"]:
                participant_email = om["frozen_destination"]
                participant_name = om["frozen_sender_name"]

            # Sanitize HTML preview for safe rendering
            clean_html = sanitize_html_preview(om["content_body_html"] or "") if om["content_body_html"] else None

            messages.append(
                MessageThreadItem(
                    id=UUID(str(om["id"])),
                    direction=MessageDirection.OUTBOUND,
                    sender_email=str(om["frozen_sender_address"] or ""),
                    sender_name=om["frozen_sender_name"],
                    recipient_email=str(om["frozen_destination"] or ""),
                    recipient_name=None,
                    subject=str(om["content_subject"] or ""),
                    content_text=None,
                    content_html=clean_html,
                    timestamp=ts,
                    status=om["status"],
                    sequence_step_id=UUID(str(om["step_id"])) if om["step_id"] else None,
                    sequence_step_position=om["step_position"],
                )
            )

        # Sort messages chronologically
        messages.sort(key=lambda m: (m.timestamp, str(m.id)))

        # Fallback subject if no messages
        subject = "No Subject"
        if messages:
            subject = messages[-1].subject or "No Subject"

        lead_row = self.session.execute(
            text(
                """
                SELECT e.lead_id
                FROM public.inbound_outreach_links l
                JOIN public.inbound_messages lim
                  ON lim.workspace_id = l.workspace_id AND lim.id = l.inbound_message_id
                JOIN public.campaign_enrollments e
                  ON e.workspace_id = l.workspace_id AND e.id = l.enrollment_id
                WHERE l.workspace_id = :ws AND lim.conversation_id = :cid
                  AND l.status = 'CONFIRMED' AND e.lead_id IS NOT NULL
                ORDER BY l.matched_at DESC
                LIMIT 1
                """
            ),
            {"ws": str(workspace_id), "cid": str(conversation_id)},
        ).first()

        return ConversationDetail(
            id=UUID(str(row["id"])),
            workspace_id=UUID(str(row["workspace_id"])),
            mailbox_id=UUID(str(row["mailbox_id"])),
            mailbox_address=str(row["mailbox_address"] or ""),
            mailbox_provider=str(row["mailbox_provider"] or ""),
            campaign_id=UUID(str(row["campaign_summary_id"])) if row["campaign_summary_id"] else None,
            campaign_name=str(row["campaign_name"]) if row["campaign_name"] else None,
            subject=subject,
            latest_activity_at=latest_act,
            created_at=created_at,
            is_read=is_read,
            read_at=read_at,
            archived_at=archived_at,
            reply_status=reply_status,
            participant_email=participant_email,
            participant_name=participant_name,
            lead_id=UUID(str(lead_row[0])) if lead_row and lead_row[0] else None,
            messages=messages,
        )

    # conversations.updated_at/version are maintained by the
    # conversations_touch_row trigger; app_api is only granted read_at and
    # archived_at (migrations 0004/0016), so these UPDATEs must not assign them.
    def mark_read(self, workspace_id: UUID, conversation_id: UUID) -> dict[str, Any] | None:
        """Mark conversation as read idempotently."""
        _safe_set_role(self.session, "app_api")
        _safe_set_workspace(self.session, workspace_id)

        now = datetime.now(UTC)
        query = text(
            """
            UPDATE public.conversations
            SET read_at = :now
            WHERE workspace_id = :ws AND id = :cid
            """
        )
        res = self.session.execute(query, {"ws": str(workspace_id), "cid": str(conversation_id), "now": now})
        if res.rowcount == 0:
            return None

        # Return updated state
        sel = text(
            "SELECT id, read_at, archived_at, latest_activity_at, updated_at FROM public.conversations WHERE workspace_id = :ws AND id = :cid"
        )
        row = self.session.execute(sel, {"ws": str(workspace_id), "cid": str(conversation_id)}).mappings().first()
        if not row:
            return None
        return {
            "id": UUID(str(row["id"])),
            "is_read": True,
            "read_at": row["read_at"],
            "archived_at": row["archived_at"],
            "updated_at": row["updated_at"],
        }

    def mark_unread(self, workspace_id: UUID, conversation_id: UUID) -> dict[str, Any] | None:
        """Mark conversation as unread idempotently."""
        _safe_set_role(self.session, "app_api")
        _safe_set_workspace(self.session, workspace_id)

        query = text(
            """
            UPDATE public.conversations
            SET read_at = NULL
            WHERE workspace_id = :ws AND id = :cid
            """
        )
        res = self.session.execute(query, {"ws": str(workspace_id), "cid": str(conversation_id)})
        if res.rowcount == 0:
            return None

        sel = text(
            "SELECT id, read_at, archived_at, latest_activity_at, updated_at FROM public.conversations WHERE workspace_id = :ws AND id = :cid"
        )
        row = self.session.execute(sel, {"ws": str(workspace_id), "cid": str(conversation_id)}).mappings().first()
        if not row:
            return None
        return {
            "id": UUID(str(row["id"])),
            "is_read": False,
            "read_at": None,
            "archived_at": row["archived_at"],
            "updated_at": row["updated_at"],
        }

    def archive(self, workspace_id: UUID, conversation_id: UUID) -> dict[str, Any] | None:
        """Archive conversation idempotently (requires inbox.manage capability)."""
        _safe_set_role(self.session, "app_api")
        _safe_set_workspace(self.session, workspace_id)

        now = datetime.now(UTC)
        query = text(
            """
            UPDATE public.conversations
            SET archived_at = COALESCE(archived_at, :now)
            WHERE workspace_id = :ws AND id = :cid
            """
        )
        res = self.session.execute(query, {"ws": str(workspace_id), "cid": str(conversation_id), "now": now})
        if res.rowcount == 0:
            return None

        sel = text(
            "SELECT id, read_at, archived_at, latest_activity_at, updated_at FROM public.conversations WHERE workspace_id = :ws AND id = :cid"
        )
        row = self.session.execute(sel, {"ws": str(workspace_id), "cid": str(conversation_id)}).mappings().first()
        if not row:
            return None

        latest_act = row["latest_activity_at"]
        read_at = row["read_at"]
        is_read = bool(read_at and read_at >= latest_act)

        return {
            "id": UUID(str(row["id"])),
            "is_read": is_read,
            "read_at": read_at,
            "archived_at": row["archived_at"],
            "updated_at": row["updated_at"],
        }

    def unarchive(self, workspace_id: UUID, conversation_id: UUID) -> dict[str, Any] | None:
        """Unarchive conversation idempotently (requires inbox.manage capability)."""
        _safe_set_role(self.session, "app_api")
        _safe_set_workspace(self.session, workspace_id)

        query = text(
            """
            UPDATE public.conversations
            SET archived_at = NULL
            WHERE workspace_id = :ws AND id = :cid
            """
        )
        res = self.session.execute(query, {"ws": str(workspace_id), "cid": str(conversation_id)})
        if res.rowcount == 0:
            return None

        sel = text(
            "SELECT id, read_at, archived_at, latest_activity_at, updated_at FROM public.conversations WHERE workspace_id = :ws AND id = :cid"
        )
        row = self.session.execute(sel, {"ws": str(workspace_id), "cid": str(conversation_id)}).mappings().first()
        if not row:
            return None

        latest_act = row["latest_activity_at"]
        read_at = row["read_at"]
        is_read = bool(read_at and read_at >= latest_act)

        return {
            "id": UUID(str(row["id"])),
            "is_read": is_read,
            "read_at": read_at,
            "archived_at": None,
            "updated_at": row["updated_at"],
        }

    def get_sync_status(self, workspace_id: UUID) -> list[MailboxSyncStatusItem]:
        """Fetch synchronization health and status for all mailboxes in the workspace."""
        _safe_set_role(self.session, "app_api")
        _safe_set_workspace(self.session, workspace_id)

        query = text(
            """
            SELECT mb.id AS mailbox_id, mb.original_address AS email_address, mb.provider, mb.connection_state AS connection_status,
                   COALESCE(mss.sync_scope, 'INBOX') AS sync_scope,
                   COALESCE(mss.status, 'UNKNOWN') AS sync_status,
                   mss.last_complete_at,
                   COALESCE(mss.failure_count, 0) AS failure_count
            FROM public.mailboxes mb
            LEFT JOIN public.mailbox_sync_states mss
                   ON mss.workspace_id = mb.workspace_id AND mss.mailbox_id = mb.id AND mss.sync_scope = 'INBOX'
            WHERE mb.workspace_id = :ws
            ORDER BY mb.original_address ASC
            """
        )
        rows = self.session.execute(query, {"ws": str(workspace_id)}).mappings().all()

        items: list[MailboxSyncStatusItem] = []
        for r in rows:
            last_complete = r["last_complete_at"]
            if isinstance(last_complete, str):
                last_complete = datetime.fromisoformat(last_complete)
            if last_complete and last_complete.tzinfo is None:
                last_complete = last_complete.replace(tzinfo=UTC)

            # SMTP doesn't have inbound sync capability
            sync_st = str(r["sync_status"])
            if str(r["provider"]).upper() == "SMTP":
                sync_st = "NOT_SUPPORTED"

            items.append(
                MailboxSyncStatusItem(
                    mailbox_id=UUID(str(r["mailbox_id"])),
                    email_address=str(r["email_address"]),
                    provider=str(r["provider"]),
                    connection_status=str(r["connection_status"]),
                    sync_scope=str(r["sync_scope"]),
                    sync_status=sync_st,
                    last_complete_at=last_complete,
                    failure_count=int(r["failure_count"]),
                )
            )
        return items
