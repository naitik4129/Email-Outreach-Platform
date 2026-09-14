from __future__ import annotations

from uuid import UUID

from sqlalchemy import RowMapping
from sqlalchemy.orm import Session

from app.api.deps import WorkspaceContext
from app.core.errors import AppError
from app.modules.campaigns.repository import CampaignRepository
from app.modules.campaigns.schemas import (
    CampaignMailboxAssignIn,
    CampaignMailboxesReorderIn,
    CampaignMailboxOut,
)
from app.modules.mailboxes.repository import MailboxRepository


class CampaignMailboxService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = CampaignRepository(session)
        self.mailboxes = MailboxRepository(session)

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
                "Mailbox assignment can only change while the campaign is DRAFT",
                status_code=409,
            )
        return campaign

    def list_mailboxes(
        self, context: WorkspaceContext, campaign_id: UUID
    ) -> list[CampaignMailboxOut]:
        campaign = self.repo.get_campaign(
            workspace_id=context.workspace_id, campaign_id=campaign_id
        )
        if campaign is None:
            raise AppError("not_found", "Campaign not found", status_code=404)
        rows = self.repo.list_campaign_mailboxes(
            workspace_id=context.workspace_id, campaign_id=campaign_id
        )
        return [self._to_out(row) for row in rows]

    def assign_mailbox(
        self,
        context: WorkspaceContext,
        campaign_id: UUID,
        payload: CampaignMailboxAssignIn,
    ) -> list[CampaignMailboxOut]:
        self._require_draft_campaign(context, campaign_id)
        # Provider-neutral: only same-workspace existence is checked here --
        # campaign code never branches on mailbox.provider. Connection/policy
        # health is surfaced as a preflight warning, not an assignment blocker,
        # since a temporarily unhealthy mailbox may recover before send time.
        mailbox = self.mailboxes.get_mailbox(context.workspace_id, payload.mailbox_id)
        if mailbox is None:
            raise AppError(
                "validation_error",
                "Mailbox not found in this workspace",
                status_code=422,
            )
        existing = self.repo.list_campaign_mailboxes(
            workspace_id=context.workspace_id, campaign_id=campaign_id
        )
        if any(str(row["mailbox_id"]) == str(payload.mailbox_id) for row in existing):
            return [self._to_out(row) for row in existing]
        self.repo.insert_campaign_mailbox(
            workspace_id=context.workspace_id,
            campaign_id=campaign_id,
            mailbox_id=payload.mailbox_id,
            allocation_position=len(existing),
        )
        return self.list_mailboxes(context, campaign_id)

    def unassign_mailbox(
        self, context: WorkspaceContext, campaign_id: UUID, mailbox_id: UUID
    ) -> list[CampaignMailboxOut]:
        self._require_draft_campaign(context, campaign_id)
        self.repo.delete_campaign_mailbox(
            workspace_id=context.workspace_id,
            campaign_id=campaign_id,
            mailbox_id=mailbox_id,
        )
        return self.list_mailboxes(context, campaign_id)

    def reorder_mailboxes(
        self,
        context: WorkspaceContext,
        campaign_id: UUID,
        payload: CampaignMailboxesReorderIn,
    ) -> list[CampaignMailboxOut]:
        self._require_draft_campaign(context, campaign_id)
        existing = self.repo.list_campaign_mailboxes(
            workspace_id=context.workspace_id, campaign_id=campaign_id
        )
        existing_ids = {str(row["mailbox_id"]) for row in existing}
        requested_ids = {str(mid) for mid in payload.mailbox_ids}
        if requested_ids != existing_ids:
            raise AppError(
                "validation_error",
                "Reorder must include exactly the currently assigned mailboxes",
                status_code=422,
            )
        # No UPDATE grant exists on campaign_mailboxes -- every reorder is a
        # full delete + reinsert of the assignment set, inside one transaction.
        self.repo.delete_all_campaign_mailboxes(
            workspace_id=context.workspace_id, campaign_id=campaign_id
        )
        for position, mailbox_id in enumerate(payload.mailbox_ids):
            self.repo.insert_campaign_mailbox(
                workspace_id=context.workspace_id,
                campaign_id=campaign_id,
                mailbox_id=mailbox_id,
                allocation_position=position,
            )
        return self.list_mailboxes(context, campaign_id)

    def _to_out(self, row: RowMapping) -> CampaignMailboxOut:
        return CampaignMailboxOut(
            mailbox_id=row["mailbox_id"],
            provider=row["provider"],
            email_address=row["original_address"],
            sender_display_name=row["sender_display_name"],
            connection_state=row["connection_state"],
            health_state=row["health_state"],
            policy_state=row["policy_state"],
            policy_reason=row["policy_reason"],
            active=row["active"],
            allocation_position=row["allocation_position"],
        )
