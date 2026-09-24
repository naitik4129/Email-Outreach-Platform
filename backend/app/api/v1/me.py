from __future__ import annotations

import json
from typing import Any

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


@router.get("/me/preferences", response_model=dict[str, Any])
def get_my_preferences(
    principal: AuthenticatedPrincipal = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    row = (
        db.execute(
            text("SELECT preferences FROM profiles WHERE id = :id"),
            {"id": str(principal.user_id)},
        )
        .mappings()
        .first()
    )
    if row is None:
        raise AppError("not_found", "Profile not found", status_code=404)
    return dict(row["preferences"] or {})


@router.patch("/me/preferences", response_model=dict[str, Any])
def update_my_preferences(
    payload: dict[str, Any],
    principal: AuthenticatedPrincipal = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    current_raw = (
        db.execute(
            text("SELECT preferences FROM profiles WHERE id = :id"),
            {"id": str(principal.user_id)},
        )
        .mappings()
        .first()
    )
    prefs = (current_raw["preferences"] if current_raw else None) or {}
    current = dict(prefs)
    current.update(payload)

    db.execute(
        text(
            """
            UPDATE profiles
            SET preferences = CAST(:prefs AS jsonb),
                updated_at = statement_timestamp()
            WHERE id = :id
            """
        ),
        {"id": str(principal.user_id), "prefs": json.dumps(current)},
    )
    return current
