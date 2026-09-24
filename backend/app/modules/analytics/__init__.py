from __future__ import annotations

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
from app.modules.analytics.service import AnalyticsService

__all__ = [
    "AnalyticsRepository",
    "AnalyticsService",
    "CampaignAnalytics",
    "CampaignSequenceAnalytics",
    "DeliverabilityOverview",
    "DeliverabilityWarning",
    "MailboxDeliverabilityItem",
    "SendFailureCategory",
    "SequenceStepAnalytics",
    "TimeSeriesBucket",
    "TopCampaignItem",
    "TopMailboxItem",
    "WorkspaceOverviewAnalytics",
]
