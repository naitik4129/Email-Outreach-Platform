from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status
from sqlalchemy.orm import Session

from app.api.deps import WorkspaceContext, get_db, get_workspace_context
from app.core.errors import AppError
from app.core.permissions import require_permission
from app.modules.imports.service import ImportService
from app.modules.leads.pagination import DEFAULT_LIMIT
from app.schemas.imports import (
    ImportCreateIn,
    ImportJobOut,
    ImportJobPage,
    ImportRowResultPage,
    ImportUploadOut,
)

router = APIRouter()


@router.post("/imports/upload", response_model=ImportUploadOut)
async def upload_import_file(
    file: UploadFile = File(...),
    context: WorkspaceContext = Depends(require_permission("contacts.manage")),
    db: Session = Depends(get_db),
) -> ImportUploadOut:
    data = await file.read()
    return ImportService(db).create_upload(
        context,
        content_type=file.content_type,
        filename=file.filename,
        data=data,
    )


@router.post("/imports", response_model=ImportJobOut, status_code=status.HTTP_201_CREATED)
def create_import(
    payload: ImportCreateIn,
    context: WorkspaceContext = Depends(require_permission("contacts.manage")),
    db: Session = Depends(get_db),
) -> ImportJobOut:
    return ImportService(db).confirm_import(context, payload)


@router.get("/imports", response_model=ImportJobPage)
def list_imports(
    limit: int = Query(DEFAULT_LIMIT, ge=1),
    cursor: str | None = Query(default=None, max_length=512),
    context: WorkspaceContext = Depends(get_workspace_context),
    db: Session = Depends(get_db),
) -> ImportJobPage:
    return ImportService(db).list_jobs(context, limit=limit, cursor=cursor)


@router.get("/imports/{import_id}", response_model=ImportJobOut)
def get_import(
    import_id: UUID,
    context: WorkspaceContext = Depends(get_workspace_context),
    db: Session = Depends(get_db),
) -> ImportJobOut:
    return ImportService(db).get_job(context, import_id)


@router.get("/imports/{import_id}/results", response_model=ImportRowResultPage)
def list_import_results(
    import_id: UUID,
    limit: int = Query(DEFAULT_LIMIT, ge=1),
    cursor: str | None = Query(default=None, max_length=512),
    context: WorkspaceContext = Depends(get_workspace_context),
    db: Session = Depends(get_db),
) -> ImportRowResultPage:
    return ImportService(db).list_row_results(
        context, import_id, limit=limit, cursor=cursor
    )
