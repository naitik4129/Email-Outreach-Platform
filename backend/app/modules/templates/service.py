from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.api.deps import WorkspaceContext
from app.core.errors import AppError
from app.modules.leads.pagination import decode_cursor, encode_cursor, normalize_limit
from app.modules.leads.repository import LeadRepository
from app.modules.templates.rendering import (
    DEFAULT_SAMPLE_DATA,
    build_lead_render_context,
    render_template_content,
    resolve_variable_value,
)
from app.modules.templates.repository import TemplateRepository
from app.modules.templates.sanitizer import sanitize_html_preview
from app.modules.templates.variables import (
    _PLACEHOLDER_RE,
    parse_and_validate_variables,
    validate_template_content,
)
from app.schemas.templates import (
    TemplateArchiveIn,
    TemplateCreateIn,
    TemplateDetailOut,
    TemplateDuplicateIn,
    TemplateListItem,
    TemplatePage,
    TemplatePreviewIn,
    TemplatePreviewOut,
    TemplateUpdateIn,
    TemplateVersionOut,
)


def _validate_name(name: str) -> str:
    cleaned = name.strip()
    if not cleaned:
        raise AppError("validation_error", "Template name is required", status_code=422)
    if len(cleaned) > 200:
        raise AppError(
            "validation_error",
            "Template name must be 200 characters or fewer",
            status_code=422,
        )
    return cleaned


class TemplateService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = TemplateRepository(session)
        self.leads_repo = LeadRepository(session)

    def create_template(
        self, context: WorkspaceContext, payload: TemplateCreateIn
    ) -> TemplateDetailOut:
        clean_name = _validate_name(payload.name)
        variable_schema = validate_template_content(
            payload.subject, payload.body_html
        )

        row = self.repo.create_template_with_version(
            workspace_id=context.workspace_id,
            name=clean_name,
            subject=payload.subject.strip(),
            body_html=payload.body_html,
            variable_schema=variable_schema,
            actor_id=context.user_id,
        )
        return self._to_detail_out(row)

    def get_template(
        self, context: WorkspaceContext, template_id: UUID
    ) -> TemplateDetailOut:
        row = self.repo.get_template(
            workspace_id=context.workspace_id, template_id=template_id
        )
        if row is None:
            raise AppError("not_found", "Template not found", status_code=404)
        return self._to_detail_out(row)

    def list_templates(
        self,
        context: WorkspaceContext,
        *,
        limit: int,
        cursor: str | None,
        query: str | None,
        status: str,
    ) -> TemplatePage:
        effective_limit = normalize_limit(limit)
        after_id = decode_cursor(cursor)

        if status not in {"ACTIVE", "ARCHIVED", "ALL"}:
            status = "ACTIVE"

        clean_query = query.strip() if query else None

        rows = self.repo.list_templates(
            workspace_id=context.workspace_id,
            limit=effective_limit + 1,
            after_id=after_id,
            status=status,
            query=clean_query,
        )

        has_more = len(rows) > effective_limit
        page_rows = rows[:effective_limit]

        items = [
            TemplateListItem(
                id=r["id"],
                workspace_id=r["workspace_id"],
                name=r["name"],
                current_version_id=r["current_version_id"],
                mode=r["mode"],
                archived_at=r["archived_at"],
                version=r["version"],
                created_at=r["created_at"],
                updated_at=r["updated_at"],
                current_revision=r["current_revision"],
                subject=r["subject"],
            )
            for r in page_rows
        ]

        next_cursor = encode_cursor(page_rows[-1]["id"]) if has_more and page_rows else None
        return TemplatePage(items=items, next_cursor=next_cursor)

    def update_template(
        self,
        context: WorkspaceContext,
        template_id: UUID,
        payload: TemplateUpdateIn,
    ) -> TemplateDetailOut:
        clean_name = _validate_name(payload.name) if payload.name is not None else None

        row = self.repo.update_template(
            workspace_id=context.workspace_id,
            template_id=template_id,
            expected_version=payload.expected_version,
            name=clean_name,
            subject=payload.subject,
            body_html=payload.body_html,
            actor_id=context.user_id,
        )
        if row is None:
            raise AppError("not_found", "Template not found", status_code=404)
        return self._to_detail_out(row)

    def duplicate_template(
        self,
        context: WorkspaceContext,
        template_id: UUID,
        payload: TemplateDuplicateIn,
    ) -> TemplateDetailOut:
        name_override = (
            _validate_name(payload.name) if payload.name is not None else None
        )
        row = self.repo.duplicate_template(
            workspace_id=context.workspace_id,
            source_template_id=template_id,
            actor_id=context.user_id,
            name_override=name_override,
        )
        if row is None:
            raise AppError("not_found", "Template not found", status_code=404)
        return self._to_detail_out(row)

    def archive_template(
        self,
        context: WorkspaceContext,
        template_id: UUID,
        payload: TemplateArchiveIn,
    ) -> TemplateDetailOut:
        row = self.repo.archive_template(
            workspace_id=context.workspace_id,
            template_id=template_id,
            expected_version=payload.expected_version,
            actor_id=context.user_id,
        )
        if row is None:
            raise AppError("not_found", "Template not found", status_code=404)
        return self._to_detail_out(row)

    def preview_template(
        self, context: WorkspaceContext, payload: TemplatePreviewIn
    ) -> TemplatePreviewOut:
        clean_subject = payload.subject.strip()
        if not clean_subject:
            raise AppError("validation_error", "Subject is required", status_code=422)

        # Validate variables in subject and body
        subj_vars, _ = parse_and_validate_variables(clean_subject, "subject")
        body_vars, _ = parse_and_validate_variables(payload.body_html, "body")
        detected_vars = sorted(subj_vars | body_vars)

        render_context: dict[str, Any] = {}
        if payload.lead_id is not None:
            lead_row = self.leads_repo.get_lead(
                workspace_id=context.workspace_id, lead_id=payload.lead_id
            )
            if lead_row is None or lead_row.get("archived_at") is not None:
                raise AppError("not_found", "Lead not found", status_code=404)
            render_context = build_lead_render_context(lead_row)
        else:
            render_context = dict(DEFAULT_SAMPLE_DATA)
            if payload.sample_data:
                render_context.update(payload.sample_data)

        # Check for missing variables (no value in context and no fallback)
        missing: set[str] = set()
        for text_source in (clean_subject, payload.body_html):
            for m in _PLACEHOLDER_RE.finditer(text_source):
                var_name = m.group(1).strip()
                fallback = m.group(2)
                # Same lookup the renderer uses, so aliases and custom.* /
                # custom_fields.* resolve here exactly as they will when sent.
                if fallback is None and not resolve_variable_value(
                    var_name, None, render_context
                ):
                    missing.add(var_name)

        rendered_subject, rendered_body = render_template_content(
            subject=clean_subject,
            body_html=payload.body_html,
            context_data=render_context,
        )

        sanitized_body = sanitize_html_preview(rendered_body)

        return TemplatePreviewOut(
            subject=rendered_subject,
            body_html=sanitized_body,
            detected_variables=detected_vars,
            missing_variables=sorted(missing),
        )

    def get_template_versions(
        self, context: WorkspaceContext, template_id: UUID
    ) -> list[TemplateVersionOut]:
        existing = self.repo.get_template(
            workspace_id=context.workspace_id, template_id=template_id
        )
        if existing is None:
            raise AppError("not_found", "Template not found", status_code=404)

        rows = self.repo.get_template_versions(
            workspace_id=context.workspace_id, template_id=template_id
        )
        return [
            TemplateVersionOut(
                id=r["id"],
                template_id=r["template_id"],
                revision=r["revision"],
                subject=r["subject"],
                body_html=r["body_html"],
                variable_schema=r["variable_schema"] or {},
                content_digest=r["content_digest"],
                renderer_version=r["renderer_version"],
                created_at=r["created_at"],
            )
            for r in rows
        ]

    def _to_detail_out(self, row: Mapping[str, Any]) -> TemplateDetailOut:
        return TemplateDetailOut(
            id=row["id"],
            workspace_id=row["workspace_id"],
            name=row["name"],
            current_version_id=row["current_version_id"],
            mode=row["mode"],
            archived_at=row["archived_at"],
            version=row["version"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            current_revision=row["current_revision"],
            subject=row["subject"] or "",
            body_html=row["body_html"] or "",
            variable_schema=row["variable_schema"] or {},
            content_digest=row["content_digest"] or "",
            renderer_version=row["renderer_version"] or 1,
            version_created_at=row["version_created_at"],
        )
