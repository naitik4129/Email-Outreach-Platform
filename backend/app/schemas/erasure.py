from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.schemas.bulk import MAX_BULK_ITEMS


class ErasureConfirmIn(BaseModel):
    """The user must type the item's name (see ErasureService for what that is per
    item). It is compared server-side, so a script cannot skip the confirmation."""

    confirm: str = Field(min_length=1, max_length=400)


class ErasureOut(BaseModel):
    operation: str
    target_id: UUID
    details: dict[str, Any]
    #: Stored files that could not be removed right now. The database change is
    #: committed either way; a retry of the same request removes leftovers.
    files_pending_cleanup: int = 0


class ErasureBulkIn(BaseModel):
    """Several archived items at once. Names can't be typed per item here, so the
    confirmation is one fixed word, compared server-side like the single-item one."""

    ids: list[UUID] = Field(min_length=1, max_length=MAX_BULK_ITEMS)
    confirm: str = Field(min_length=1, max_length=400)

    @field_validator("ids")
    @classmethod
    def _no_duplicates(cls, ids: list[UUID]) -> list[UUID]:
        if len(set(ids)) != len(ids):
            raise ValueError("Each id may appear only once per request")
        return ids
