from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.api.deps import WorkspaceContext, get_db, get_workspace_context
from app.core.permissions import require_permission
from app.modules.leads.pagination import DEFAULT_LIMIT
from app.modules.leads.service import LeadService
from app.schemas.leads import (
    ExpectedVersionIn,
    LeadCreateIn,
    LeadDetailOut,
    LeadListCreateIn,
    LeadListMemberCreateIn,
    LeadListMemberPage,
    LeadListOut,
    LeadListPage,
    LeadListUpdateIn,
    LeadOut,
    LeadPage,
    LeadUpdateIn,
)

router = APIRouter()


@router.get("/leads", response_model=LeadPage)
def list_leads(
    limit: int = Query(DEFAULT_LIMIT, ge=1),
    cursor: str | None = Query(default=None, max_length=512),
    q: str | None = Query(default=None, max_length=100),
    status_filter: str = Query("ACTIVE", alias="status"),
    list_id: UUID | None = None,
    context: WorkspaceContext = Depends(get_workspace_context),
    db: Session = Depends(get_db),
) -> LeadPage:
    return LeadService(db).list_leads(
        context,
        limit=limit,
        cursor=cursor,
        query=q,
        status=status_filter,
        list_id=list_id,
    )


@router.post("/leads", response_model=LeadOut, status_code=status.HTTP_201_CREATED)
def create_lead(
    payload: LeadCreateIn,
    context: WorkspaceContext = Depends(require_permission("contacts.manage")),
    db: Session = Depends(get_db),
) -> LeadOut:
    return LeadService(db).create_lead(context, payload)


@router.get("/leads/{lead_id}", response_model=LeadDetailOut)
def get_lead(
    lead_id: UUID,
    context: WorkspaceContext = Depends(get_workspace_context),
    db: Session = Depends(get_db),
) -> LeadDetailOut:
    return LeadService(db).get_lead_detail(context, lead_id)


@router.patch("/leads/{lead_id}", response_model=LeadOut)
def update_lead(
    lead_id: UUID,
    payload: LeadUpdateIn,
    context: WorkspaceContext = Depends(require_permission("contacts.manage")),
    db: Session = Depends(get_db),
) -> LeadOut:
    return LeadService(db).update_lead(context, lead_id, payload)


@router.post("/leads/{lead_id}/archive", response_model=LeadOut)
def archive_lead(
    lead_id: UUID,
    payload: ExpectedVersionIn,
    context: WorkspaceContext = Depends(require_permission("contacts.manage")),
    db: Session = Depends(get_db),
) -> LeadOut:
    return LeadService(db).archive_lead(context, lead_id, payload)


@router.get("/lead-lists", response_model=LeadListPage)
def list_lead_lists(
    limit: int = Query(DEFAULT_LIMIT, ge=1),
    cursor: str | None = Query(default=None, max_length=512),
    context: WorkspaceContext = Depends(get_workspace_context),
    db: Session = Depends(get_db),
) -> LeadListPage:
    return LeadService(db).list_lists(context, limit=limit, cursor=cursor)


@router.post(
    "/lead-lists",
    response_model=LeadListOut,
    status_code=status.HTTP_201_CREATED,
)
def create_lead_list(
    payload: LeadListCreateIn,
    context: WorkspaceContext = Depends(require_permission("contacts.manage")),
    db: Session = Depends(get_db),
) -> LeadListOut:
    return LeadService(db).create_list(context, payload)


@router.get("/lead-lists/{list_id}", response_model=LeadListOut)
def get_lead_list(
    list_id: UUID,
    context: WorkspaceContext = Depends(get_workspace_context),
    db: Session = Depends(get_db),
) -> LeadListOut:
    return LeadService(db).get_list(context, list_id)


@router.patch("/lead-lists/{list_id}", response_model=LeadListOut)
def update_lead_list(
    list_id: UUID,
    payload: LeadListUpdateIn,
    context: WorkspaceContext = Depends(require_permission("contacts.manage")),
    db: Session = Depends(get_db),
) -> LeadListOut:
    return LeadService(db).update_list(context, list_id, payload)


@router.post("/lead-lists/{list_id}/archive", response_model=LeadListOut)
def archive_lead_list(
    list_id: UUID,
    payload: ExpectedVersionIn,
    context: WorkspaceContext = Depends(require_permission("contacts.manage")),
    db: Session = Depends(get_db),
) -> LeadListOut:
    return LeadService(db).archive_list(context, list_id, payload)


@router.get("/lead-lists/{list_id}/members", response_model=LeadListMemberPage)
def list_lead_list_members(
    list_id: UUID,
    limit: int = Query(DEFAULT_LIMIT, ge=1),
    cursor: str | None = Query(default=None, max_length=512),
    context: WorkspaceContext = Depends(get_workspace_context),
    db: Session = Depends(get_db),
) -> LeadListMemberPage:
    return LeadService(db).list_members(
        context,
        list_id,
        limit=limit,
        cursor=cursor,
    )


@router.post(
    "/lead-lists/{list_id}/members",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def add_lead_list_member(
    list_id: UUID,
    payload: LeadListMemberCreateIn,
    context: WorkspaceContext = Depends(require_permission("contacts.manage")),
    db: Session = Depends(get_db),
) -> Response:
    LeadService(db).add_member(context, list_id, payload)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete(
    "/lead-lists/{list_id}/members/{lead_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
def remove_lead_list_member(
    list_id: UUID,
    lead_id: UUID,
    context: WorkspaceContext = Depends(require_permission("contacts.manage")),
    db: Session = Depends(get_db),
) -> Response:
    LeadService(db).remove_member(context, list_id, lead_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
