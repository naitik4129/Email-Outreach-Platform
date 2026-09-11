from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class TemplateCreateIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    subject: str = Field(min_length=1, max_length=500)
    body_html: str = Field(default="", max_length=200_000)
    mode: Literal["STANDARD"] = "STANDARD"


class TemplateUpdateIn(BaseModel):
    expected_version: int = Field(ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    subject: str | None = Field(default=None, min_length=1, max_length=500)
    body_html: str | None = Field(default=None, max_length=200_000)


class TemplateDuplicateIn(BaseModel):
    name: str | None = Field(default=None, max_length=200)


class TemplateArchiveIn(BaseModel):
    expected_version: int = Field(ge=1)


class TemplatePreviewIn(BaseModel):
    subject: str = Field(min_length=1, max_length=500)
    body_html: str = Field(default="", max_length=200_000)
    lead_id: UUID | None = None
    sample_data: dict[str, Any] | None = None


class TemplatePreviewOut(BaseModel):
    subject: str
    body_html: str
    detected_variables: list[str]
    missing_variables: list[str]


class TemplateVersionOut(BaseModel):
    id: UUID
    template_id: UUID
    revision: int
    subject: str
    body_html: str
    variable_schema: dict[str, Any]
    content_digest: str
    renderer_version: int
    created_at: datetime


class TemplateListItem(BaseModel):
    id: UUID
    workspace_id: UUID
    name: str
    current_version_id: UUID | None = None
    mode: str
    archived_at: datetime | None = None
    version: int
    created_at: datetime
    updated_at: datetime
    current_revision: int | None = None
    subject: str | None = None


class TemplatePage(BaseModel):
    items: list[TemplateListItem]
    next_cursor: str | None = None


class TemplateDetailOut(BaseModel):
    id: UUID
    workspace_id: UUID
    name: str
    current_version_id: UUID | None = None
    mode: str
    archived_at: datetime | None = None
    version: int
    created_at: datetime
    updated_at: datetime
    current_revision: int | None = None
    subject: str
    body_html: str
    variable_schema: dict[str, Any] = Field(default_factory=dict)
    content_digest: str
    renderer_version: int
    version_created_at: datetime | None = None
