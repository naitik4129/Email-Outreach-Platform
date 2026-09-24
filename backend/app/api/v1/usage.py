from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import WorkspaceContext, get_db
from app.core.permissions import require_permission
from app.modules.usage.schemas import WorkspaceUsageOut
from app.modules.usage.service import UsageService

router = APIRouter()


@router.get("/usage", response_model=WorkspaceUsageOut)
def get_workspace_usage(
    context: WorkspaceContext = Depends(require_permission("product.read")),
    db: Session = Depends(get_db),
) -> WorkspaceUsageOut:
    """Returns durable workspace usage metrics compared with quotas."""
    service = UsageService(db)
    return service.get_workspace_usage(context.workspace_id)
