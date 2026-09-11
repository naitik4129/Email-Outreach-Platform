from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.api.deps import WorkspaceContext
from app.core.errors import AppError
from app.modules.leads.normalization import normalize_email
from app.modules.leads.pagination import decode_cursor, encode_cursor, normalize_limit
from app.modules.suppression.repository import SuppressionRepository
from app.schemas.suppression import (
    SuppressionCreateIn,
    SuppressionOut,
    SuppressionPage,
    SuppressionReleaseIn,
)


def _suppression_out(row: Mapping[str, Any]) -> SuppressionOut:
    return SuppressionOut(
        id=row["id"],
        workspace_id=row["workspace_id"],
        email=row["email"],
        canonical_address=row["canonical_address"],
        reason=row["reason"],
        status=row["status"],
        first_observed_at=row["first_observed_at"],
        last_observed_at=row["last_observed_at"],
        released_at=row["released_at"],
        release_actor_id=row["release_actor_id"],
        removable=(row["reason"] == "MANUAL" and row["status"] == "ACTIVE"),
        version=row["version"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class SuppressionService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = SuppressionRepository(session)

    def list_suppressions(
        self, context: WorkspaceContext, *, limit: int, cursor: str | None, query: str | None
    ) -> SuppressionPage:
        effective_limit = normalize_limit(limit)
        rows = self.repo.list_suppressions(
            workspace_id=context.workspace_id,
            limit=effective_limit + 1,
            after_id=decode_cursor(cursor),
            query=query,
        )
        page_rows = rows[:effective_limit]
        next_cursor = (
            encode_cursor(page_rows[-1]["id"]) if len(rows) > effective_limit else None
        )
        return SuppressionPage(
            items=[_suppression_out(row) for row in page_rows],
            next_cursor=next_cursor,
        )

    def get_suppression(self, context: WorkspaceContext, suppression_id: UUID) -> SuppressionOut:
        row = self.repo.get_suppression(
            workspace_id=context.workspace_id, suppression_id=suppression_id
        )
        if row is None:
            raise AppError("suppression_not_found", "Suppression not found", status_code=404)
        return _suppression_out(row)

    def create_manual_suppression(
        self, context: WorkspaceContext, payload: SuppressionCreateIn
    ) -> SuppressionOut:
        try:
            normalized = normalize_email(payload.email)
        except AppError as exc:
            raise AppError("invalid_email", "Invalid email format", status_code=422) from exc

        self.repo.leads.ensure_recipient_address(
            workspace_id=context.workspace_id,
            canonical_address=normalized.canonical,
            original_display=normalized.original,
        )

        address_id = self.repo.get_address_id(
            workspace_id=context.workspace_id, canonical_address=normalized.canonical
        )
        assert address_id is not None

        suppression_id, was_insert = self.repo.upsert_manual_suppression(
            workspace_id=context.workspace_id,
            address_id=address_id,
            actor_id=context.user_id,
        )

        return self.get_suppression(context, suppression_id)

    def release_manual_suppression(
        self, context: WorkspaceContext, suppression_id: UUID, payload: SuppressionReleaseIn
    ) -> SuppressionOut:
        row = self.repo.release_manual_suppression(
            workspace_id=context.workspace_id,
            suppression_id=suppression_id,
            actor_id=context.user_id,
            reason=payload.reason,
        )
        if row is None:
            raise AppError(
                "invalid_operation",
                "Suppression not found, already released, or not manually removable.",
                status_code=409,
            )
            
        return self.get_suppression(context, suppression_id)
