from __future__ import annotations

from uuid import UUID

from sqlalchemy import RowMapping
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import WorkspaceContext
from app.core.errors import AppError
from app.modules.campaigns.repository import CampaignRepository
from app.modules.campaigns.schemas import AudienceOut, AudienceSelectIn


class AudienceService:
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
                "Audience selection can only change while the campaign is DRAFT",
                status_code=409,
            )
        return campaign

    def select_audience(
        self, context: WorkspaceContext, campaign_id: UUID, payload: AudienceSelectIn
    ) -> AudienceOut:
        self._require_draft_campaign(context, campaign_id)

        if not payload.list_ids and not payload.lead_ids:
            raise AppError(
                "validation_error",
                "Select at least one lead or lead list",
                status_code=422,
            )

        # Cross-tenant safety: every list/lead id is re-resolved against this
        # workspace before any capture work starts. An id belonging to
        # another workspace (or simply nonexistent) is silently invisible
        # under RLS, so a mismatch here means "not in this workspace" --
        # reported as a validation error, never a tenant-existence leak.
        list_versions = self.repo.get_existing_list_ids(
            workspace_id=context.workspace_id, list_ids=payload.list_ids
        )
        missing_lists = set(payload.list_ids) - set(list_versions.keys())
        if missing_lists:
            raise AppError(
                "validation_error",
                "One or more selected lead lists were not found in this workspace",
                status_code=422,
            )

        existing_leads = self.repo.get_existing_lead_ids(
            workspace_id=context.workspace_id, lead_ids=payload.lead_ids
        )
        missing_leads = set(payload.lead_ids) - existing_leads
        if missing_leads:
            raise AppError(
                "validation_error",
                "One or more selected leads were not found in this workspace",
                status_code=422,
            )

        selection_manifest = {
            "version": 1,
            "lists": [str(i) for i in payload.list_ids],
            "leads": [str(i) for i in payload.lead_ids],
        }

        try:
            audience = self.repo.insert_campaign_audience(
                workspace_id=context.workspace_id,
                campaign_id=campaign_id,
                selection_manifest=selection_manifest,
            )
        except IntegrityError as exc:
            # campaign_audiences_active_capture_idx: only one CAPTURING
            # revision per campaign at a time.
            raise AppError(
                "conflict",
                "An audience capture is already in progress for this campaign. "
                "Wait for it to finish or abandon it before selecting again.",
                status_code=409,
            ) from exc

        audience_id = UUID(str(audience["id"]))
        for list_id, version in list_versions.items():
            self.repo.insert_audience_capture_source(
                workspace_id=context.workspace_id,
                campaign_id=campaign_id,
                audience_id=audience_id,
                list_id=list_id,
                captured_list_revision=version,
            )

        total_count = self.repo.count_candidate_leads(
            workspace_id=context.workspace_id,
            list_ids=payload.list_ids,
            lead_ids=payload.lead_ids,
        )
        self.repo.insert_capture_planning_job(
            workspace_id=context.workspace_id,
            campaign_id=campaign_id,
            audience_id=audience_id,
            total_count=total_count,
        )

        self._dispatch_capture_task(
            workspace_id=context.workspace_id,
            campaign_id=campaign_id,
            audience_id=audience_id,
        )

        return self._to_out(audience, total_candidates=total_count)

    def get_audience(
        self, context: WorkspaceContext, campaign_id: UUID, audience_id: UUID
    ) -> AudienceOut:
        campaign = self.repo.get_campaign(
            workspace_id=context.workspace_id, campaign_id=campaign_id
        )
        if campaign is None:
            raise AppError("not_found", "Campaign not found", status_code=404)
        audience = self.repo.get_audience(
            workspace_id=context.workspace_id,
            campaign_id=campaign_id,
            audience_id=audience_id,
        )
        if audience is None:
            raise AppError("not_found", "Audience revision not found", status_code=404)
        return self._to_full_out(context, campaign, audience)

    def get_committed_audience(
        self, context: WorkspaceContext, campaign_id: UUID
    ) -> AudienceOut | None:
        campaign = self.repo.get_campaign(
            workspace_id=context.workspace_id, campaign_id=campaign_id
        )
        if campaign is None:
            raise AppError("not_found", "Campaign not found", status_code=404)
        if campaign["draft_audience_id"] is not None:
            audience = self.repo.get_audience(
                workspace_id=context.workspace_id,
                campaign_id=campaign_id,
                audience_id=UUID(str(campaign["draft_audience_id"])),
            )
        else:
            audience = self.repo.get_latest_audience(
                workspace_id=context.workspace_id, campaign_id=campaign_id
            )
        if audience is None:
            return None
        return self._to_full_out(context, campaign, audience)

    def commit_audience(
        self, context: WorkspaceContext, campaign_id: UUID, audience_id: UUID
    ) -> AudienceOut:
        campaign = self.repo.get_campaign(
            workspace_id=context.workspace_id, campaign_id=campaign_id
        )
        if campaign is None:
            raise AppError("not_found", "Campaign not found", status_code=404)
        if campaign["status"] != "DRAFT":
            raise AppError(
                "state_conflict",
                "Audience can only be committed while the campaign is DRAFT",
                status_code=409,
            )
        row = self.repo.commit_draft_audience(
            workspace_id=context.workspace_id,
            campaign_id=campaign_id,
            audience_id=audience_id,
        )
        if row is None:
            raise AppError(
                "validation_error",
                "Audience revision is not READY, or does not belong to this campaign",
                status_code=422,
            )
        audience = self.repo.get_audience(
            workspace_id=context.workspace_id,
            campaign_id=campaign_id,
            audience_id=audience_id,
        )
        assert audience is not None
        return self._to_full_out(context, row, audience)

    def abandon_audience(
        self, context: WorkspaceContext, campaign_id: UUID, audience_id: UUID
    ) -> None:
        """Recovery command for a stuck/unwanted CAPTURING revision.

        app_api has no UPDATE grant on campaign_audiences (only
        app_worker_general can move it out of CAPTURING), so abandonment is
        dispatched as the same worker task in recovery mode rather than
        written directly here.
        """
        campaign = self.repo.get_campaign(
            workspace_id=context.workspace_id, campaign_id=campaign_id
        )
        if campaign is None:
            raise AppError("not_found", "Campaign not found", status_code=404)
        audience = self.repo.get_audience(
            workspace_id=context.workspace_id,
            campaign_id=campaign_id,
            audience_id=audience_id,
        )
        if audience is None:
            raise AppError("not_found", "Audience revision not found", status_code=404)
        if audience["status"] != "CAPTURING":
            raise AppError(
                "state_conflict",
                "Only a CAPTURING audience revision can be abandoned",
                status_code=409,
            )
        self._dispatch_capture_task(
            workspace_id=context.workspace_id,
            campaign_id=campaign_id,
            audience_id=audience_id,
            abandon=True,
        )

    def _dispatch_capture_task(
        self,
        *,
        workspace_id: UUID,
        campaign_id: UUID,
        audience_id: UUID,
        abandon: bool = False,
    ) -> None:
        from workers.celery_app import celery_app

        celery_app.send_task(
            "campaigns.capture_audience_chunk",
            kwargs={
                "workspace_id": str(workspace_id),
                "campaign_id": str(campaign_id),
                "audience_id": str(audience_id),
                "abandon": abandon,
            },
            queue="campaigns",
        )

    def _to_out(
        self, row: RowMapping, *, total_candidates: int | None = None
    ) -> AudienceOut:
        return AudienceOut(
            id=row["id"],
            campaign_id=row["campaign_id"],
            revision=row["revision"],
            status=row["status"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            total_candidates=total_candidates,
            error_reason=None,
        )

    def _to_full_out(
        self,
        context: WorkspaceContext,
        campaign: RowMapping,
        audience: RowMapping,
    ) -> AudienceOut:
        job = self.repo.get_capture_job_for_audience(
            workspace_id=context.workspace_id, audience_id=UUID(str(audience["id"]))
        )
        counts = None
        if audience["status"] in ("READY", "FAILED"):
            counts = self.repo.get_audience_member_counts(
                workspace_id=context.workspace_id, audience_id=UUID(str(audience["id"]))
            )
        return AudienceOut(
            id=audience["id"],
            campaign_id=audience["campaign_id"],
            revision=audience["revision"],
            status=audience["status"],
            started_at=audience["started_at"],
            completed_at=audience["completed_at"],
            is_committed=(
                campaign.get("draft_audience_id") is not None
                and str(campaign["draft_audience_id"]) == str(audience["id"])
            ),
            total_candidates=job["total_count"] if job else None,
            processed_count=job["processed_count"] if job else 0,
            accepted_count=counts["accepted"] if counts else None,
            excluded_count=counts["excluded"] if counts else None,
            error_reason=job["error_reason"] if job else None,
        )
