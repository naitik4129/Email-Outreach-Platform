export type TimeSeriesBucket = {
  date: string;
  sent: number;
  replies: number;
  bounces: number;
};

export type TopCampaignItem = {
  campaign_id: string;
  name: string;
  status: string;
  sent: number;
  replies: number;
  reply_rate: number;
  bounces: number;
  bounce_rate: number;
};

export type TopMailboxItem = {
  mailbox_id: string;
  email_address: string;
  provider: string;
  sent: number;
  replies: number;
  reply_rate: number;
  bounces: number;
  bounce_rate: number;
};

export type WorkspaceOverviewAnalytics = {
  workspace_id: string;
  start_date: string;
  end_date: string;
  timezone: string;
  prospects_contacted: number;
  emails_sent: number;
  replies: number;
  bounces: number;
  unsubscribes: number;
  complaints: number;
  failed_sends: number;
  reply_rate: number;
  bounce_rate: number;
  complaint_rate: number;
  unsubscribe_rate: number;
  open_tracking_supported: boolean;
  click_tracking_supported: boolean;
  delivery_confirmation_supported: boolean;
  trend: TimeSeriesBucket[];
  top_campaigns: TopCampaignItem[];
  top_mailboxes: TopMailboxItem[];
};

export type CampaignAnalytics = {
  campaign_id: string;
  campaign_name: string;
  campaign_status: string;
  total_recipients: number;
  enrolled: number;
  scheduled: number;
  sent: number;
  failed: number;
  cancelled: number;
  skipped: number;
  remaining: number;
  bounced: number;
  complained: number;
  unsubscribed: number;
  replied: number;
  unique_recipients_contacted: number;
  unique_recipients_replied: number;
  bounce_rate: number;
  complaint_rate: number;
  unsubscribe_rate: number;
  reply_rate: number;
  failure_rate: number;
  open_tracking_supported: boolean;
  click_tracking_supported: boolean;
  delivery_confirmation_supported: boolean;
};

export type SequenceStepAnalytics = {
  step_id: string;
  position: number;
  kind: string;
  subject: string | null;
  scheduled: number;
  sent: number;
  failed: number;
  cancelled: number;
  bounced: number;
  replied: number;
  unsubscribed: number;
  reply_rate: number;
};

export type CampaignSequenceAnalytics = {
  campaign_id: string;
  steps: SequenceStepAnalytics[];
};

export type DeliverabilityWarning = {
  code: string;
  level: "WARNING" | "CRITICAL";
  title: string;
  message: string;
  metric_value?: number | null;
  threshold?: number | null;
  mailbox_id?: string | null;
  campaign_id?: string | null;
};

export type MailboxDeliverabilityItem = {
  mailbox_id: string;
  email_address: string;
  provider: string;
  connection_state: string;
  health_state: string;
  policy_state: string;
  health_status: "HEALTHY" | "WARNING" | "CRITICAL" | "DISCONNECTED";
  sent_count: number;
  bounce_count: number;
  bounce_rate: number;
  complaint_count: number;
  complaint_rate: number;
  failure_count: number;
  active_safety_holds_count: number;
  warnings: DeliverabilityWarning[];
};

export type SendFailureCategory = {
  category: string;
  count: number;
  description: string;
};

export type DeliverabilityOverview = {
  workspace_id: string;
  overall_health: "HEALTHY" | "NEEDS_ATTENTION" | "ACTION_REQUIRED";
  total_sent: number;
  bounce_rate: number;
  complaint_rate: number;
  failure_rate: number;
  active_safety_holds: number;
  warnings: DeliverabilityWarning[];
  mailboxes: MailboxDeliverabilityItem[];
  failure_breakdown: SendFailureCategory[];
};
