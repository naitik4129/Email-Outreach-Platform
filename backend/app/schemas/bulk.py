from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field, field_validator

#: Upper bound for one request. Each item takes a row lock and writes an audit
#: event, so an unbounded batch would hold locks for the whole request.
MAX_BULK_ITEMS = 100


class BulkItemIn(BaseModel):
    id: UUID
    expected_version: int = Field(ge=1)


class BulkActionIn(BaseModel):
    items: list[BulkItemIn] = Field(min_length=1, max_length=MAX_BULK_ITEMS)

    @field_validator("items")
    @classmethod
    def _no_duplicates(cls, items: list[BulkItemIn]) -> list[BulkItemIn]:
        if len({item.id for item in items}) != len(items):
            raise ValueError("Each id may appear only once per request")
        return items


class BulkItemResult(BaseModel):
    id: UUID
    ok: bool
    code: str | None = None
    message: str | None = None


class BulkActionOut(BaseModel):
    results: list[BulkItemResult]
    succeeded: int
    failed: int
