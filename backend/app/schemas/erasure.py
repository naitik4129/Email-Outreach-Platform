from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


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
