from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.deps import WorkspaceContext, get_db, get_workspace_context
from app.core.permissions import require_permission
from app.modules.personalization.api_service import PersonalizationApiService
from app.modules.personalization.schemas import (
    ApproveIn,
    GenerationProgressOut,
    PersonalizationCapabilitiesOut,
    PersonalizationConfigIn,
    PersonalizationStateOut,
    PreviewBatchOut,
    PreviewCreateIn,
)

router = APIRouter()

# Authorization: reading needs product.read (any member); editing the objective
# and generating samples needs campaigns.draft; approving the samples that unlock
# activation needs campaigns.execute -- the same capability as activating
# (ADR-0011, no new capability). RLS enforces the same rules in the database.


@router.get(
    "/personalization/capabilities", response_model=PersonalizationCapabilitiesOut
)
def get_capabilities(
    context: WorkspaceContext = Depends(get_workspace_context),
    db: Session = Depends(get_db),
) -> PersonalizationCapabilitiesOut:
    return PersonalizationApiService(db).capabilities()


@router.get(
    "/campaigns/{campaign_id}/personalization", response_model=PersonalizationStateOut
)
def get_personalization(
    campaign_id: UUID,
    context: WorkspaceContext = Depends(get_workspace_context),
    db: Session = Depends(get_db),
) -> PersonalizationStateOut:
    return PersonalizationApiService(db).get_state(context, campaign_id)


@router.put(
    "/campaigns/{campaign_id}/personalization", response_model=PersonalizationStateOut
)
def put_personalization(
    campaign_id: UUID,
    payload: PersonalizationConfigIn,
    context: WorkspaceContext = Depends(require_permission("campaigns.draft")),
    db: Session = Depends(get_db),
) -> PersonalizationStateOut:
    return PersonalizationApiService(db).put_config(context, campaign_id, payload)


@router.post(
    "/campaigns/{campaign_id}/personalization/previews",
    response_model=PreviewBatchOut,
    status_code=status.HTTP_202_ACCEPTED,
)
def create_previews(
    campaign_id: UUID,
    payload: PreviewCreateIn,
    context: WorkspaceContext = Depends(require_permission("campaigns.draft")),
    db: Session = Depends(get_db),
) -> PreviewBatchOut:
    return PersonalizationApiService(db).create_previews(context, campaign_id, payload)


@router.get(
    "/campaigns/{campaign_id}/personalization/previews/latest",
    response_model=PreviewBatchOut | None,
)
def get_latest_previews(
    campaign_id: UUID,
    context: WorkspaceContext = Depends(get_workspace_context),
    db: Session = Depends(get_db),
) -> PreviewBatchOut | None:
    return PersonalizationApiService(db).latest_batch(context, campaign_id)


@router.get(
    "/campaigns/{campaign_id}/personalization/previews/{batch_id}",
    response_model=PreviewBatchOut,
)
def get_previews(
    campaign_id: UUID,
    batch_id: UUID,
    context: WorkspaceContext = Depends(get_workspace_context),
    db: Session = Depends(get_db),
) -> PreviewBatchOut:
    return PersonalizationApiService(db).get_batch(context, campaign_id, batch_id)


@router.post(
    "/campaigns/{campaign_id}/personalization/approve",
    response_model=PersonalizationStateOut,
)
def approve_previews(
    campaign_id: UUID,
    payload: ApproveIn,
    context: WorkspaceContext = Depends(require_permission("campaigns.execute")),
    db: Session = Depends(get_db),
) -> PersonalizationStateOut:
    return PersonalizationApiService(db).approve(context, campaign_id, payload)


@router.get(
    "/campaigns/{campaign_id}/personalization/progress",
    response_model=GenerationProgressOut,
)
def get_generation_progress(
    campaign_id: UUID,
    context: WorkspaceContext = Depends(get_workspace_context),
    db: Session = Depends(get_db),
) -> GenerationProgressOut:
    return PersonalizationApiService(db).progress(context, campaign_id)
