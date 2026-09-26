from __future__ import annotations

import html
import re
from uuid import UUID

from sqlalchemy import RowMapping
from sqlalchemy.orm import Session

from app.api.deps import WorkspaceContext
from app.core.config import Settings
from app.core.errors import AppError
from app.modules.campaigns.attachments import extract_cid_references
from app.modules.campaigns.audience_service import AudienceService
from app.modules.campaigns.mailbox_service import CampaignMailboxService
from app.modules.campaigns.repository import CampaignRepository
from app.modules.campaigns.schemas import PreflightIssue, PreflightResult
from app.modules.campaigns.sequence_service import SequenceService
from app.modules.campaigns.settings_service import CampaignSettingsService
from app.modules.personalization.api_service import PersonalizationApiService
from app.modules.personalization.config_schema import parse_config
from app.modules.personalization.context_builder import build_context
from app.modules.personalization.version import CAMPAIGN_TYPE_HYPER
from app.modules.templates.variables import validate_template_content

_HIGH_EXCLUSION_RATE_THRESHOLD = 0.5
# How many audience members are sampled to estimate how many leads have too little
# data to personalize (bounded so preflight stays cheap on huge audiences).
_THIN_CONTEXT_SAMPLE = 200
_TAG_RE = re.compile(r"<[^>]*>")


def _has_visible_content(body_html: str | None) -> bool:
    """True when the body would show something: an image or non-blank text once
    tags and non-breaking spaces are removed (so `<p></p>` counts as empty)."""
    if not body_html:
        return False
    if re.search(r"<img\b", body_html, re.IGNORECASE):
        return True
    text_only = html.unescape(_TAG_RE.sub("", body_html))
    return bool(text_only.replace("\xa0", " ").strip())
_UNHEALTHY_CONNECTION_STATES = {"DISCONNECTED", "RECONNECT_REQUIRED"}
_UNHEALTHY_POLICY_STATES = {"DISABLED", "RESTRICTED"}


class PreflightService:
    """Single reusable, read-only preflight check.

    No writes, no side effects. Composes the same services the individual
    configuration endpoints already expose -- there is exactly one place
    readiness rules live, so Phase 8's future activation guard can call this
    unmodified rather than re-implementing the same checks.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = CampaignRepository(session)
        self.sequences = SequenceService(session)
        self.mailboxes = CampaignMailboxService(session)
        self.settings = CampaignSettingsService(session)
        self.audiences = AudienceService(session)

    def run(self, context: WorkspaceContext, campaign_id: UUID) -> PreflightResult:
        campaign = self.repo.get_campaign(
            workspace_id=context.workspace_id, campaign_id=campaign_id
        )
        if campaign is None:
            raise AppError("not_found", "Campaign not found", status_code=404)

        errors: list[PreflightIssue] = []
        warnings: list[PreflightIssue] = []

        self._check_sequence(context, campaign_id, errors)
        if campaign.get("campaign_type") == CAMPAIGN_TYPE_HYPER:
            self._check_personalization(context, campaign, errors, warnings)
        self._check_mailboxes(context, campaign_id, errors, warnings)
        self._check_audience(context, campaign_id, errors, warnings)
        self._check_settings(campaign, errors)

        return PreflightResult(ready=len(errors) == 0, errors=errors, warnings=warnings)

    def _check_sequence(
        self, context: WorkspaceContext, campaign_id: UUID, errors: list[PreflightIssue]
    ) -> None:
        sequence = self.sequences.get_sequence(context, campaign_id)
        if not sequence.steps:
            errors.append(
                PreflightIssue(
                    code="sequence_empty",
                    message="Add at least one Email step to the sequence.",
                    field_path="sequence",
                )
            )
            return

        kinds = [step.kind for step in sequence.steps]
        if kinds[0] != "EMAIL":
            errors.append(
                PreflightIssue(
                    code="sequence_must_start_email",
                    message="The sequence must start with an Email step.",
                    field_path="sequence",
                )
            )
        if kinds[-1] != "EMAIL":
            errors.append(
                PreflightIssue(
                    code="sequence_must_end_email",
                    message="The sequence must end with an Email step.",
                    field_path="sequence",
                )
            )
        alternating = all(kinds[i] != kinds[i + 1] for i in range(len(kinds) - 1))
        if not alternating:
            errors.append(
                PreflightIssue(
                    code="sequence_not_alternating",
                    message="Email and Wait steps must alternate "
                    "(Email, Wait, Email, ...).",
                    field_path="sequence",
                )
            )

        for step in sequence.steps:
            if step.kind != "EMAIL":
                continue
            field_path = f"sequence.steps[{step.position}]"
            if not (step.email_subject or "").strip():
                errors.append(
                    PreflightIssue(
                        code="sequence_step_missing_content",
                        message=f"Email step at position {step.position} "
                        "has no subject.",
                        field_path=field_path,
                    )
                )
            if not _has_visible_content(step.email_body_html):
                errors.append(
                    PreflightIssue(
                        code="sequence_step_missing_body",
                        message=f"Email step at position {step.position} "
                        "has an empty body.",
                        field_path=field_path,
                    )
                )
            inline_ids = {
                a.content_id for a in step.attachments if a.disposition == "INLINE"
            }
            if extract_cid_references(step.email_body_html) - inline_ids:
                errors.append(
                    PreflightIssue(
                        code="sequence_step_missing_image",
                        message=f"Email step at position {step.position} uses an "
                        "image that is no longer attached. Remove it or insert "
                        "the image again.",
                        field_path=field_path,
                    )
                )
            try:
                validate_template_content(
                    step.email_subject or " ",
                    step.email_body_html or "",
                    step.email_preheader,
                )
            except AppError as exc:
                errors.append(
                    PreflightIssue(
                        code="sequence_step_invalid_variable",
                        message=f"Email step at position {step.position}: "
                        f"{exc.message}",
                        field_path=field_path,
                    )
                )

    def _check_personalization(
        self,
        context: WorkspaceContext,
        campaign: RowMapping,
        errors: list[PreflightIssue],
        warnings: list[PreflightIssue],
    ) -> None:
        """Rules specific to a hyper-personalized campaign (ADR-0011). The
        reference templates themselves are validated by _check_sequence (same
        variable rules as any step)."""
        settings = Settings.current()
        campaign_id = UUID(str(campaign["id"]))
        if not settings.personalization_enabled:
            errors.append(
                PreflightIssue(
                    code="personalization_disabled",
                    message="Hyper-personalized campaigns are not enabled for "
                    "this deployment.",
                    field_path="personalization",
                )
            )
            return

        service = PersonalizationApiService(self.session, settings)
        digest, sequence, steps = service.current_digest(context, campaign_id)
        try:
            config = (
                parse_config(sequence["personalization_config"])
                if sequence is not None
                else None
            )
        except Exception:
            config = None
        if config is None:
            errors.append(
                PreflightIssue(
                    code="personalization_objective_missing",
                    message="Define the campaign objective (offer, call to "
                    "action) before launching.",
                    field_path="personalization",
                )
            )

        email_steps = [s for s in steps if s["kind"] == "EMAIL"]
        for step in email_steps:
            if extract_cid_references(step["email_body_html"]):
                errors.append(
                    PreflightIssue(
                        code="reference_inline_image_unsupported",
                        message=f"Email step at position {step['position']} "
                        "contains an inline image, which personalized emails do "
                        "not support. Use an attachment instead.",
                        field_path=f"sequence.steps[{step['position']}]",
                    )
                )
        if len(email_steps) > 1 and not settings.sequence_progression_enabled:
            errors.append(
                PreflightIssue(
                    code="followups_require_progression",
                    message="Follow-up emails are not enabled on this "
                    "deployment, so a multi-step personalized sequence cannot "
                    "run.",
                    field_path="sequence",
                )
            )

        approval = service.approval_status(context, campaign_id, digest)
        if approval.status == "NONE":
            errors.append(
                PreflightIssue(
                    code="personalization_not_approved",
                    message="Generate sample emails and approve them before "
                    "launching.",
                    field_path="personalization",
                )
            )
        elif approval.status == "STALE":
            errors.append(
                PreflightIssue(
                    code="personalization_approval_stale",
                    message="The objective or emails changed since the samples "
                    "were approved. Generate and approve new samples.",
                    field_path="personalization",
                )
            )

        audience_id = campaign["draft_audience_id"]
        if audience_id is not None and settings.personalization_min_facts > 0:
            members = self.repo.list_accepted_audience_members(
                workspace_id=context.workspace_id,
                audience_id=UUID(str(audience_id)),
                after_ordinal=None,
                limit=_THIN_CONTEXT_SAMPLE,
            )
            thin = sum(
                1
                for member in members
                if build_context(
                    member["frozen_variables"] or {},
                    min_facts=settings.personalization_min_facts,
                ).thin
            )
            if thin:
                warnings.append(
                    PreflightIssue(
                        code="leads_missing_context_warning",
                        message=f"{thin} of {len(members)} sampled leads have "
                        "little data to personalize with. Unless their company "
                        "website adds more, they will receive your reference "
                        "email with normal variable substitution.",
                        field_path="audience",
                    )
                )

    def _check_mailboxes(
        self,
        context: WorkspaceContext,
        campaign_id: UUID,
        errors: list[PreflightIssue],
        warnings: list[PreflightIssue],
    ) -> None:
        assigned = self.mailboxes.list_mailboxes(context, campaign_id)
        if not assigned:
            errors.append(
                PreflightIssue(
                    code="no_mailboxes_assigned",
                    message="Assign at least one sending mailbox.",
                    field_path="mailboxes",
                )
            )
            return

        unhealthy = [
            mb
            for mb in assigned
            if mb.connection_state in _UNHEALTHY_CONNECTION_STATES
            or mb.policy_state in _UNHEALTHY_POLICY_STATES
        ]
        if len(unhealthy) == len(assigned):
            errors.append(
                PreflightIssue(
                    code="no_usable_mailboxes",
                    message="None of the assigned mailboxes are currently able "
                    "to send.",
                    field_path="mailboxes",
                )
            )
        elif unhealthy:
            for mb in unhealthy:
                warnings.append(
                    PreflightIssue(
                        code="mailbox_unhealthy",
                        message=f"Mailbox {mb.email_address} is not currently able "
                        "to send "
                        f"({mb.connection_state}/{mb.policy_state}).",
                        field_path=f"mailboxes[{mb.mailbox_id}]",
                    )
                )

    def _check_audience(
        self,
        context: WorkspaceContext,
        campaign_id: UUID,
        errors: list[PreflightIssue],
        warnings: list[PreflightIssue],
    ) -> None:
        audience = self.audiences.get_committed_audience(context, campaign_id)
        if audience is None or not audience.is_committed:
            errors.append(
                PreflightIssue(
                    code="audience_not_selected",
                    message="Select and commit an audience for this campaign.",
                    field_path="audience",
                )
            )
            return
        if audience.status != "READY":
            errors.append(
                PreflightIssue(
                    code="audience_not_ready",
                    message=f"The committed audience revision is "
                    f"{audience.status.lower()}, "
                    "not ready.",
                    field_path="audience",
                )
            )
            return
        accepted = audience.accepted_count or 0
        excluded = audience.excluded_count or 0
        if accepted == 0:
            errors.append(
                PreflightIssue(
                    code="audience_zero_eligible",
                    message="No eligible recipients remain in the selected audience.",
                    field_path="audience",
                )
            )
            return
        total = accepted + excluded
        if total > 0 and (excluded / total) > _HIGH_EXCLUSION_RATE_THRESHOLD:
            warnings.append(
                PreflightIssue(
                    code="high_exclusion_rate",
                    message=f"{excluded} of {total} selected leads were excluded "
                    "(archived, suppressed, or invalid).",
                    field_path="audience",
                )
            )

    def _check_settings(
        self, campaign: RowMapping, errors: list[PreflightIssue]
    ) -> None:
        if campaign["current_settings_id"] is None:
            errors.append(
                PreflightIssue(
                    code="settings_missing",
                    message="Configure a sending schedule for this campaign.",
                    field_path="settings",
                )
            )
