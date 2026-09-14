from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import RowMapping, text
from sqlalchemy.orm import Session

from app.api.deps import WorkspaceContext
from app.core.errors import AppError
from app.modules.campaigns.repository import CampaignRepository
from app.modules.campaigns.schemas import (
    SequenceOut,
    SequenceStepCreateIn,
    SequenceStepOut,
    SequenceStepsReorderIn,
    SequenceStepUpdateIn,
)
from app.modules.templates.variables import validate_template_content


class SequenceService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = CampaignRepository(session)

    def _require_draft_campaign(
        self, context: WorkspaceContext, campaign_id: UUID
    ) -> RowMapping:
        campaign = self.repo.get_campaign(
            workspace_id=context.workspace_id, campaign_id=campaign_id
        )
        if campaign is None:
            raise AppError("not_found", "Campaign not found", status_code=404)
        if campaign["status"] != "DRAFT":
            raise AppError(
                "state_conflict",
                "Sequence can only be edited while the campaign is DRAFT",
                status_code=409,
            )
        return campaign

    def _ensure_sequence(
        self, context: WorkspaceContext, campaign_id: UUID
    ) -> RowMapping:
        existing = self.repo.get_sequence(
            workspace_id=context.workspace_id, campaign_id=campaign_id
        )
        if existing is not None:
            return existing
        return self.repo.create_sequence(
            workspace_id=context.workspace_id, campaign_id=campaign_id
        )

    def _get_template_version(
        self, context: WorkspaceContext, template_version_id: UUID
    ) -> RowMapping | None:
        return (
            self.session.execute(
                text(
                    """
                    SELECT id, subject, body_html, variable_schema
                    FROM template_versions
                    WHERE workspace_id = :workspace_id AND id = :template_version_id
                    """
                ),
                {
                    "workspace_id": str(context.workspace_id),
                    "template_version_id": str(template_version_id),
                },
            )
            .mappings()
            .first()
        )

    def _resolve_email_content(
        self,
        context: WorkspaceContext,
        *,
        source_template_version_id: UUID | None,
        email_subject: str | None,
        email_body_html: str | None,
    ) -> tuple[str, str, dict[str, Any]]:
        if source_template_version_id is not None:
            version = self._get_template_version(context, source_template_version_id)
            if version is None:
                raise AppError(
                    "validation_error",
                    "Template version not found in this workspace",
                    status_code=422,
                )
            return (
                version["subject"],
                version["body_html"],
                version["variable_schema"] or {},
            )

        if not email_subject or not email_subject.strip():
            raise AppError(
                "validation_error",
                "email_subject is required for an EMAIL step without a "
                "template reference",
                status_code=422,
            )
        body = email_body_html or ""
        variable_schema = validate_template_content(email_subject, body)
        return email_subject.strip(), body, variable_schema

    def get_sequence(self, context: WorkspaceContext, campaign_id: UUID) -> SequenceOut:
        self._require_campaign(context, campaign_id)
        sequence = self.repo.get_sequence(
            workspace_id=context.workspace_id, campaign_id=campaign_id
        )
        if sequence is None:
            return SequenceOut(
                id=None,
                campaign_id=campaign_id,
                revision=0,
                status="EMPTY",
                steps=[],
            )
        steps = self.repo.list_steps_ordered(
            workspace_id=context.workspace_id, sequence_id=UUID(str(sequence["id"]))
        )
        return SequenceOut(
            id=sequence["id"],
            campaign_id=sequence["campaign_id"],
            revision=sequence["revision"],
            status=sequence["status"],
            steps=[self._to_step_out(row) for row in steps],
        )

    def _require_campaign(
        self, context: WorkspaceContext, campaign_id: UUID
    ) -> RowMapping:
        campaign = self.repo.get_campaign(
            workspace_id=context.workspace_id, campaign_id=campaign_id
        )
        if campaign is None:
            raise AppError("not_found", "Campaign not found", status_code=404)
        return campaign

    def add_step(
        self,
        context: WorkspaceContext,
        campaign_id: UUID,
        payload: SequenceStepCreateIn,
    ) -> SequenceStepOut:
        self._require_draft_campaign(context, campaign_id)
        sequence = self._ensure_sequence(context, campaign_id)

        if payload.kind == "EMAIL":
            if payload.wait_duration_minutes is not None:
                raise AppError(
                    "validation_error",
                    "wait_duration_minutes must not be set for an EMAIL step",
                    status_code=422,
                )
            subject, body, variable_schema = self._resolve_email_content(
                context,
                source_template_version_id=payload.source_template_version_id,
                email_subject=payload.email_subject,
                email_body_html=payload.email_body_html,
            )
            row = self.repo.insert_step(
                workspace_id=context.workspace_id,
                sequence_id=UUID(str(sequence["id"])),
                campaign_id=campaign_id,
                position=payload.position,
                kind="EMAIL",
                email_subject=subject,
                email_body_html=body,
                email_variable_schema=variable_schema,
                wait_duration_minutes=None,
                source_template_version_id=payload.source_template_version_id,
            )
        else:
            if payload.wait_duration_minutes is None:
                raise AppError(
                    "validation_error",
                    "wait_duration_minutes is required for a WAIT step",
                    status_code=422,
                )
            if payload.email_subject or payload.email_body_html:
                raise AppError(
                    "validation_error",
                    "A WAIT step must not include email content",
                    status_code=422,
                )
            row = self.repo.insert_step(
                workspace_id=context.workspace_id,
                sequence_id=UUID(str(sequence["id"])),
                campaign_id=campaign_id,
                position=payload.position,
                kind="WAIT",
                email_subject=None,
                email_body_html=None,
                email_variable_schema=None,
                wait_duration_minutes=payload.wait_duration_minutes,
                source_template_version_id=None,
            )
        return self._to_step_out(row)

    def update_step(
        self,
        context: WorkspaceContext,
        campaign_id: UUID,
        step_id: UUID,
        payload: SequenceStepUpdateIn,
    ) -> SequenceStepOut:
        self._require_draft_campaign(context, campaign_id)
        existing = self.repo.get_step(
            workspace_id=context.workspace_id, step_id=step_id
        )
        if existing is None or str(existing["campaign_id"]) != str(campaign_id):
            raise AppError("not_found", "Sequence step not found", status_code=404)

        if existing["kind"] == "EMAIL":
            content_changing = (
                payload.email_subject is not None
                or payload.email_body_html is not None
                or payload.source_template_version_id is not None
            )
            if content_changing:
                subject, body, variable_schema = self._resolve_email_content(
                    context,
                    source_template_version_id=payload.source_template_version_id,
                    email_subject=(
                        payload.email_subject
                        if payload.email_subject is not None
                        else existing["email_subject"]
                    ),
                    email_body_html=(
                        payload.email_body_html
                        if payload.email_body_html is not None
                        else existing["email_body_html"]
                    ),
                )
            else:
                subject, body, variable_schema = None, None, None
            row = self.repo.update_step(
                workspace_id=context.workspace_id,
                step_id=step_id,
                expected_version=payload.expected_version,
                email_subject=subject,
                email_body_html=body,
                email_variable_schema=variable_schema,
                email_variable_schema_provided=content_changing,
                wait_duration_minutes=None,
                source_template_version_id=payload.source_template_version_id,
                source_template_version_id_provided=(
                    payload.source_template_version_id is not None
                ),
            )
        else:
            row = self.repo.update_step(
                workspace_id=context.workspace_id,
                step_id=step_id,
                expected_version=payload.expected_version,
                email_subject=None,
                email_body_html=None,
                email_variable_schema=None,
                email_variable_schema_provided=False,
                wait_duration_minutes=payload.wait_duration_minutes,
                source_template_version_id=None,
                source_template_version_id_provided=False,
            )
        if row is None:
            raise AppError("not_found", "Sequence step not found", status_code=404)
        return self._to_step_out(row)

    def delete_step(
        self, context: WorkspaceContext, campaign_id: UUID, step_id: UUID
    ) -> None:
        self._require_draft_campaign(context, campaign_id)
        sequence = self.repo.get_sequence(
            workspace_id=context.workspace_id, campaign_id=campaign_id
        )
        if sequence is None:
            raise AppError("not_found", "Sequence step not found", status_code=404)
        deleted = self.repo.delete_step(
            workspace_id=context.workspace_id,
            sequence_id=UUID(str(sequence["id"])),
            step_id=step_id,
        )
        if not deleted:
            raise AppError("not_found", "Sequence step not found", status_code=404)

    def reorder_steps(
        self,
        context: WorkspaceContext,
        campaign_id: UUID,
        payload: SequenceStepsReorderIn,
    ) -> SequenceOut:
        self._require_draft_campaign(context, campaign_id)
        sequence = self.repo.get_sequence(
            workspace_id=context.workspace_id, campaign_id=campaign_id
        )
        if sequence is None:
            raise AppError("not_found", "Sequence not found", status_code=404)

        sequence_id = UUID(str(sequence["id"]))
        existing = self.repo.list_steps_ordered(
            workspace_id=context.workspace_id, sequence_id=sequence_id
        )
        existing_ids = {str(row["id"]) for row in existing}

        requested_ids = {str(entry.step_id) for entry in payload.steps}
        if requested_ids != existing_ids:
            raise AppError(
                "validation_error",
                "Reorder must include exactly the current set of sequence steps",
                status_code=422,
            )
        positions = sorted(entry.position for entry in payload.steps)
        if positions != list(range(1, len(existing) + 1)):
            raise AppError(
                "validation_error",
                "Positions must be a contiguous 1..N sequence with no "
                "duplicates or gaps",
                status_code=422,
            )

        ordering = [(entry.step_id, entry.position) for entry in payload.steps]
        self.repo.reorder_steps(
            workspace_id=context.workspace_id,
            sequence_id=sequence_id,
            ordering=ordering,
        )
        return self.get_sequence(context, campaign_id)

    def _to_step_out(self, row: RowMapping) -> SequenceStepOut:
        return SequenceStepOut(
            id=row["id"],
            sequence_id=row["sequence_id"],
            campaign_id=row["campaign_id"],
            position=row["position"],
            kind=row["kind"],
            email_subject=row["email_subject"],
            email_body_html=row["email_body_html"],
            email_variable_schema=row["email_variable_schema"],
            wait_duration_minutes=row["wait_duration_minutes"],
            source_template_version_id=row["source_template_version_id"],
            version=row["version"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
