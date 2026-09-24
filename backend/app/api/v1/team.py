from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from app.api.deps import (
    WorkspaceContext,
    get_db,
)
from app.core.permissions import require_permission
from app.modules.team.schemas import (
    InvitationCreateIn,
    InvitationOut,
    MemberRemoveIn,
    MemberRoleUpdateIn,
    TransferOwnershipIn,
    TransferOwnershipOut,
)
from app.modules.team.service import TeamService

router = APIRouter()


@router.get("/members")
def list_workspace_members(
    context: WorkspaceContext = Depends(require_permission("product.read")),
    db: Session = Depends(get_db),
) -> list[dict[str, Any]]:
    """Lists all active and historical members in the active workspace."""
    service = TeamService(db)
    return service.list_members(context.workspace_id)


@router.patch("/members/{membership_id}")
def update_member_role(
    membership_id: UUID,
    payload: MemberRoleUpdateIn,
    context: WorkspaceContext = Depends(require_permission("team.manage")),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Updates a member's role (Owner only, cannot change own role or Owner role)."""
    service = TeamService(db)
    return service.update_member_role(
        workspace_id=context.workspace_id,
        actor_id=context.user_id,
        membership_id=membership_id,
        payload=payload,
    )


@router.delete("/members/{membership_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_member(
    membership_id: UUID,
    payload: MemberRemoveIn,
    context: WorkspaceContext = Depends(require_permission("team.manage")),
    db: Session = Depends(get_db),
) -> None:
    """Revokes a member's access (Owner only, cannot remove Owner or self)."""
    service = TeamService(db)
    service.remove_member(
        workspace_id=context.workspace_id,
        actor_id=context.user_id,
        membership_id=membership_id,
        payload=payload,
    )


@router.post("/transfer-ownership", response_model=TransferOwnershipOut)
def transfer_workspace_ownership(
    payload: TransferOwnershipIn,
    context: WorkspaceContext = Depends(require_permission("ownership.manage")),
    db: Session = Depends(get_db),
) -> TransferOwnershipOut:
    """Atomically transfers workspace ownership: demotes owner, promotes member."""
    service = TeamService(db)
    return service.transfer_ownership(
        workspace_id=context.workspace_id,
        actor_id=context.user_id,
        payload=payload,
    )


@router.get("/invitations", response_model=list[InvitationOut])
def list_workspace_invitations(
    context: WorkspaceContext = Depends(require_permission("audit.read")),
    db: Session = Depends(get_db),
) -> list[InvitationOut]:
    """Lists workspace invitations (Admin and Owner only)."""
    service = TeamService(db)
    return service.list_invitations(context.workspace_id)


@router.post(
    "/invitations",
    response_model=InvitationOut,
    status_code=status.HTTP_201_CREATED,
)
def create_workspace_invitation(
    payload: InvitationCreateIn,
    request: Request,
    context: WorkspaceContext = Depends(require_permission("team.manage")),
    db: Session = Depends(get_db),
) -> InvitationOut:
    """Creates a workspace invitation and dispatches transactional notification."""
    service = TeamService(db)
    origin = request.headers.get("origin") or "http://localhost:3000"
    return service.create_invitation(
        workspace_id=context.workspace_id,
        inviter_id=context.user_id,
        payload=payload,
        base_url=origin,
    )


@router.post(
    "/invitations/{invitation_id}/revoke",
    status_code=status.HTTP_204_NO_CONTENT,
)
def revoke_workspace_invitation(
    invitation_id: UUID,
    context: WorkspaceContext = Depends(require_permission("team.manage")),
    db: Session = Depends(get_db),
) -> None:
    """Revokes an active invitation."""
    service = TeamService(db)
    service.revoke_invitation(
        workspace_id=context.workspace_id,
        actor_id=context.user_id,
        invitation_id=invitation_id,
    )


@router.post("/invitations/{invitation_id}/resend", response_model=InvitationOut)
def resend_workspace_invitation(
    invitation_id: UUID,
    request: Request,
    context: WorkspaceContext = Depends(require_permission("team.manage")),
    db: Session = Depends(get_db),
) -> InvitationOut:
    """Resends an invitation with a fresh token and reset expiration."""
    service = TeamService(db)
    origin = request.headers.get("origin") or "http://localhost:3000"
    return service.resend_invitation(
        workspace_id=context.workspace_id,
        actor_id=context.user_id,
        invitation_id=invitation_id,
        base_url=origin,
    )
