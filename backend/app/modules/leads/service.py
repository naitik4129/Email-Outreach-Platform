from __future__ import annotations

from collections.abc import Mapping
from typing import Any, NoReturn
from uuid import UUID

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import WorkspaceContext
from app.core.errors import AppError
from app.modules.leads.normalization import (
    clean_optional_text,
    normalize_email,
    validate_custom_fields,
)
from app.modules.leads.pagination import decode_cursor, encode_cursor, normalize_limit
from app.modules.leads.repository import LeadRepository
from app.schemas.leads import (
    ExpectedVersionIn,
    LeadCreateIn,
    LeadDetailOut,
    LeadListCreateIn,
    LeadListItem,
    LeadListMemberCreateIn,
    LeadListMemberOut,
    LeadListMemberPage,
    LeadListOut,
    LeadListPage,
    LeadListSummary,
    LeadListUpdateIn,
    LeadOut,
    LeadPage,
    LeadUpdateIn,
)


def _constraint_name(exc: IntegrityError) -> str:
    original = getattr(exc, "orig", None)
    diag = getattr(original, "diag", None)
    constraint = getattr(diag, "constraint_name", None)
    return str(constraint or "")


def _map_integrity_error(exc: IntegrityError) -> AppError:
    constraint = _constraint_name(exc)
    if constraint == "leads_canonical_address_key":
        return AppError(
            "duplicate_lead",
            "A lead with this email already exists",
            status_code=409,
        )
    if constraint == "lead_list_memberships_pkey":
        return AppError(
            "duplicate_membership",
            "Lead is already in this list",
            status_code=409,
        )
    if constraint in {
        "lead_list_memberships_list_fkey",
        "lead_list_memberships_lead_fkey",
    }:
        return AppError("not_found", "Lead or list not found", status_code=404)
    return AppError("conflict", "Request conflicts with current data", status_code=409)


def _trim_name(name: str) -> str:
    cleaned = name.strip()
    if not cleaned:
        raise AppError("validation_error", "Name is required", status_code=422)
    if len(cleaned) > 200:
        raise AppError(
            "validation_error",
            "Name must be 200 characters or fewer",
            status_code=422,
        )
    return cleaned


def _lead_out(row: Mapping[str, Any]) -> LeadOut:
    return LeadOut(
        id=row["id"],
        workspace_id=row["workspace_id"],
        email=row["original_address"],
        canonical_address=row["canonical_address"],
        normalization_version=row["normalization_version"],
        first_name=row["first_name"],
        last_name=row["last_name"],
        company=row["company"],
        title=row["title"],
        custom_fields=row["custom_fields"],
        status=row["status"],
        validation_status=row["validation_status"],
        validated_at=row["validated_at"],
        contact_revision=row["contact_revision"],
        archived_at=row["archived_at"],
        version=row["version"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _list_out(row: Mapping[str, Any]) -> LeadListOut:
    return LeadListOut(
        id=row["id"],
        workspace_id=row["workspace_id"],
        name=row["name"],
        archived_at=row["archived_at"],
        membership_revision=row["membership_revision"],
        member_count=row["member_count"],
        version=row["version"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


class LeadService:
    def __init__(self, session: Session) -> None:
        self.repo = LeadRepository(session)

    def create_lead(self, context: WorkspaceContext, payload: LeadCreateIn) -> LeadOut:
        email = normalize_email(payload.email)
        custom_fields = validate_custom_fields(payload.custom_fields)
        try:
            self.repo.ensure_recipient_address(
                workspace_id=context.workspace_id,
                canonical_address=email.canonical,
                original_display=email.original,
            )
            row = self.repo.create_lead(
                workspace_id=context.workspace_id,
                original_address=email.original,
                canonical_address=email.canonical,
                first_name=clean_optional_text(payload.first_name, "First name"),
                last_name=clean_optional_text(payload.last_name, "Last name"),
                company=clean_optional_text(payload.company, "Company"),
                title=clean_optional_text(payload.title, "Title"),
                custom_fields=custom_fields,
            )
            if payload.list_id is not None:
                self._assert_active_list(context.workspace_id, payload.list_id)
                self.repo.add_member(
                    workspace_id=context.workspace_id,
                    list_id=payload.list_id,
                    lead_id=row["id"],
                    actor_id=context.user_id,
                )
            return _lead_out(row)
        except IntegrityError as exc:
            raise _map_integrity_error(exc) from exc

    def list_leads(
        self,
        context: WorkspaceContext,
        *,
        limit: int,
        cursor: str | None,
        query: str | None,
        status: str,
        list_id: UUID | None,
    ) -> LeadPage:
        effective_limit = normalize_limit(limit)
        after_id = decode_cursor(cursor)
        cleaned_query = query.strip() if query else None
        if cleaned_query and len(cleaned_query) > 100:
            raise AppError(
                "validation_error",
                "Search must be 100 characters or fewer",
                status_code=422,
            )
        if status not in {"ACTIVE", "ARCHIVED", "ALL"}:
            raise AppError(
                "validation_error",
                "Unsupported lead status",
                status_code=422,
            )
        rows = self.repo.list_leads(
            workspace_id=context.workspace_id,
            limit=effective_limit + 1,
            after_id=after_id,
            status=None if status == "ALL" else status,
            query=cleaned_query,
            list_id=list_id,
        )
        page_rows = rows[:effective_limit]
        items = [
            LeadListItem(**_lead_out(row).model_dump(), list_count=row["list_count"])
            for row in page_rows
        ]
        next_cursor = (
            encode_cursor(page_rows[-1]["id"]) if len(rows) > effective_limit else None
        )
        return LeadPage(items=items, next_cursor=next_cursor)

    def get_lead_detail(
        self, context: WorkspaceContext, lead_id: UUID
    ) -> LeadDetailOut:
        row = self.repo.get_lead(workspace_id=context.workspace_id, lead_id=lead_id)
        if row is None:
            raise AppError("not_found", "Lead not found", status_code=404)
        lists = self.repo.get_lead_lists(
            workspace_id=context.workspace_id,
            lead_id=lead_id,
        )
        return LeadDetailOut(
            **_lead_out(row).model_dump(),
            lists=[
                LeadListSummary(
                    id=item["id"],
                    name=item["name"],
                    archived_at=item["archived_at"],
                )
                for item in lists
            ],
        )

    def update_lead(
        self, context: WorkspaceContext, lead_id: UUID, payload: LeadUpdateIn
    ) -> LeadOut:
        values: dict[str, Any] = {}
        if payload.email is not None:
            email = normalize_email(payload.email)
            values.update(
                {
                    "original_address": email.original,
                    "canonical_address": email.canonical,
                    "normalization_version": email.normalization_version,
                }
            )
            self.repo.ensure_recipient_address(
                workspace_id=context.workspace_id,
                canonical_address=email.canonical,
                original_display=email.original,
            )
        for attr, label in (
            ("first_name", "First name"),
            ("last_name", "Last name"),
            ("company", "Company"),
            ("title", "Title"),
        ):
            if attr in payload.model_fields_set:
                values[attr] = clean_optional_text(getattr(payload, attr), label)
        if payload.custom_fields is not None:
            values["custom_fields"] = validate_custom_fields(payload.custom_fields)
        if payload.validation_status is not None:
            values["validation_status"] = payload.validation_status
        if not values:
            raise AppError(
                "validation_error",
                "No mutable fields supplied",
                status_code=422,
            )

        try:
            row = self.repo.update_lead(
                workspace_id=context.workspace_id,
                lead_id=lead_id,
                expected_version=payload.expected_version,
                values=values,
            )
        except IntegrityError as exc:
            raise _map_integrity_error(exc) from exc
        if row is None:
            self._raise_not_found_or_stale(context.workspace_id, lead_id, "Lead")
        return _lead_out(row)

    def archive_lead(
        self, context: WorkspaceContext, lead_id: UUID, payload: ExpectedVersionIn
    ) -> LeadOut:
        row = self.repo.update_lead(
            workspace_id=context.workspace_id,
            lead_id=lead_id,
            expected_version=payload.expected_version,
            values={"status": "ARCHIVED", "archived_at": "now"},
        )
        if row is None:
            self._raise_not_found_or_stale(context.workspace_id, lead_id, "Lead")
        return _lead_out(row)

    def create_list(
        self, context: WorkspaceContext, payload: LeadListCreateIn
    ) -> LeadListOut:
        row = self.repo.create_list(
            workspace_id=context.workspace_id,
            name=_trim_name(payload.name),
        )
        return _list_out({**row, "member_count": 0})

    def list_lists(
        self, context: WorkspaceContext, *, limit: int, cursor: str | None
    ) -> LeadListPage:
        effective_limit = normalize_limit(limit)
        rows = self.repo.list_lists(
            workspace_id=context.workspace_id,
            limit=effective_limit + 1,
            after_id=decode_cursor(cursor),
        )
        page_rows = rows[:effective_limit]
        next_cursor = (
            encode_cursor(page_rows[-1]["id"]) if len(rows) > effective_limit else None
        )
        return LeadListPage(
            items=[_list_out(row) for row in page_rows],
            next_cursor=next_cursor,
        )

    def get_list(self, context: WorkspaceContext, list_id: UUID) -> LeadListOut:
        row = self.repo.get_list(workspace_id=context.workspace_id, list_id=list_id)
        if row is None:
            raise AppError("not_found", "Lead list not found", status_code=404)
        return _list_out(row)

    def update_list(
        self, context: WorkspaceContext, list_id: UUID, payload: LeadListUpdateIn
    ) -> LeadListOut:
        if payload.name is None:
            raise AppError(
                "validation_error",
                "No mutable fields supplied",
                status_code=422,
            )
        row = self.repo.update_list(
            workspace_id=context.workspace_id,
            list_id=list_id,
            expected_version=payload.expected_version,
            name=_trim_name(payload.name),
        )
        if row is None:
            self._raise_list_not_found_or_stale(context.workspace_id, list_id)
        return _list_out(row)

    def archive_list(
        self, context: WorkspaceContext, list_id: UUID, payload: ExpectedVersionIn
    ) -> LeadListOut:
        row = self.repo.update_list(
            workspace_id=context.workspace_id,
            list_id=list_id,
            expected_version=payload.expected_version,
            archive=True,
        )
        if row is None:
            self._raise_list_not_found_or_stale(context.workspace_id, list_id)
        return _list_out(row)

    def add_member(
        self, context: WorkspaceContext, list_id: UUID, payload: LeadListMemberCreateIn
    ) -> None:
        self._assert_active_list(context.workspace_id, list_id)
        self._assert_active_lead(context.workspace_id, payload.lead_id)
        try:
            self.repo.add_member(
                workspace_id=context.workspace_id,
                list_id=list_id,
                lead_id=payload.lead_id,
                actor_id=context.user_id,
            )
        except IntegrityError as exc:
            raise _map_integrity_error(exc) from exc

    def remove_member(
        self, context: WorkspaceContext, list_id: UUID, lead_id: UUID
    ) -> None:
        self._assert_active_list(context.workspace_id, list_id)
        self.repo.remove_member(
            workspace_id=context.workspace_id,
            list_id=list_id,
            lead_id=lead_id,
        )

    def list_members(
        self,
        context: WorkspaceContext,
        list_id: UUID,
        *,
        limit: int,
        cursor: str | None,
    ) -> LeadListMemberPage:
        self.get_list(context, list_id)
        effective_limit = normalize_limit(limit)
        rows = self.repo.list_members(
            workspace_id=context.workspace_id,
            list_id=list_id,
            limit=effective_limit + 1,
            after_id=decode_cursor(cursor),
        )
        page_rows = rows[:effective_limit]
        members = [
            LeadListMemberOut(
                lead=_lead_out(row),
                added_by=row["added_by"],
                added_at=row["added_at"],
            )
            for row in page_rows
        ]
        next_cursor = (
            encode_cursor(page_rows[-1]["id"]) if len(rows) > effective_limit else None
        )
        return LeadListMemberPage(items=members, next_cursor=next_cursor)

    def _assert_active_lead(self, workspace_id: UUID, lead_id: UUID) -> None:
        row = self.repo.get_lead(workspace_id=workspace_id, lead_id=lead_id)
        if row is None or row["status"] != "ACTIVE":
            raise AppError("not_found", "Lead not found", status_code=404)

    def _assert_active_list(self, workspace_id: UUID, list_id: UUID) -> None:
        row = self.repo.get_list(workspace_id=workspace_id, list_id=list_id)
        if row is None or row["archived_at"] is not None:
            raise AppError("not_found", "Lead list not found", status_code=404)

    def _raise_not_found_or_stale(
        self, workspace_id: UUID, lead_id: UUID, label: str
    ) -> NoReturn:
        if self.repo.get_lead(workspace_id=workspace_id, lead_id=lead_id) is None:
            raise AppError("not_found", f"{label} not found", status_code=404)
        raise AppError(
            "version_conflict",
            f"{label} was modified by another request; reload and retry",
            status_code=409,
        )

    def _raise_list_not_found_or_stale(
        self, workspace_id: UUID, list_id: UUID
    ) -> NoReturn:
        if self.repo.get_list(workspace_id=workspace_id, list_id=list_id) is None:
            raise AppError("not_found", "Lead list not found", status_code=404)
        raise AppError(
            "version_conflict",
            "Lead list was modified by another request; reload and retry",
            status_code=409,
        )
