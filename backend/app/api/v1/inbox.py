from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import WorkspaceContext, get_db
from app.core.permissions import require_permission
from app.modules.inbox.schemas import (
    ConversationActionResponse,
    ConversationDetail,
    ConversationFilter,
    ConversationPage,
    InboxSyncStatusResponse,
)
from app.modules.inbox.service import InboxService
from app.modules.leads.pagination import DEFAULT_LIMIT

router = APIRouter()


@router.get("/inbox/conversations", response_model=ConversationPage)
def list_conversations(
    limit: int = Query(DEFAULT_LIMIT, ge=1),
    cursor: str | None = Query(default=None, max_length=512),
    q: str | None = Query(default=None, max_length=200),
    filter_mode: ConversationFilter = Query(default=ConversationFilter.ALL, alias="filter"),
    mailbox_id: UUID | None = Query(default=None),
    campaign_id: UUID | None = Query(default=None),
    context: WorkspaceContext = Depends(require_permission("product.read")),
    db: Session = Depends(get_db),
) -> ConversationPage:
    return InboxService(db).list_conversations(
        context,
        filter_mode=filter_mode,
        mailbox_id=mailbox_id,
        campaign_id=campaign_id,
        search_query=q,
        cursor=cursor,
        limit=limit,
    )


@router.get("/inbox/conversations/{conversation_id}", response_model=ConversationDetail)
def get_conversation(
    conversation_id: UUID,
    context: WorkspaceContext = Depends(require_permission("product.read")),
    db: Session = Depends(get_db),
) -> ConversationDetail:
    return InboxService(db).get_conversation(context, conversation_id)


@router.patch("/inbox/conversations/{conversation_id}/read", response_model=ConversationActionResponse)
def mark_conversation_read(
    conversation_id: UUID,
    context: WorkspaceContext = Depends(require_permission("product.read")),
    db: Session = Depends(get_db),
) -> ConversationActionResponse:
    return InboxService(db).mark_read(context, conversation_id)


@router.patch("/inbox/conversations/{conversation_id}/unread", response_model=ConversationActionResponse)
def mark_conversation_unread(
    conversation_id: UUID,
    context: WorkspaceContext = Depends(require_permission("product.read")),
    db: Session = Depends(get_db),
) -> ConversationActionResponse:
    return InboxService(db).mark_unread(context, conversation_id)


@router.patch("/inbox/conversations/{conversation_id}/archive", response_model=ConversationActionResponse)
def archive_conversation(
    conversation_id: UUID,
    context: WorkspaceContext = Depends(require_permission("inbox.manage")),
    db: Session = Depends(get_db),
) -> ConversationActionResponse:
    return InboxService(db).archive(context, conversation_id)


@router.patch("/inbox/conversations/{conversation_id}/unarchive", response_model=ConversationActionResponse)
def unarchive_conversation(
    conversation_id: UUID,
    context: WorkspaceContext = Depends(require_permission("inbox.manage")),
    db: Session = Depends(get_db),
) -> ConversationActionResponse:
    return InboxService(db).unarchive(context, conversation_id)


@router.get("/inbox/sync-status", response_model=InboxSyncStatusResponse)
def get_inbox_sync_status(
    context: WorkspaceContext = Depends(require_permission("product.read")),
    db: Session = Depends(get_db),
) -> InboxSyncStatusResponse:
    return InboxService(db).get_sync_status(context)
