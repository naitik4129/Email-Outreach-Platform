from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.deps import WorkspaceContext, get_db
from app.core.permissions import require_permission
from app.modules.notifications.schemas import (
    NotificationListOut,
    UnreadCountOut,
)
from app.modules.notifications.service import NotificationService

router = APIRouter()


@router.get("/notifications", response_model=NotificationListOut)
def list_notifications(
    unread_only: bool = Query(False),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    context: WorkspaceContext = Depends(require_permission("product.read")),
    db: Session = Depends(get_db),
) -> NotificationListOut:
    """Lists in-app notifications for the active user in this workspace."""
    service = NotificationService(db)
    return service.list_notifications(
        workspace_id=context.workspace_id,
        user_id=context.user_id,
        unread_only=unread_only,
        limit=limit,
        offset=offset,
    )


@router.get("/notifications/unread-count", response_model=UnreadCountOut)
def get_unread_count(
    context: WorkspaceContext = Depends(require_permission("product.read")),
    db: Session = Depends(get_db),
) -> UnreadCountOut:
    """Returns the unread notifications count for the active user in this workspace."""
    service = NotificationService(db)
    count = service.get_unread_count(
        workspace_id=context.workspace_id,
        user_id=context.user_id,
    )
    return UnreadCountOut(unread_count=count)


@router.post(
    "/notifications/{notification_id}/read",
    status_code=status.HTTP_204_NO_CONTENT,
)
def mark_notification_read(
    notification_id: UUID,
    context: WorkspaceContext = Depends(require_permission("product.read")),
    db: Session = Depends(get_db),
) -> None:
    """Marks a single in-app notification as read."""
    service = NotificationService(db)
    service.mark_as_read(
        workspace_id=context.workspace_id,
        user_id=context.user_id,
        notification_id=notification_id,
    )


@router.post("/notifications/mark-all-read", status_code=status.HTTP_200_OK)
def mark_all_notifications_read(
    context: WorkspaceContext = Depends(require_permission("product.read")),
    db: Session = Depends(get_db),
) -> dict[str, int]:
    """Marks all notifications as read for the active user in this workspace."""
    service = NotificationService(db)
    count = service.mark_all_as_read(
        workspace_id=context.workspace_id,
        user_id=context.user_id,
    )
    return {"marked_read": count}
