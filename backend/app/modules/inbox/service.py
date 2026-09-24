from __future__ import annotations

import logging
from uuid import UUID

from sqlalchemy.orm import Session

from app.api.deps import WorkspaceContext
from app.core.errors import AppError
from app.modules.inbox.repository import InboxRepository
from app.modules.inbox.schemas import (
    ConversationActionResponse,
    ConversationDetail,
    ConversationFilter,
    ConversationPage,
    InboxSyncStatusResponse,
)
from app.modules.leads.pagination import MAX_LIMIT, normalize_limit

logger = logging.getLogger(__name__)


class InboxService:
    """Domain service for unified outreach inbox."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.repository = InboxRepository(session)

    def list_conversations(
        self,
        context: WorkspaceContext,
        *,
        filter_mode: ConversationFilter = ConversationFilter.ALL,
        mailbox_id: UUID | None = None,
        campaign_id: UUID | None = None,
        search_query: str | None = None,
        cursor: str | None = None,
        limit: int = 25,
    ) -> ConversationPage:
        limit = normalize_limit(limit)
        return self.repository.list_conversations(
            workspace_id=context.workspace_id,
            filter_mode=filter_mode,
            mailbox_id=mailbox_id,
            campaign_id=campaign_id,
            search_query=search_query,
            cursor=cursor,
            limit=limit,
        )

    def get_conversation(
        self,
        context: WorkspaceContext,
        conversation_id: UUID,
    ) -> ConversationDetail:
        conv = self.repository.get_conversation(
            workspace_id=context.workspace_id,
            conversation_id=conversation_id,
        )
        if not conv:
            # 404 to avoid leaking existence of cross-tenant IDs
            raise AppError("not_found", "Conversation not found", status_code=404)
        return conv

    def mark_read(
        self,
        context: WorkspaceContext,
        conversation_id: UUID,
    ) -> ConversationActionResponse:
        res = self.repository.mark_read(
            workspace_id=context.workspace_id,
            conversation_id=conversation_id,
        )
        if not res:
            raise AppError("not_found", "Conversation not found", status_code=404)
        return ConversationActionResponse(**res)

    def mark_unread(
        self,
        context: WorkspaceContext,
        conversation_id: UUID,
    ) -> ConversationActionResponse:
        res = self.repository.mark_unread(
            workspace_id=context.workspace_id,
            conversation_id=conversation_id,
        )
        if not res:
            raise AppError("not_found", "Conversation not found", status_code=404)
        return ConversationActionResponse(**res)

    def archive(
        self,
        context: WorkspaceContext,
        conversation_id: UUID,
    ) -> ConversationActionResponse:
        res = self.repository.archive(
            workspace_id=context.workspace_id,
            conversation_id=conversation_id,
        )
        if not res:
            raise AppError("not_found", "Conversation not found", status_code=404)
        return ConversationActionResponse(**res)

    def unarchive(
        self,
        context: WorkspaceContext,
        conversation_id: UUID,
    ) -> ConversationActionResponse:
        res = self.repository.unarchive(
            workspace_id=context.workspace_id,
            conversation_id=conversation_id,
        )
        if not res:
            raise AppError("not_found", "Conversation not found", status_code=404)
        return ConversationActionResponse(**res)

    def get_sync_status(
        self,
        context: WorkspaceContext,
    ) -> InboxSyncStatusResponse:
        items = self.repository.get_sync_status(workspace_id=context.workspace_id)
        return InboxSyncStatusResponse(mailboxes=items)
