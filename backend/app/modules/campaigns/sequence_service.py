from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from sqlalchemy import RowMapping, text
from sqlalchemy.orm import Session

from app.api.deps import WorkspaceContext
from app.core.errors import AppError
from app.modules.campaigns.attachment_service import to_attachment_out
from app.modules.campaigns.attachments import extract_cid_references
from app.modules.campaigns.repository import CampaignRepository
from app.modules.campaigns.schemas import (
    PreviewRecipientOut,
    PreviewRecipientsOut,
    SequenceOut,
    SequenceStepCreateIn,
    SequenceStepOut,
    SequenceStepsReorderIn,
    SequenceStepUpdateIn,
)
from app.modules.templates.sanitizer import sanitize_email_html
from app.modules.templates.variables import validate_template_content

_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")
# Wait inserted by "duplicate step" when the source step has no following wait.
_DEFAULT_DUPLICATE_WAIT_MINUTES = 2 * 24 * 60


def _normalize_preheader(preheader: str | None) -> str | None:
    """Trim; empty means "no pre-header". Control characters are rejected here so
    the user gets a 422 instead of a CHECK-constraint failure."""
    if preheader is None:
        return None
    cleaned = preheader.strip()
    if not cleaned:
        return None
    if _CONTROL_CHARS_RE.search(cleaned):
        raise AppError(
            "validation_error",
            "Pre-header must not contain control characters",
            status_code=422,
        )
    return cleaned


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
                    SELECT id, subject, body_html, preheader, variable_schema
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
        email_preheader: str | None = None,
        sanitize_body: bool = True,
    ) -> tuple[str, str, str | None, dict[str, Any]]:
        """Return (subject, body, preheader, variable_schema) for an EMAIL step.

        A template version supplies defaults only: any explicitly provided field
        wins, so edits made after picking a template are never silently replaced
        by the template's text. The template id stays as provenance. Content
        entering a step is always sanitized (allow-list) before it is stored.
        For the pre-header, None means "use the template's / none" and an empty
        string clears it.
        """
        subject = email_subject
        body = email_body_html
        preheader = email_preheader
        if source_template_version_id is not None:
            version = self._get_template_version(context, source_template_version_id)
            if version is None:
                raise AppError(
                    "validation_error",
                    "Template version not found in this workspace",
                    status_code=422,
                )
            if subject is None:
                subject = version["subject"]
            if body is None:
                body = version["body_html"]
            if preheader is None:
                preheader = version["preheader"]

        if not subject or not subject.strip():
            raise AppError(
                "validation_error",
                "email_subject is required for an EMAIL step without a "
                "template reference",
                status_code=422,
            )
        clean_body = sanitize_email_html(body or "") if sanitize_body else (body or "")
        clean_preheader = _normalize_preheader(preheader)
        variable_schema = validate_template_content(
            subject, clean_body, clean_preheader
        )
        return subject.strip(), clean_body, clean_preheader, variable_schema

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
        by_step: dict[str, list[Any]] = {}
        for attachment in self.repo.list_sequence_attachments(
            workspace_id=context.workspace_id, sequence_id=UUID(str(sequence["id"]))
        ):
            by_step.setdefault(str(attachment["step_id"]), []).append(attachment)
        return SequenceOut(
            id=sequence["id"],
            campaign_id=sequence["campaign_id"],
            revision=sequence["revision"],
            status=sequence["status"],
            steps=[
                self._to_step_out(row, by_step.get(str(row["id"]), []))
                for row in steps
            ],
        )

    def list_preview_recipients(
        self,
        context: WorkspaceContext,
        campaign_id: UUID,
        *,
        limit: int,
        after_ordinal: int | None,
    ) -> PreviewRecipientsOut:
        """Prospects the sequence preview can render against: the campaign's
        activated audience, else its committed/latest READY one. Each item
        carries the frozen variables the real message renders with."""
        campaign = self._require_campaign(context, campaign_id)
        audience_id = campaign["activated_audience_id"] or campaign["draft_audience_id"]
        audience = None
        if audience_id is not None:
            audience = self.repo.get_audience(
                workspace_id=context.workspace_id,
                campaign_id=campaign_id,
                audience_id=UUID(str(audience_id)),
            )
        else:
            audience = self.repo.get_latest_audience(
                workspace_id=context.workspace_id, campaign_id=campaign_id
            )
        if audience is None or audience["status"] != "READY":
            return PreviewRecipientsOut(source="NONE", items=[], total=0)

        audience_uuid = UUID(str(audience["id"]))
        rows = self.repo.list_accepted_audience_members(
            workspace_id=context.workspace_id,
            audience_id=audience_uuid,
            after_ordinal=after_ordinal,
            limit=limit + 1,
        )
        page = rows[:limit]
        items = []
        for row in page:
            variables = dict(row["frozen_variables"] or {})
            items.append(
                PreviewRecipientOut(
                    audience_member_id=row["id"],
                    lead_id=row["lead_id"],
                    email=variables.get("email"),
                    first_name=variables.get("first_name"),
                    last_name=variables.get("last_name"),
                    company=variables.get("company"),
                    variables=variables,
                )
            )
        counts = self.repo.get_audience_member_counts(
            workspace_id=context.workspace_id, audience_id=audience_uuid
        )
        return PreviewRecipientsOut(
            source="AUDIENCE",
            items=items,
            total=counts["accepted"],
            next_cursor=(
                int(page[-1]["capture_ordinal"]) if len(rows) > limit and page else None
            ),
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
            subject, body, preheader, variable_schema = self._resolve_email_content(
                context,
                source_template_version_id=payload.source_template_version_id,
                email_subject=payload.email_subject,
                email_body_html=payload.email_body_html,
                email_preheader=payload.email_preheader,
            )
            # A brand-new step has no uploaded images yet.
            self._validate_cid_references(context, None, body)
            position = payload.position
            if payload.leading_wait_minutes is not None:
                # Same transaction as the email insert below: a failure rolls
                # both back, so the sequence never gains a dangling WAIT.
                wait_row = self.repo.insert_step(
                    workspace_id=context.workspace_id,
                    sequence_id=UUID(str(sequence["id"])),
                    campaign_id=campaign_id,
                    position=position,
                    kind="WAIT",
                    email_subject=None,
                    email_body_html=None,
                    email_variable_schema=None,
                    wait_duration_minutes=payload.leading_wait_minutes,
                    source_template_version_id=None,
                )
                position = wait_row["position"] + 1
            row = self.repo.insert_step(
                workspace_id=context.workspace_id,
                sequence_id=UUID(str(sequence["id"])),
                campaign_id=campaign_id,
                position=position,
                kind="EMAIL",
                email_subject=subject,
                email_body_html=body,
                email_preheader=preheader,
                email_variable_schema=variable_schema,
                wait_duration_minutes=None,
                source_template_version_id=payload.source_template_version_id,
            )
        else:
            if payload.leading_wait_minutes is not None:
                raise AppError(
                    "validation_error",
                    "leading_wait_minutes is only valid for an EMAIL step",
                    status_code=422,
                )
            if payload.wait_duration_minutes is None:
                raise AppError(
                    "validation_error",
                    "wait_duration_minutes is required for a WAIT step",
                    status_code=422,
                )
            if (
                payload.email_subject
                or payload.email_body_html
                or payload.email_preheader
            ):
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
                or payload.email_preheader is not None
                or payload.source_template_version_id is not None
            )
            if content_changing:
                if payload.source_template_version_id is not None:
                    # Picking a template: template supplies whatever the caller
                    # did not explicitly send.
                    subject_in = payload.email_subject
                    body_in = payload.email_body_html
                    preheader_in = payload.email_preheader
                else:
                    subject_in = (
                        payload.email_subject
                        if payload.email_subject is not None
                        else existing["email_subject"]
                    )
                    body_in = (
                        payload.email_body_html
                        if payload.email_body_html is not None
                        else existing["email_body_html"]
                    )
                    preheader_in = (
                        payload.email_preheader
                        if payload.email_preheader is not None
                        else existing["email_preheader"]
                    )
                subject, body, preheader, variable_schema = (
                    self._resolve_email_content(
                        context,
                        source_template_version_id=payload.source_template_version_id,
                        email_subject=subject_in,
                        email_body_html=body_in,
                        email_preheader=preheader_in,
                        # A subject/pre-header-only save must not rewrite a
                        # legacy body through the allow-list.
                        sanitize_body=(
                            payload.email_body_html is not None
                            or payload.source_template_version_id is not None
                        ),
                    )
                )
                self._validate_cid_references(context, step_id, body)
            else:
                subject, body, preheader, variable_schema = None, None, None, None
            row = self.repo.update_step(
                workspace_id=context.workspace_id,
                step_id=step_id,
                expected_version=payload.expected_version,
                email_subject=subject,
                email_body_html=body,
                email_preheader=preheader,
                email_preheader_provided=content_changing,
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
        return self._to_step_out(
            row,
            self.repo.list_step_attachments(
                workspace_id=context.workspace_id, step_id=step_id
            ),
        )

    def delete_step(
        self,
        context: WorkspaceContext,
        campaign_id: UUID,
        step_id: UUID,
        *,
        with_adjacent_wait: bool = False,
    ) -> None:
        """Delete a step. With `with_adjacent_wait`, deleting an EMAIL step also
        removes the WAIT before it (or the one after it when it is first), in the
        same transaction, so Email/Wait alternation is not left broken."""
        self._require_draft_campaign(context, campaign_id)
        sequence = self.repo.get_sequence(
            workspace_id=context.workspace_id, campaign_id=campaign_id
        )
        if sequence is None:
            raise AppError("not_found", "Sequence step not found", status_code=404)
        if with_adjacent_wait:
            steps = self.repo.list_steps_ordered(
                workspace_id=context.workspace_id,
                sequence_id=UUID(str(sequence["id"])),
            )
            index = next(
                (i for i, s in enumerate(steps) if str(s["id"]) == str(step_id)), None
            )
            if index is not None and steps[index]["kind"] == "EMAIL":
                neighbour = None
                if index > 0 and steps[index - 1]["kind"] == "WAIT":
                    neighbour = steps[index - 1]
                elif index + 1 < len(steps) and steps[index + 1]["kind"] == "WAIT":
                    neighbour = steps[index + 1]
                if neighbour is not None:
                    self.repo.delete_step(
                        workspace_id=context.workspace_id,
                        sequence_id=UUID(str(sequence["id"])),
                        step_id=UUID(str(neighbour["id"])),
                    )
        deleted = self.repo.delete_step(
            workspace_id=context.workspace_id,
            sequence_id=UUID(str(sequence["id"])),
            step_id=step_id,
        )
        if not deleted:
            raise AppError("not_found", "Sequence step not found", status_code=404)

    def duplicate_step(
        self, context: WorkspaceContext, campaign_id: UUID, step_id: UUID
    ) -> SequenceOut:
        """Copy an EMAIL step directly after itself.

        The copy is inserted as a WAIT + EMAIL pair so the documented strict
        Email/Wait alternation (enforced by preflight) still holds. The wait
        reuses the duration of the WAIT that follows the source, if any. Both
        inserts run in the request transaction, so they commit or roll back
        together.
        """
        self._require_draft_campaign(context, campaign_id)
        source = self.repo.get_step(workspace_id=context.workspace_id, step_id=step_id)
        if source is None or str(source["campaign_id"]) != str(campaign_id):
            raise AppError("not_found", "Sequence step not found", status_code=404)
        if source["kind"] != "EMAIL":
            raise AppError(
                "validation_error",
                "Only an EMAIL step can be duplicated",
                status_code=422,
            )

        sequence_id = UUID(str(source["sequence_id"]))
        steps = self.repo.list_steps_ordered(
            workspace_id=context.workspace_id, sequence_id=sequence_id
        )
        following = next(
            (s for s in steps if s["position"] == source["position"] + 1), None
        )
        wait_minutes = (
            following["wait_duration_minutes"]
            if following is not None and following["kind"] == "WAIT"
            else _DEFAULT_DUPLICATE_WAIT_MINUTES
        )

        wait_row = self.repo.insert_step(
            workspace_id=context.workspace_id,
            sequence_id=sequence_id,
            campaign_id=campaign_id,
            position=source["position"] + 1,
            kind="WAIT",
            email_subject=None,
            email_body_html=None,
            email_variable_schema=None,
            wait_duration_minutes=wait_minutes,
            source_template_version_id=None,
        )
        copy = self.repo.insert_step(
            workspace_id=context.workspace_id,
            sequence_id=sequence_id,
            campaign_id=campaign_id,
            position=wait_row["position"] + 1,
            kind="EMAIL",
            email_subject=source["email_subject"],
            email_body_html=source["email_body_html"],
            email_preheader=source["email_preheader"],
            email_variable_schema=source["email_variable_schema"],
            wait_duration_minutes=None,
            source_template_version_id=source["source_template_version_id"],
        )
        # Same storage objects and content ids: the copied body's image
        # references keep working without rewriting it.
        self.repo.copy_step_attachments(
            workspace_id=context.workspace_id,
            from_step_id=step_id,
            to_campaign_id=campaign_id,
            to_sequence_id=sequence_id,
            to_step_id=UUID(str(copy["id"])),
        )
        return self.get_sequence(context, campaign_id)

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

    def _validate_cid_references(
        self, context: WorkspaceContext, step_id: UUID | None, body_html: str | None
    ) -> None:
        """Every inline image in the body (src="cid:...") must be an image that
        was uploaded to this step, otherwise the email would go out with a broken
        image (or, for pasted templates, a reference to another step's file)."""
        referenced = extract_cid_references(body_html)
        if not referenced:
            return
        available: set[str] = set()
        if step_id is not None:
            available = {
                r["content_id"]
                for r in self.repo.list_step_attachments(
                    workspace_id=context.workspace_id, step_id=step_id
                )
                if r["disposition"] == "INLINE"
            }
        if referenced - available:
            raise AppError(
                "validation_error",
                "An image in the email is not available on this step. Remove it "
                "or insert the image again.",
                status_code=422,
            )

    def _to_step_out(
        self, row: RowMapping, attachments: Any = ()
    ) -> SequenceStepOut:
        return SequenceStepOut(
            id=row["id"],
            sequence_id=row["sequence_id"],
            campaign_id=row["campaign_id"],
            position=row["position"],
            kind=row["kind"],
            email_subject=row["email_subject"],
            email_body_html=row["email_body_html"],
            email_preheader=row.get("email_preheader"),
            attachments=[to_attachment_out(a) for a in attachments],
            email_variable_schema=row["email_variable_schema"],
            wait_duration_minutes=row["wait_duration_minutes"],
            source_template_version_id=row["source_template_version_id"],
            version=row["version"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
