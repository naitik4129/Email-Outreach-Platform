from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel


class ProfileOut(BaseModel):
    id: UUID
    display_name: str | None
    email: str | None
