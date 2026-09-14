from __future__ import annotations

from uuid import UUID

from sqlalchemy.orm import Session

from app.api.deps import WorkspaceContext
from app.modules.campaigns.audience_service import AudienceService
from app.modules.campaigns.mailbox_service import CampaignMailboxService
from app.modules.campaigns.preflight import PreflightService
from app.modules.campaigns.schemas import CampaignReviewOut
from app.modules.campaigns.sequence_service import SequenceService
from app.modules.campaigns.service import CampaignService
from app.modules.campaigns.settings_service import CampaignSettingsService


class ReviewService:
    """Assembles the Review screen's payload from the other campaign
    services in one call -- thin composition, no new SQL of its own, so it
    cannot drift out of sync with what each configuration tab shows."""

    def __init__(self, session: Session) -> None:
        self.campaigns = CampaignService(session)
        self.sequences = SequenceService(session)
        self.mailboxes = CampaignMailboxService(session)
        self.settings = CampaignSettingsService(session)
        self.audiences = AudienceService(session)
        self.preflight = PreflightService(session)

    def get_review(
        self, context: WorkspaceContext, campaign_id: UUID
    ) -> CampaignReviewOut:
        campaign = self.campaigns.get_campaign(context, campaign_id)
        sequence = self.sequences.get_sequence(context, campaign_id)
        mailboxes = self.mailboxes.list_mailboxes(context, campaign_id)
        settings = self.settings.get_current(context, campaign_id)
        audience = self.audiences.get_committed_audience(context, campaign_id)
        preflight = self.preflight.run(context, campaign_id)

        return CampaignReviewOut(
            campaign=campaign,
            sequence=sequence,
            mailboxes=mailboxes,
            settings=settings,
            audience=audience,
            preflight=preflight,
        )
