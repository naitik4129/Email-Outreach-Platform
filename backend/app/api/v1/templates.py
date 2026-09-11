from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.deps import WorkspaceContext, get_db, get_workspace_context
from app.core.permissions import require_permission
from app.modules.leads.pagination import DEFAULT_LIMIT
from app.modules.templates.service import TemplateService
from app.schemas.templates import (
    TemplateArchiveIn,
    TemplateCreateIn,
    TemplateDetailOut,
    TemplateDuplicateIn,
    TemplatePage,
    TemplatePreviewIn,
    TemplatePreviewOut,
    TemplateUpdateIn,
    TemplateVersionOut,
)

router = APIRouter()


@router.get("/templates", response_model=TemplatePage)
def list_templates(
    limit: int = Query(DEFAULT_LIMIT, ge=1),
    cursor: str | None = Query(default=None, max_length=512),
    q: str | None = Query(default=None, max_length=100),
    status_filter: str = Query("ACTIVE", alias="status"),
    context: WorkspaceContext = Depends(get_workspace_context),
    db: Session = Depends(get_db),
) -> TemplatePage:
    return TemplateService(db).list_templates(
        context,
        limit=limit,
        cursor=cursor,
        query=q,
        status=status_filter,
    )


@router.post(
    "/templates",
    response_model=TemplateDetailOut,
    status_code=status.HTTP_201_CREATED,
)
def create_template(
    payload: TemplateCreateIn,
    context: WorkspaceContext = Depends(require_permission("templates.manage")),
    db: Session = Depends(get_db),
) -> TemplateDetailOut:
    return TemplateService(db).create_template(context, payload)


@router.post("/templates/preview", response_model=TemplatePreviewOut)
def preview_template(
    payload: TemplatePreviewIn,
    context: WorkspaceContext = Depends(get_workspace_context),
    db: Session = Depends(get_db),
) -> TemplatePreviewOut:
    return TemplateService(db).preview_template(context, payload)


@router.get("/templates/{template_id}", response_model=TemplateDetailOut)
def get_template(
    template_id: UUID,
    context: WorkspaceContext = Depends(get_workspace_context),
    db: Session = Depends(get_db),
) -> TemplateDetailOut:
    return TemplateService(db).get_template(context, template_id)


@router.patch("/templates/{template_id}", response_model=TemplateDetailOut)
def update_template(
    template_id: UUID,
    payload: TemplateUpdateIn,
    context: WorkspaceContext = Depends(require_permission("templates.manage")),
    db: Session = Depends(get_db),
) -> TemplateDetailOut:
    return TemplateService(db).update_template(context, template_id, payload)


@router.post(
    "/templates/{template_id}/duplicate",
    response_model=TemplateDetailOut,
    status_code=status.HTTP_201_CREATED,
)
def duplicate_template(
    template_id: UUID,
    payload: TemplateDuplicateIn,
    context: WorkspaceContext = Depends(require_permission("templates.manage")),
    db: Session = Depends(get_db),
) -> TemplateDetailOut:
    return TemplateService(db).duplicate_template(context, template_id, payload)


@router.post("/templates/{template_id}/archive", response_model=TemplateDetailOut)
def archive_template(
    template_id: UUID,
    payload: TemplateArchiveIn,
    context: WorkspaceContext = Depends(require_permission("templates.manage")),
    db: Session = Depends(get_db),
) -> TemplateDetailOut:
    return TemplateService(db).archive_template(context, template_id, payload)


@router.get(
    "/templates/{template_id}/versions", response_model=list[TemplateVersionOut]
)
def list_template_versions(
    template_id: UUID,
    context: WorkspaceContext = Depends(get_workspace_context),
    db: Session = Depends(get_db),
) -> list[TemplateVersionOut]:
    return TemplateService(db).get_template_versions(context, template_id)
