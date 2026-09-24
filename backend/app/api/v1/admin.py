from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.deps import PlatformOperatorPrincipal, get_db, get_platform_operator
from app.modules.admin.schemas import (
    AdminWorkspaceOut,
    PlatformAuditLogOut,
    RestrictWorkspaceIn,
)
from app.modules.admin.service import AdminService

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/workspaces", response_model=list[AdminWorkspaceOut])
def list_admin_workspaces(
    operator: PlatformOperatorPrincipal = Depends(get_platform_operator),
    db: Session = Depends(get_db),
) -> list[AdminWorkspaceOut]:
    """Lists all workspaces with platform health indicators (Platform Operator only)."""
    service = AdminService(db)
    return service.list_workspaces()


@router.post("/workspaces/{workspace_id}/restrict", status_code=status.HTTP_200_OK)
def restrict_workspace(
    workspace_id: UUID,
    payload: RestrictWorkspaceIn,
    operator: PlatformOperatorPrincipal = Depends(get_platform_operator),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    """Places a platform-level sending restriction on a workspace for safety/abuse."""
    service = AdminService(db)
    service.restrict_workspace(
        workspace_id=workspace_id,
        reason=payload.reason,
        operator_id=operator.user_id,
    )
    return {"status": "restricted", "workspace_id": str(workspace_id)}


@router.post("/workspaces/{workspace_id}/restore", status_code=status.HTTP_200_OK)
def restore_workspace(
    workspace_id: UUID,
    operator: PlatformOperatorPrincipal = Depends(get_platform_operator),
    db: Session = Depends(get_db),
) -> dict[str, str]:
    """Restores a restricted workspace to ACTIVE status."""
    service = AdminService(db)
    service.restore_workspace(
        workspace_id=workspace_id,
        operator_id=operator.user_id,
    )
    return {"status": "active", "workspace_id": str(workspace_id)}


@router.get("/audit-logs", response_model=list[PlatformAuditLogOut])
def list_platform_audit_logs(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    operator: PlatformOperatorPrincipal = Depends(get_platform_operator),
    db: Session = Depends(get_db),
) -> list[PlatformAuditLogOut]:
    """Lists platform operator audit records."""
    service = AdminService(db)
    return service.list_platform_audit_logs(limit=limit, offset=offset)
