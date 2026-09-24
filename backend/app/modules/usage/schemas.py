from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class DimensionUsage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    used: int
    limit: int | None = None
    unit: str


class WorkspaceUsageOut(BaseModel):
    model_config = ConfigDict(extra="ignore")

    workspace_id: UUID
    plan_name: str
    dimensions: dict[str, DimensionUsage]
    as_of: datetime
