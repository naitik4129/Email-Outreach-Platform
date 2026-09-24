from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class DateRangePreset(StrEnum):
    TODAY = "today"
    YESTERDAY = "yesterday"
    LAST_7_DAYS = "last_7_days"
    LAST_30_DAYS = "last_30_days"
    CUSTOM = "custom"



class TimeSeriesBucket(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: str = Field(description="ISO date string (YYYY-MM-DD)")
    sent: int = Field(
        ge=0, description="Outbound messages accepted in this time window"
    )
    replies: int = Field(ge=0, description="Authoritative replies received")
    bounces: int = Field(ge=0, description="Authoritative bounces recorded")


class TopCampaignItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    campaign_id: UUID
    name: str
    status: str
    sent: int = Field(ge=0)
    replies: int = Field(ge=0)
    reply_rate: float = Field(ge=0.0, le=100.0)
    bounces: int = Field(ge=0)
    bounce_rate: float = Field(ge=0.0, le=100.0)


class TopMailboxItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mailbox_id: UUID
    email_address: str
    provider: str
    sent: int = Field(ge=0)
    replies: int = Field(ge=0)
    reply_rate: float = Field(ge=0.0, le=100.0)
    bounces: int = Field(ge=0)
    bounce_rate: float = Field(ge=0.0, le=100.0)


class WorkspaceOverviewAnalytics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: UUID
    start_date: str
    end_date: str
    timezone: str
    prospects_contacted: int = Field(
        ge=0, description="Unique recipient addresses sent at least 1 email in period"
    )
    emails_sent: int = Field(ge=0, description="Total outbound messages accepted")
    replies: int = Field(ge=0, description="Total authoritative replies received")
    bounces: int = Field(ge=0, description="Total authoritative hard bounces")
    unsubscribes: int = Field(ge=0, description="Total unsubscribes recorded")
    complaints: int = Field(ge=0, description="Total complaints recorded")
    failed_sends: int = Field(ge=0, description="Total outbound messages marked FAILED")
    reply_rate: float = Field(ge=0.0, le=100.0, description="replies / emails_sent %")
    bounce_rate: float = Field(ge=0.0, le=100.0, description="bounces / emails_sent %")
    complaint_rate: float = Field(
        ge=0.0, le=100.0, description="complaints / emails_sent %"
    )
    unsubscribe_rate: float = Field(
        ge=0.0, le=100.0, description="unsubscribes / emails_sent %"
    )
    open_tracking_supported: bool = Field(
        default=False, description="Whether open tracking is supported by platform"
    )
    click_tracking_supported: bool = Field(
        default=False, description="Whether click tracking is supported by platform"
    )
    delivery_confirmation_supported: bool = Field(
        default=False, description="Whether DSN/DLR delivery confirmation is supported"
    )
    trend: list[TimeSeriesBucket] = Field(default_factory=list)
    top_campaigns: list[TopCampaignItem] = Field(default_factory=list)
    top_mailboxes: list[TopMailboxItem] = Field(default_factory=list)


class CampaignAnalytics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    campaign_id: UUID
    campaign_name: str
    campaign_status: str
    total_recipients: int = Field(
        ge=0, description="Unique recipient addresses enrolled"
    )
    enrolled: int = Field(ge=0, description="Total enrollments in campaign")
    scheduled: int = Field(ge=0, description="Messages currently scheduled/queued")
    sent: int = Field(ge=0, description="Messages successfully sent and accepted")
    failed: int = Field(ge=0, description="Messages failed")
    cancelled: int = Field(ge=0, description="Messages cancelled")
    skipped: int = Field(ge=0, description="Messages skipped")
    remaining: int = Field(ge=0, description="Messages pending future execution")
    bounced: int = Field(ge=0, description="Recipients with hard bounce recorded")
    complained: int = Field(ge=0, description="Recipients with complaint recorded")
    unsubscribed: int = Field(ge=0, description="Recipients with unsubscribe recorded")
    replied: int = Field(ge=0, description="Recipients with reply recorded")
    unique_recipients_contacted: int = Field(
        ge=0, description="Distinct recipients sent at least 1 message"
    )
    unique_recipients_replied: int = Field(
        ge=0, description="Distinct recipients who replied"
    )
    bounce_rate: float = Field(ge=0.0, le=100.0, description="bounced / sent %")
    complaint_rate: float = Field(ge=0.0, le=100.0, description="complained / sent %")
    unsubscribe_rate: float = Field(
        ge=0.0, le=100.0, description="unsubscribed / sent %"
    )
    reply_rate: float = Field(ge=0.0, le=100.0, description="replied / sent %")
    failure_rate: float = Field(
        ge=0.0, le=100.0, description="failed / (sent + failed) %"
    )
    open_tracking_supported: bool = Field(default=False)
    click_tracking_supported: bool = Field(default=False)
    delivery_confirmation_supported: bool = Field(default=False)


class SequenceStepAnalytics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_id: UUID
    position: int = Field(ge=1)
    kind: str
    subject: str | None = None
    scheduled: int = Field(ge=0)
    sent: int = Field(ge=0)
    failed: int = Field(ge=0)
    cancelled: int = Field(ge=0)
    bounced: int = Field(ge=0)
    replied: int = Field(
        ge=0, description="Replies attributed specifically to this step"
    )
    unsubscribed: int = Field(ge=0)
    reply_rate: float = Field(ge=0.0, le=100.0)


class CampaignSequenceAnalytics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    campaign_id: UUID
    steps: list[SequenceStepAnalytics] = Field(default_factory=list)


class DeliverabilityWarning(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    level: str = Field(description="WARNING or CRITICAL")
    title: str
    message: str
    metric_value: float | None = None
    threshold: float | None = None
    mailbox_id: UUID | None = None
    campaign_id: UUID | None = None


class MailboxDeliverabilityItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mailbox_id: UUID
    email_address: str
    provider: str
    connection_state: str
    health_state: str
    policy_state: str
    health_status: str = Field(
        description="HEALTHY, WARNING, CRITICAL, or DISCONNECTED"
    )
    sent_count: int = Field(ge=0)
    bounce_count: int = Field(ge=0)
    bounce_rate: float = Field(ge=0.0, le=100.0)
    complaint_count: int = Field(ge=0)
    complaint_rate: float = Field(ge=0.0, le=100.0)
    failure_count: int = Field(ge=0)
    active_safety_holds_count: int = Field(ge=0)
    warnings: list[DeliverabilityWarning] = Field(default_factory=list)


class SendFailureCategory(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category: str
    count: int = Field(ge=0)
    description: str


class DeliverabilityOverview(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workspace_id: UUID
    overall_health: str = Field(
        description="HEALTHY, NEEDS_ATTENTION, or ACTION_REQUIRED"
    )
    total_sent: int = Field(ge=0)
    bounce_rate: float = Field(ge=0.0, le=100.0)
    complaint_rate: float = Field(ge=0.0, le=100.0)
    failure_rate: float = Field(ge=0.0, le=100.0)
    active_safety_holds: int = Field(ge=0)
    warnings: list[DeliverabilityWarning] = Field(default_factory=list)
    mailboxes: list[MailboxDeliverabilityItem] = Field(default_factory=list)
    failure_breakdown: list[SendFailureCategory] = Field(default_factory=list)
