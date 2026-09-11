from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping, Sequence
from typing import Any, cast
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.errors import AppError
from app.modules.templates.variables import validate_template_content


def _compute_digest(subject: str, body_html: str) -> str:
    payload = f"{subject}\n\n{body_html}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


class TemplateRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create_template_with_version(
        self,
        *,
        workspace_id: UUID,
        name: str,
        subject: str,
        body_html: str,
        variable_schema: dict[str, Any],
        actor_id: UUID,
    ) -> Mapping[str, Any]:
        template_id = uuid.uuid4()
        version_id = uuid.uuid4()
        digest = _compute_digest(subject, body_html)

        # 1. Insert template without current_version_id to satisfy FK
        template_row = (
            self.session.execute(
                text(
                    """
                    INSERT INTO templates
                        (id, workspace_id, name, current_version_id, mode)
                    VALUES
                        (:id, :workspace_id, :name, NULL, 'STANDARD')
                    RETURNING *
                    """
                ),
                {
                    "id": str(template_id),
                    "workspace_id": str(workspace_id),
                    "name": name,
                },
            )
            .mappings()
            .one()
        )

        # 2. Insert initial version (revision 1)
        version_row = (
            self.session.execute(
                text(
                    """
                    INSERT INTO template_versions
                        (id, workspace_id, template_id, revision, subject,
                         body_html, variable_schema, content_digest,
                         renderer_version)
                    VALUES
                        (:id, :workspace_id, :template_id, 1, :subject,
                         :body_html, CAST(:variable_schema AS jsonb),
                         :content_digest, 1)
                    RETURNING *
                    """
                ),
                {
                    "id": str(version_id),
                    "workspace_id": str(workspace_id),
                    "template_id": str(template_id),
                    "subject": subject,
                    "body_html": body_html,
                    "variable_schema": json.dumps(variable_schema),
                    "content_digest": digest,
                },
            )
            .mappings()
            .one()
        )

        # 3. Update current_version_id pointer (triggers version bump via templates_touch_row)
        updated_template = (
            self.session.execute(
                text(
                    """
                    UPDATE templates
                    SET current_version_id = :version_id
                    WHERE id = :id AND workspace_id = :workspace_id
                    RETURNING *
                    """
                ),
                {
                    "id": str(template_id),
                    "workspace_id": str(workspace_id),
                    "version_id": str(version_id),
                },
            )
            .mappings()
            .one()
        )

        # 4. Record audit event
        self._record_audit_event(
            workspace_id=workspace_id,
            actor_id=actor_id,
            action="template.create",
            target_id=template_id,
            after_state={
                "name": name,
                "version": updated_template["version"],
                "revision": 1,
            },
        )

        return self._combine_template_and_version(updated_template, version_row)

    def get_template(
        self, *, workspace_id: UUID, template_id: UUID
    ) -> Mapping[str, Any] | None:
        row = (
            self.session.execute(
                text(
                    """
                    SELECT
                        t.id, t.workspace_id, t.name, t.current_version_id,
                        t.mode, t.archived_at, t.version, t.created_at,
                        t.updated_at,
                        v.revision AS current_revision, v.subject, v.body_html,
                        v.variable_schema, v.content_digest,
                        v.renderer_version,
                        v.created_at AS version_created_at
                    FROM templates t
                    LEFT JOIN template_versions v
                        ON v.workspace_id = t.workspace_id
                        AND v.template_id = t.id
                        AND v.id = t.current_version_id
                    WHERE t.id = :template_id
                      AND t.workspace_id = :workspace_id
                    """
                ),
                {
                    "template_id": str(template_id),
                    "workspace_id": str(workspace_id),
                },
            )
            .mappings()
            .first()
        )
        return cast(Mapping[str, Any] | None, row)

    def list_templates(
        self,
        *,
        workspace_id: UUID,
        limit: int,
        after_id: UUID | None,
        status: str | None,
        query: str | None,
    ) -> Sequence[Mapping[str, Any]]:
        clauses = ["t.workspace_id = :workspace_id"]
        params: dict[str, Any] = {
            "workspace_id": str(workspace_id),
            "limit": limit,
        }

        if after_id is not None:
            clauses.append("t.id > :after_id")
            params["after_id"] = str(after_id)

        if status == "ACTIVE":
            clauses.append("t.archived_at IS NULL")
        elif status == "ARCHIVED":
            clauses.append("t.archived_at IS NOT NULL")
        # if 'ALL', do not add archived_at filter

        if query:
            clauses.append(
                "(t.name ILIKE :query_pattern OR v.subject ILIKE :query_pattern)"
            )
            params["query_pattern"] = f"%{query}%"

        where_sql = " AND ".join(clauses)
        sql = f"""
            SELECT
                t.id, t.workspace_id, t.name, t.current_version_id,
                t.mode, t.archived_at, t.version, t.created_at,
                t.updated_at,
                v.revision AS current_revision, v.subject
            FROM templates t
            LEFT JOIN template_versions v
                ON v.workspace_id = t.workspace_id
                AND v.template_id = t.id
                AND v.id = t.current_version_id
            WHERE {where_sql}
            ORDER BY t.id ASC
            LIMIT :limit
        """
        rows = self.session.execute(text(sql), params).mappings().all()
        return cast(Sequence[Mapping[str, Any]], rows)

    def update_template(
        self,
        *,
        workspace_id: UUID,
        template_id: UUID,
        expected_version: int,
        name: str | None,
        subject: str | None,
        body_html: str | None,
        actor_id: UUID,
    ) -> Mapping[str, Any] | None:
        # Lock and verify existing record
        existing = (
            self.session.execute(
                text(
                    """
                    SELECT
                        t.id, t.workspace_id, t.name, t.current_version_id,
                        t.mode, t.archived_at, t.version,
                        v.revision AS current_revision, v.subject, v.body_html,
                        v.variable_schema
                    FROM templates t
                    LEFT JOIN template_versions v
                        ON v.workspace_id = t.workspace_id
                        AND v.template_id = t.id
                        AND v.id = t.current_version_id
                    WHERE t.id = :template_id AND t.workspace_id = :workspace_id
                    FOR UPDATE OF t
                    """
                ),
                {
                    "template_id": str(template_id),
                    "workspace_id": str(workspace_id),
                },
            )
            .mappings()
            .first()
        )

        if existing is None:
            return None

        if existing["archived_at"] is not None:
            raise AppError("conflict", "Cannot edit an archived template", status_code=409)

        if existing["version"] != expected_version:
            raise AppError(
                "conflict",
                "Template has been modified by another user. Please refresh and try again.",
                status_code=409,
            )

        new_name = name.strip() if name is not None else existing["name"]
        curr_subject = existing["subject"] or ""
        curr_body = existing["body_html"] or ""
        new_subject = subject.strip() if subject is not None else curr_subject
        new_body = body_html if body_html is not None else curr_body

        content_changed = (new_subject != curr_subject) or (new_body != curr_body)

        version_id = existing["current_version_id"]
        if content_changed:
            # Validate new content
            variable_schema = validate_template_content(new_subject, new_body)
            new_version_id = uuid.uuid4()
            new_revision = (existing["current_revision"] or 0) + 1
            digest = _compute_digest(new_subject, new_body)

            self.session.execute(
                text(
                    """
                    INSERT INTO template_versions
                        (id, workspace_id, template_id, revision, subject,
                         body_html, variable_schema, content_digest,
                         renderer_version)
                    VALUES
                        (:id, :workspace_id, :template_id, :revision, :subject,
                         :body_html, CAST(:variable_schema AS jsonb),
                         :content_digest, 1)
                    """
                ),
                {
                    "id": str(new_version_id),
                    "workspace_id": str(workspace_id),
                    "template_id": str(template_id),
                    "revision": new_revision,
                    "subject": new_subject,
                    "body_html": new_body,
                    "variable_schema": json.dumps(variable_schema),
                    "content_digest": digest,
                },
            )
            version_id = new_version_id

        # Update templates row (triggers version bump)
        updated = (
            self.session.execute(
                text(
                    """
                    UPDATE templates
                    SET name = :name, current_version_id = :current_version_id
                    WHERE id = :id AND workspace_id = :workspace_id
                      AND version = :expected_version
                    RETURNING *
                    """
                ),
                {
                    "id": str(template_id),
                    "workspace_id": str(workspace_id),
                    "name": new_name,
                    "current_version_id": str(version_id),
                    "expected_version": expected_version,
                },
            )
            .mappings()
            .first()
        )

        if updated is None:
            raise AppError(
                "conflict",
                "Template was concurrently updated. Please refresh and try again.",
                status_code=409,
            )

        self._record_audit_event(
            workspace_id=workspace_id,
            actor_id=actor_id,
            action="template.update",
            target_id=template_id,
            before_state={"name": existing["name"], "version": existing["version"]},
            after_state={"name": new_name, "version": updated["version"]},
        )

        return self.get_template(workspace_id=workspace_id, template_id=template_id)

    def duplicate_template(
        self,
        *,
        workspace_id: UUID,
        source_template_id: UUID,
        actor_id: UUID,
        name_override: str | None = None,
    ) -> Mapping[str, Any] | None:
        source = self.get_template(
            workspace_id=workspace_id, template_id=source_template_id
        )
        if source is None or source["archived_at"] is not None:
            return None

        dup_name = (
            name_override.strip()
            if name_override and name_override.strip()
            else f"{source['name']} (Copy)"
        )[:200]

        return self.create_template_with_version(
            workspace_id=workspace_id,
            name=dup_name,
            subject=source["subject"],
            body_html=source["body_html"],
            variable_schema=source["variable_schema"] or {},
            actor_id=actor_id,
        )

    def archive_template(
        self,
        *,
        workspace_id: UUID,
        template_id: UUID,
        expected_version: int,
        actor_id: UUID,
    ) -> Mapping[str, Any] | None:
        existing = (
            self.session.execute(
                text(
                    """
                    SELECT id, workspace_id, name, version, archived_at
                    FROM templates
                    WHERE id = :template_id AND workspace_id = :workspace_id
                    FOR UPDATE
                    """
                ),
                {
                    "template_id": str(template_id),
                    "workspace_id": str(workspace_id),
                },
            )
            .mappings()
            .first()
        )

        if existing is None:
            return None

        if existing["archived_at"] is not None:
            # Already archived, return current state
            return self.get_template(workspace_id=workspace_id, template_id=template_id)

        if existing["version"] != expected_version:
            raise AppError(
                "conflict",
                "Template has been modified by another user. Please refresh and try again.",
                status_code=409,
            )

        updated = (
            self.session.execute(
                text(
                    """
                    UPDATE templates
                    SET archived_at = pg_catalog.transaction_timestamp()
                    WHERE id = :template_id AND workspace_id = :workspace_id
                      AND version = :expected_version
                    RETURNING *
                    """
                ),
                {
                    "template_id": str(template_id),
                    "workspace_id": str(workspace_id),
                    "expected_version": expected_version,
                },
            )
            .mappings()
            .first()
        )

        if updated is None:
            raise AppError(
                "conflict",
                "Template was concurrently modified. Please refresh and try again.",
                status_code=409,
            )

        self._record_audit_event(
            workspace_id=workspace_id,
            actor_id=actor_id,
            action="template.archive",
            target_id=template_id,
            before_state={"archived_at": None, "version": existing["version"]},
            after_state={"archived_at": "now", "version": updated["version"]},
        )

        return self.get_template(workspace_id=workspace_id, template_id=template_id)

    def get_template_versions(
        self, *, workspace_id: UUID, template_id: UUID
    ) -> Sequence[Mapping[str, Any]]:
        rows = (
            self.session.execute(
                text(
                    """
                    SELECT
                        id, workspace_id, template_id, revision, subject,
                        body_html, variable_schema, content_digest,
                        renderer_version, created_at
                    FROM template_versions
                    WHERE template_id = :template_id
                      AND workspace_id = :workspace_id
                    ORDER BY revision DESC
                    """
                ),
                {
                    "template_id": str(template_id),
                    "workspace_id": str(workspace_id),
                },
            )
            .mappings()
            .all()
        )
        return cast(Sequence[Mapping[str, Any]], rows)

    def _record_audit_event(
        self,
        *,
        workspace_id: UUID,
        actor_id: UUID,
        action: str,
        target_id: UUID,
        before_state: dict[str, Any] | None = None,
        after_state: dict[str, Any] | None = None,
    ) -> None:
        self.session.execute(
            text(
                """
                INSERT INTO audit_events
                    (workspace_id, actor_kind, actor_id, action, target_type,
                     target_id, before_state, after_state)
                VALUES
                    (:workspace_id, 'USER', :actor_id, :action, 'template',
                     :target_id,
                     CAST(:before_state AS jsonb),
                     CAST(:after_state AS jsonb))
                """
            ),
            {
                "workspace_id": str(workspace_id),
                "actor_id": str(actor_id),
                "action": action,
                "target_id": str(target_id),
                "before_state": json.dumps(before_state) if before_state else None,
                "after_state": json.dumps(after_state) if after_state else None,
            },
        )

    def _combine_template_and_version(
        self, template: Mapping[str, Any], version: Mapping[str, Any]
    ) -> Mapping[str, Any]:
        result = dict(template)
        result["current_revision"] = version["revision"]
        result["subject"] = version["subject"]
        result["body_html"] = version["body_html"]
        result["variable_schema"] = version["variable_schema"]
        result["content_digest"] = version["content_digest"]
        result["renderer_version"] = version["renderer_version"]
        result["version_created_at"] = version["created_at"]
        return result
