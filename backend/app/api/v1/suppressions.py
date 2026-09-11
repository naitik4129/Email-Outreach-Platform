from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.deps import WorkspaceContext, get_db, get_workspace_context
from app.core.permissions import require_permission
from app.modules.leads.pagination import DEFAULT_LIMIT
from app.modules.suppression.service import SuppressionService
from app.schemas.suppression import (
    SuppressionCreateIn,
    SuppressionOut,
    SuppressionPage,
    SuppressionReleaseIn,
)

router = APIRouter()


@router.get("/suppressions", response_model=SuppressionPage)
def list_suppressions(
    limit: int = Query(DEFAULT_LIMIT, ge=1),
    cursor: str | None = Query(default=None, max_length=512),
    q: str | None = Query(default=None, max_length=100),
    context: WorkspaceContext = Depends(get_workspace_context),
    db: Session = Depends(get_db),
) -> SuppressionPage:
    return SuppressionService(db).list_suppressions(
        context, limit=limit, cursor=cursor, query=q
    )


@router.post("/suppressions", response_model=SuppressionOut, status_code=status.HTTP_201_CREATED)
def create_suppression(
    payload: SuppressionCreateIn,
    context: WorkspaceContext = Depends(require_permission("contacts.manage")),
    db: Session = Depends(get_db),
) -> SuppressionOut:
    return SuppressionService(db).create_manual_suppression(context, payload)


@router.get("/suppressions/{suppression_id}", response_model=SuppressionOut)
def get_suppression(
    suppression_id: UUID,
    context: WorkspaceContext = Depends(get_workspace_context),
    db: Session = Depends(get_db),
) -> SuppressionOut:
    return SuppressionService(db).get_suppression(context, suppression_id)


@router.post("/suppressions/{suppression_id}/release", response_model=SuppressionOut)
def release_suppression(
    suppression_id: UUID,
    payload: SuppressionReleaseIn,
    # Releasing suppression requires admin/owner level authority, usually contacts.manage is sufficient
    # but based on SUPPRESSION.md "only Admin/Owner release MANUAL with audit".
    context: WorkspaceContext = Depends(require_permission("contacts.manage")),
    db: Session = Depends(get_db),
) -> SuppressionOut:
    return SuppressionService(db).release_manual_suppression(context, suppression_id, payload)
