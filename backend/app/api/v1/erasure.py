from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import WorkspaceContext, get_db
from app.core.permissions import require_permission
from app.modules.erasure.service import ErasureService
from app.schemas.erasure import ErasureConfirmIn, ErasureOut

router = APIRouter()

# Every route is POST with the confirmation in the body: a name or an email
# address must not end up in a URL (access logs, browser history), and the
# database commands re-check workspace.manage (ADMIN/OWNER) on their own.
_Admin = Depends(require_permission("workspace.manage"))


@router.post("/campaigns/{campaign_id}/purge", response_model=ErasureOut)
def purge_campaign(
    campaign_id: UUID,
    payload: ErasureConfirmIn,
    context: WorkspaceContext = _Admin,
    db: Session = Depends(get_db),
) -> ErasureOut:
    return ErasureService(db).purge_campaign(context, campaign_id, payload.confirm)


@router.post("/campaigns/{campaign_id}/erase", response_model=ErasureOut)
def erase_campaign(
    campaign_id: UUID,
    payload: ErasureConfirmIn,
    context: WorkspaceContext = _Admin,
    db: Session = Depends(get_db),
) -> ErasureOut:
    return ErasureService(db).erase_campaign(context, campaign_id, payload.confirm)


@router.post("/leads/{lead_id}/erase", response_model=ErasureOut)
def erase_lead(
    lead_id: UUID,
    payload: ErasureConfirmIn,
    context: WorkspaceContext = _Admin,
    db: Session = Depends(get_db),
) -> ErasureOut:
    return ErasureService(db).erase_lead(context, lead_id, payload.confirm)


@router.post("/templates/{template_id}/purge", response_model=ErasureOut)
def purge_template(
    template_id: UUID,
    payload: ErasureConfirmIn,
    context: WorkspaceContext = _Admin,
    db: Session = Depends(get_db),
) -> ErasureOut:
    return ErasureService(db).purge_template(context, template_id, payload.confirm)


@router.post("/lead-lists/{list_id}/purge", response_model=ErasureOut)
def purge_lead_list(
    list_id: UUID,
    payload: ErasureConfirmIn,
    context: WorkspaceContext = _Admin,
    db: Session = Depends(get_db),
) -> ErasureOut:
    return ErasureService(db).purge_lead_list(context, list_id, payload.confirm)


@router.post("/imports/{import_id}/purge", response_model=ErasureOut)
def purge_import(
    import_id: UUID,
    payload: ErasureConfirmIn,
    context: WorkspaceContext = _Admin,
    db: Session = Depends(get_db),
) -> ErasureOut:
    return ErasureService(db).purge_import(context, import_id, payload.confirm)


@router.post("/mailboxes/{mailbox_id}/purge", response_model=ErasureOut)
def purge_mailbox(
    mailbox_id: UUID,
    payload: ErasureConfirmIn,
    context: WorkspaceContext = _Admin,
    db: Session = Depends(get_db),
) -> ErasureOut:
    return ErasureService(db).purge_mailbox(context, mailbox_id, payload.confirm)
