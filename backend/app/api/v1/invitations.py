from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.core.auth import AuthenticatedPrincipal
from app.core.errors import AppError
from app.modules.team.schemas import (
    InvitationAcceptIn,
    InvitationAcceptOut,
    InvitationVerifyOut,
)
from app.modules.team.service import TeamService

router = APIRouter(prefix="/invitations", tags=["invitations"])


@router.get("/verify", response_model=InvitationVerifyOut)
def verify_invitation(
    token: str = Query(..., min_length=16, max_length=256),
    db: Session = Depends(get_db),
) -> InvitationVerifyOut:
    """Verifies an invitation token validity for display on the accept screen."""
    service = TeamService(db)
    return service.verify_invitation_token(token)


@router.post("/accept", response_model=InvitationAcceptOut)
def accept_invitation(
    payload: InvitationAcceptIn,
    principal: AuthenticatedPrincipal = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> InvitationAcceptOut:
    """Accepts an invitation and establishes workspace membership."""
    if not principal.email:
        raise AppError(
            "unauthenticated",
            "Verified email address required",
            status_code=401,
        )

    service = TeamService(db)
    return service.accept_invitation(
        actor_id=principal.user_id,
        actor_email=principal.email,
        payload=payload,
    )

