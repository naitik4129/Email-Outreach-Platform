from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.core.auth import AuthenticatedPrincipal
from app.core.errors import AppError
from app.schemas.profile import ProfileOut

router = APIRouter()


@router.get("/me", response_model=ProfileOut)
def get_me(
    principal: AuthenticatedPrincipal = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ProfileOut:
    row = (
        db.execute(
            text("SELECT id, display_name FROM profiles WHERE id = :id"),
            {"id": str(principal.user_id)},
        )
        .mappings()
        .first()
    )
    if row is None:
        raise AppError("not_found", "Profile not found", status_code=404)
    return ProfileOut(
        id=row["id"], display_name=row["display_name"], email=principal.email
    )
