from __future__ import annotations

import logging
from datetime import UTC, datetime, time, timedelta
from uuid import UUID

from sqlalchemy.orm import Session

from app.api.deps import WorkspaceContext
from app.core.errors import AppError
from app.modules.analytics.repository import AnalyticsRepository
from app.modules.analytics.schemas import (
    CampaignAnalytics,
    CampaignSequenceAnalytics,
    DeliverabilityOverview,
    DeliverabilityWarning,
    MailboxDeliverabilityItem,
    SendFailureCategory,
    SequenceStepAnalytics,
    TimeSeriesBucket,
    TopCampaignItem,
    TopMailboxItem,
    WorkspaceOverviewAnalytics,
)

logger = logging.getLogger(__name__)

MAX_DATE_RANGE_DAYS = 365


def _parse_date_bounds(
    start_date_str: str | None,
    end_date_str: str | None,
) -> tuple[datetime, datetime]:
    """Parse YYYY-MM-DD date boundaries into UTC datetimes covering full days."""
    now_utc = datetime.now(UTC)

    if not end_date_str:
        end_dt = datetime.combine(now_utc.date(), time.max, tzinfo=UTC)
    else:
        try:
            d = datetime.strptime(end_date_str, "%Y-%m-%d").date()
            end_dt = datetime.combine(d, time.max, tzinfo=UTC)
        except ValueError as exc:
            raise AppError(
                "validation_error",
                "Invalid end_date format. Expected YYYY-MM-DD",
                status_code=422,
            ) from exc

    if not start_date_str:
        # Default: 30 days prior
        start_dt = datetime.combine(
            (end_dt.date() - timedelta(days=29)), time.min, tzinfo=UTC
        )
    else:
        try:
            d = datetime.strptime(start_date_str, "%Y-%m-%d").date()
            start_dt = datetime.combine(d, time.min, tzinfo=UTC)
        except ValueError as exc:
            raise AppError(
                "validation_error",
                "Invalid start_date format. Expected YYYY-MM-DD",
                status_code=422,
            ) from exc

    if start_dt > end_dt:
        raise AppError(
            "validation_error",
            "start_date cannot be later than end_date",
            status_code=422,
        )

    if (end_dt - start_dt).days > MAX_DATE_RANGE_DAYS:
        raise AppError(
            "validation_error",
            f"Date range exceeds maximum allowed limit of {MAX_DATE_RANGE_DAYS} days",
            status_code=422,
        )

    return start_dt, end_dt


class AnalyticsService:
    """Domain service managing analytics and deliverability reporting."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.repository = AnalyticsRepository(session)

    def get_campaign_analytics(
        self, context: WorkspaceContext, campaign_id: UUID
    ) -> CampaignAnalytics:
        result = self.repository.get_campaign_analytics(
            workspace_id=context.workspace_id,
            campaign_id=campaign_id,
        )
        if not result:
            raise AppError("not_found", "Campaign not found", status_code=404)
        return CampaignAnalytics(**result)

    def get_sequence_analytics(
        self, context: WorkspaceContext, campaign_id: UUID
    ) -> CampaignSequenceAnalytics:
        result = self.repository.get_sequence_analytics(
            workspace_id=context.workspace_id,
            campaign_id=campaign_id,
        )
        if result is None:
            raise AppError("not_found", "Campaign not found", status_code=404)
        return CampaignSequenceAnalytics(
            campaign_id=campaign_id,
            steps=[SequenceStepAnalytics(**s) for s in result],
        )

    def get_workspace_overview(
        self,
        context: WorkspaceContext,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
        timezone: str | None = "UTC",
        campaign_id: UUID | None = None,
        mailbox_id: UUID | None = None,
    ) -> WorkspaceOverviewAnalytics:
        start_dt, end_dt = _parse_date_bounds(start_date, end_date)
        tz_name = timezone or "UTC"

        raw = self.repository.get_workspace_overview(
            workspace_id=context.workspace_id,
            start_date=start_dt,
            end_date=end_dt,
            timezone_name=tz_name,
            campaign_id=campaign_id,
            mailbox_id=mailbox_id,
        )

        trend_items = [TimeSeriesBucket(**b) for b in raw["trend"]]
        top_camps = [TopCampaignItem(**c) for c in raw["top_campaigns"]]
        top_mbs = [TopMailboxItem(**m) for m in raw["top_mailboxes"]]

        return WorkspaceOverviewAnalytics(
            workspace_id=raw["workspace_id"],
            start_date=raw["start_date"],
            end_date=raw["end_date"],
            timezone=raw["timezone"],
            prospects_contacted=raw["prospects_contacted"],
            emails_sent=raw["emails_sent"],
            replies=raw["replies"],
            bounces=raw["bounces"],
            unsubscribes=raw["unsubscribes"],
            complaints=raw["complaints"],
            failed_sends=raw["failed_sends"],
            reply_rate=raw["reply_rate"],
            bounce_rate=raw["bounce_rate"],
            complaint_rate=raw["complaint_rate"],
            unsubscribe_rate=raw["unsubscribe_rate"],
            open_tracking_supported=raw["open_tracking_supported"],
            click_tracking_supported=raw["click_tracking_supported"],
            delivery_confirmation_supported=raw["delivery_confirmation_supported"],
            trend=trend_items,
            top_campaigns=top_camps,
            top_mailboxes=top_mbs,
        )

    def get_deliverability_overview(
        self,
        context: WorkspaceContext,
        *,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> DeliverabilityOverview:
        start_dt, end_dt = _parse_date_bounds(start_date, end_date)

        raw = self.repository.get_deliverability_overview(
            workspace_id=context.workspace_id,
            start_date=start_dt,
            end_date=end_dt,
        )

        warnings = [DeliverabilityWarning(**w) for w in raw["warnings"]]
        mailboxes = [
            MailboxDeliverabilityItem(
                **{**m, "warnings": [DeliverabilityWarning(**w) for w in m["warnings"]]}
            )
            for m in raw["mailboxes"]
        ]
        failure_breakdown = [SendFailureCategory(**f) for f in raw["failure_breakdown"]]

        return DeliverabilityOverview(
            workspace_id=raw["workspace_id"],
            overall_health=raw["overall_health"],
            total_sent=raw["total_sent"],
            bounce_rate=raw["bounce_rate"],
            complaint_rate=raw["complaint_rate"],
            failure_rate=raw["failure_rate"],
            active_safety_holds=raw["active_safety_holds"],
            warnings=warnings,
            mailboxes=mailboxes,
            failure_breakdown=failure_breakdown,
        )
