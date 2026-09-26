export type RoleCode = "OWNER" | "ADMIN" | "MANAGER" | "MEMBER" | "VIEWER";

export type Profile = {
  id: string;
  display_name: string | null;
  email: string | null;
};

export type WorkspaceListItem = {
  workspace_id: string;
  workspace_name: string;
  workspace_status: string;
  membership_id: string;
  role_code: RoleCode;
  membership_version: number;
};

export type Workspace = {
  id: string;
  name: string;
  status: string;
  defaults: Record<string, unknown>;
  version: number;
  role_code: RoleCode;
};

export type WorkspaceCreateResult = {
  id: string;
  name: string;
  status: string;
  role_code: RoleCode;
  membership_id: string;
  membership_version: number;
};

export type Membership = {
  membership_id: string;
  user_id: string;
  display_name: string | null;
  role_code: RoleCode;
  status: "ACTIVE" | "REVOKED";
  version: number;
  joined_at: string;
  revoked_at: string | null;
};

export type LeadStatus = "ACTIVE" | "ARCHIVED";

export type LeadValidationStatus =
  | "UNKNOWN"
  | "VALID"
  | "INVALID"
  | "RISKY"
  | "CATCH_ALL"
  | "DISPOSABLE";

// Optional profile fields; names match backend app/modules/leads/fields.py.
export type LeadProfile = {
  phone: string | null;
  department: string | null;
  experience_years: number | null;
  linkedin_url: string | null;
  website: string | null;
  city: string | null;
  state: string | null;
  country: string | null;
  company_website: string | null;
  company_industry: string | null;
  company_founded_year: number | null;
  company_linkedin_url: string | null;
};

export type LeadProfileKey = keyof LeadProfile;

export type Lead = LeadProfile & {
  id: string;
  workspace_id: string;
  email: string;
  canonical_address: string;
  normalization_version: number;
  first_name: string | null;
  last_name: string | null;
  company: string | null;
  title: string | null;
  custom_fields: Record<string, unknown>;
  status: LeadStatus;
  validation_status: LeadValidationStatus;
  validated_at: string | null;
  contact_revision: number;
  archived_at: string | null;
  version: number;
  created_at: string;
  updated_at: string;
};

export type LeadListSummary = {
  id: string;
  name: string;
  archived_at: string | null;
};

export type LeadDetail = Lead & {
  lists: LeadListSummary[];
};

export type LeadListItem = Lead & {
  list_count: number;
};

export type LeadPage = {
  items: LeadListItem[];
  next_cursor: string | null;
};

export type LeadList = {
  id: string;
  workspace_id: string;
  name: string;
  archived_at: string | null;
  membership_revision: number;
  member_count: number;
  version: number;
  created_at: string;
  updated_at: string;
};

export type LeadListPage = {
  items: LeadList[];
  next_cursor: string | null;
};

export type LeadListMember = {
  lead: Lead;
  added_by: string | null;
  added_at: string;
};

export type LeadListMemberPage = {
  items: LeadListMember[];
  next_cursor: string | null;
};

// --- Imports ---

export type ImportKind = "LEADS" | "SUPPRESSION";
export type ImportStatus =
  | "PENDING"
  | "PROCESSING"
  | "COMPLETED"
  | "COMPLETED_WITH_ERRORS"
  | "FAILED";
export type ImportRowStatus = "ACCEPTED" | "DUPLICATE" | "REJECTED";

export type ImportJob = {
  id: string;
  workspace_id: string;
  initiator_id: string;
  import_kind: ImportKind;
  mapping: Record<string, string>;
  list_id: string | null;
  status: ImportStatus;
  total_rows: number | null;
  processed_rows: number;
  accepted_rows: number;
  duplicate_rows: number;
  rejected_rows: number;
  failure_summary: string | null;
  version: number;
  created_at: string;
  updated_at: string;
};

export type ImportJobPage = {
  items: ImportJob[];
  next_cursor: string | null;
};

export type ImportRowResult = {
  row_number: number;
  status: ImportRowStatus;
  lead_id: string | null;
  suppression_id: string | null;
  validation_reason: string | null;
};

export type ImportRowResultPage = {
  items: ImportRowResult[];
  next_cursor: string | null;
};

export type ImportUploadOut = {
  storage_object_key: string;
  storage_object_version: string;
  storage_object_digest: string;
  headers: string[];
  sample_rows: Record<string, string>[];
  detected_total_rows: number;
  warnings: string[];
};

export type ImportCreateIn = {
  storage_object_key: string;
  storage_object_version: string;
  storage_object_digest: string;
  import_kind: ImportKind;
  mapping: {
    columns: Record<string, string>;
    source_filename?: string | null;
  };
  list_id?: string | null;
};

// --- Suppression ---

export type SuppressionReason =
  | "MANUAL"
  | "UNSUBSCRIBE"
  | "HARD_BOUNCE"
  | "COMPLAINT";
export type SuppressionStatus = "ACTIVE" | "RELEASED";

export type Suppression = {
  id: string;
  workspace_id: string;
  email: string;
  canonical_address: string;
  reason: SuppressionReason;
  status: SuppressionStatus;
  first_observed_at: string;
  last_observed_at: string;
  released_at: string | null;
  release_actor_id: string | null;
  removable: boolean;
  version: number;
  created_at: string;
  updated_at: string;
};

export type SuppressionPage = {
  items: Suppression[];
  next_cursor: string | null;
};

// --- Templates ---

export type TemplateMode = "STANDARD";

export type TemplateListItem = {
  id: string;
  workspace_id: string;
  name: string;
  current_version_id: string | null;
  mode: TemplateMode;
  archived_at: string | null;
  version: number;
  created_at: string;
  updated_at: string;
  current_revision: number | null;
  subject: string | null;
};

export type TemplateDetail = {
  id: string;
  workspace_id: string;
  name: string;
  current_version_id: string | null;
  mode: TemplateMode;
  archived_at: string | null;
  version: number;
  created_at: string;
  updated_at: string;
  current_revision: number | null;
  subject: string;
  body_html: string;
  preheader?: string | null;
  variable_schema: Record<string, unknown>;
  content_digest: string;
  renderer_version: number;
  version_created_at: string | null;
};

export type TemplatePage = {
  items: TemplateListItem[];
  next_cursor: string | null;
};

export type TemplateVersion = {
  id: string;
  template_id: string;
  revision: number;
  subject: string;
  body_html: string;
  preheader?: string | null;
  variable_schema: Record<string, unknown>;
  content_digest: string;
  renderer_version: number;
  created_at: string;
};

export type TemplatePreviewResult = {
  subject: string;
  body_html: string;
  // Rendered pre-header text; "" (or absent from older servers) when none.
  preheader?: string;
  detected_variables: string[];
  missing_variables: string[];
};

export type MailboxConnectionState =
  | "CONNECTED"
  | "DISCONNECTED"
  | "RECONNECT_REQUIRED";

export type MailboxHealthState = "HEALTHY" | "DEGRADED" | "UNKNOWN";

export type MailboxPolicyState = "ENABLED" | "DISABLED" | "RESTRICTED";

export type MailboxListItem = {
  id: string;
  provider: "GMAIL" | "MICROSOFT" | "SMTP";
  email_address: string;
  sender_display_name: string | null;
  connection_state: MailboxConnectionState;
  health_state: MailboxHealthState;
  policy_state: MailboxPolicyState;
  policy_reason: string | null;
  circuit_state: string;
  sync_state: string;
  created_at: string;
  updated_at: string;
};

export type SmtpSecurityMode = "STARTTLS" | "IMPLICIT_TLS";

export type SmtpConfigView = {
  host: string;
  port: number;
  security_mode: SmtpSecurityMode;
  username: string;
  // Reply detection reads the mailbox over IMAP; all null when not configured.
  imap_host?: string | null;
  imap_port?: number | null;
  imap_security_mode?: SmtpSecurityMode | null;
  imap_username?: string | null;
};

export type ReplySyncStatus =
  | "ENABLED"
  | "RECONNECT_REQUIRED"
  | "IMAP_NOT_CONFIGURED"
  | "UNSUPPORTED";

export type MailboxDetail = {
  id: string;
  provider: "GMAIL" | "MICROSOFT" | "SMTP";
  email_address: string;
  sender_display_name: string | null;
  signature_html: string | null;
  connection_state: MailboxConnectionState;
  health_state: MailboxHealthState;
  policy_state: MailboxPolicyState;
  policy_reason: string | null;
  circuit_state: string;
  sync_state: string;
  blocked_until: string | null;
  pending_safety_count: number;
  current_connection_generation: number;
  config_version: number;
  version: number;
  created_at: string;
  updated_at: string;
  smtp_config?: SmtpConfigView | null;
  reply_sync_status?: ReplySyncStatus;
};

export type GmailConnectStartResult = {
  authorization_url: string;
  expires_at: string;
};

export type GmailConnectCompleteResult = {
  mailbox_id: string;
  provider: string;
  email_address: string;
  connection_state: MailboxConnectionState;
  health_state: MailboxHealthState;
};

export type MicrosoftConnectStartResult = {
  authorization_url: string;
  expires_at: string;
};

export type MicrosoftConnectCompleteResult = {
  mailbox_id: string;
  provider: string;
  email_address: string;
  connection_state: MailboxConnectionState;
  health_state: MailboxHealthState;
};

export type SmtpConnectInput = {
  host: string;
  port: number;
  security_mode: SmtpSecurityMode;
  username: string;
  password: string;
  email_address: string;
  sender_display_name?: string | null;
  imap_host?: string;
  imap_port?: number;
  imap_security_mode?: SmtpSecurityMode;
  imap_username?: string;
  imap_password?: string;
};

export type SmtpConnectResult = {
  mailbox_id: string;
  provider: string;
  email_address: string;
  connection_state: MailboxConnectionState;
  health_state: MailboxHealthState;
};

export type SmtpUpdateInput = {
  host?: string;
  port?: number;
  security_mode?: SmtpSecurityMode;
  username?: string;
  // Omit entirely to keep the existing password.
  password?: string;
  sender_display_name?: string | null;
  imap_host?: string;
  imap_port?: number;
  imap_security_mode?: SmtpSecurityMode;
  imap_username?: string;
  imap_password?: string;
};

export type MailboxTestSendResult = {
  message_id: string;
  status: "SENT" | "FAILED" | "UNKNOWN_OUTCOME";
  recipient_email: string;
  provider_message_id?: string | null;
  accepted_at?: string | null;
  error_message?: string | null;
};

export type DisconnectMailboxResult = {
  mailbox_id: string;
  connection_state: "DISCONNECTED";
};

// --- Campaigns ---

export type CampaignStatus =
  | "DRAFT"
  | "SCHEDULED"
  | "RUNNING"
  | "PAUSED"
  | "ERROR"
  | "COMPLETED"
  | "ARCHIVED";

// Chosen once when the campaign is created and never changes (ADR-0011).
export type CampaignType = "STANDARD" | "HYPER_PERSONALIZED";

export type CampaignListItem = {
  id: string;
  workspace_id: string;
  name: string;
  description: string | null;
  status: CampaignStatus;
  // Absent on responses from a server that predates hyper-personalized campaigns.
  campaign_type?: CampaignType;
  draft_sequence_id: string | null;
  draft_audience_id: string | null;
  current_settings_id: string | null;
  version: number;
  created_at: string;
  updated_at: string;
};

export type CampaignPage = {
  items: CampaignListItem[];
  next_cursor: string | null;
};

export type CampaignDetail = {
  id: string;
  workspace_id: string;
  name: string;
  description: string | null;
  creator_id: string;
  status: CampaignStatus;
  campaign_type?: CampaignType;
  start_at: string | null;
  draft_sequence_id: string | null;
  draft_audience_id: string | null;
  current_settings_id: string | null;
  planning_status: "PENDING" | "READY";
  archived_at: string | null;
  error_reason: string | null;
  version: number;
  created_at: string;
  updated_at: string;
};

export type SequenceStepKind = "EMAIL" | "WAIT";

export type StepAttachment = {
  id: string;
  step_id: string;
  filename: string;
  content_type: string;
  size_bytes: number;
  disposition: "ATTACHMENT" | "INLINE";
  // Token used in the body as <img src="cid:CONTENT_ID">.
  content_id: string;
  created_at: string;
};

export type SequenceStep = {
  id: string;
  sequence_id: string;
  campaign_id: string;
  position: number;
  kind: SequenceStepKind;
  email_subject: string | null;
  email_body_html: string | null;
  email_preheader?: string | null;
  attachments?: StepAttachment[];
  email_variable_schema: Record<string, unknown> | null;
  wait_duration_minutes: number | null;
  source_template_version_id: string | null;
  version: number;
  created_at: string;
  updated_at: string;
};

export type SequencePreviewRecipient = {
  audience_member_id: string;
  lead_id: string;
  email: string | null;
  first_name: string | null;
  last_name: string | null;
  company: string | null;
  // The captured snapshot the real message renders with.
  variables: Record<string, unknown>;
};

export type SequencePreviewRecipients = {
  source: "AUDIENCE" | "NONE";
  items: SequencePreviewRecipient[];
  total: number | null;
  next_cursor: number | null;
};

export type CampaignSequence = {
  id: string | null;
  campaign_id: string;
  revision: number;
  status: "DRAFT" | "FROZEN" | "EMPTY";
  steps: SequenceStep[];
};

export type CampaignMailbox = {
  mailbox_id: string;
  provider: "GMAIL" | "MICROSOFT" | "SMTP";
  email_address: string;
  sender_display_name: string | null;
  connection_state: MailboxConnectionState;
  health_state: MailboxHealthState;
  policy_state: MailboxPolicyState;
  policy_reason: string | null;
  active: boolean;
  allocation_position: number;
};

export type CampaignSettings = {
  id: string;
  campaign_id: string;
  revision: number;
  timezone: string;
  weekdays: number[];
  window_start_local: string;
  window_end_local: string;
  daily_limit: number | null;
  created_at: string;
};

export type AudienceStatus = "CAPTURING" | "READY" | "FAILED" | "ABANDONED";

export type CampaignAudience = {
  id: string;
  campaign_id: string;
  revision: number;
  status: AudienceStatus;
  started_at: string;
  completed_at: string | null;
  is_committed: boolean;
  total_candidates: number | null;
  processed_count: number;
  accepted_count: number | null;
  excluded_count: number | null;
  error_reason: string | null;
};

export type PreflightIssue = {
  code: string;
  message: string;
  field_path: string | null;
};

export type PreflightResult = {
  ready: boolean;
  errors: PreflightIssue[];
  warnings: PreflightIssue[];
};

export type CampaignReview = {
  campaign: CampaignDetail;
  sequence: CampaignSequence | null;
  mailboxes: CampaignMailbox[];
  settings: CampaignSettings | null;
  audience: CampaignAudience | null;
  preflight: PreflightResult;
};

export type PlanningJobStatus = {
  phase: "ENROLL" | "RENDER";
  state: "PENDING" | "PROCESSING" | "READY" | "FAILED";
  processed_count: number;
  total_count: number | null;
  error_reason: string | null;
};

export type CampaignPlanning = {
  campaign_id: string;
  planning_status: "PENDING" | "READY";
  enroll: PlanningJobStatus | null;
  render: PlanningJobStatus | null;
};

// --- Hyper-personalized campaigns ---

export type PersonalizationCapabilities = {
  enabled: boolean;
  model: string | null;
};

export type PersonalizationConfig = {
  objective: string;
  offer: string;
  cta: string;
  target: string;
  problem_solved: string;
  tone: string;
  must_mention: string[];
  never_say: string[];
};

export type PersonalizationApproval = {
  status: "NONE" | "APPROVED" | "STALE";
  approved_at: string | null;
  approved_by: string | null;
};

export type PersonalizationState = {
  campaign_id: string;
  campaign_type: CampaignType;
  enabled: boolean;
  model: string | null;
  config: PersonalizationConfig | null;
  config_version: number | null;
  config_digest: string;
  approval: PersonalizationApproval;
};

export type PreviewFact = { id: string; source: "LEAD" | "WEBSITE"; text: string };

export type PreviewItem = {
  id: string;
  recipient: {
    audience_member_id: string;
    first_name: string | null;
    last_name: string | null;
    company: string | null;
    title: string | null;
  };
  step_id: string;
  step_position: number;
  state: "PENDING" | "OK" | "FAILED";
  subject: string | null;
  body_html: string | null;
  facts: PreviewFact[];
  research_summary: { status?: string; source_url?: string | null; excerpt?: string } | null;
  fallback_used: boolean;
  failure_codes: string[];
};

export type PreviewBatch = {
  batch_id: string;
  config_digest: string;
  current_digest: string;
  stale: boolean;
  created_at: string;
  expires_at: string;
  complete: boolean;
  all_ok: boolean;
  items: PreviewItem[];
};

export type GenerationProgress = {
  campaign_id: string;
  pending: number;
  succeeded: number;
  failed: number;
  superseded: number;
  fallback: number;
  oldest_pending_at: string | null;
  failure_codes: Record<string, number>;
  budget: {
    generation_used: number;
    generation_cap: number;
    preview_used: number;
    preview_cap: number;
    fetch_used: number;
    fetch_cap: number;
  };
};

export type InboxFilter = "ALL" | "UNREAD" | "READ" | "REPLIED" | "ARCHIVED";

export type ConversationListItem = {
  id: string;
  mailbox_id: string;
  mailbox_address: string;
  mailbox_provider: string;
  campaign_id: string | null;
  campaign_name: string | null;
  subject: string;
  snippet: string;
  latest_activity_at: string;
  is_read: boolean;
  read_at: string | null;
  archived_at: string | null;
  participant_email: string;
  participant_name: string | null;
  reply_status: string;
  message_count: number;
};

export type ConversationPage = {
  items: ConversationListItem[];
  next_cursor: string | null;
  has_more: boolean;
  unread_count: number;
};

export type MessageThreadItem = {
  id: string;
  direction: "INBOUND" | "OUTBOUND";
  sender_email: string;
  sender_name: string | null;
  recipient_email: string;
  recipient_name: string | null;
  subject: string;
  content_text: string | null;
  content_html: string | null;
  timestamp: string;
  status: string | null;
  sequence_step_id: string | null;
  /** 1-based step of the email (for a reply: the email that was replied to). */
  sequence_step_position?: number | null;
  association_status: string | null;
  classification: string | null;
};

export type ConversationDetail = {
  id: string;
  workspace_id: string;
  mailbox_id: string;
  mailbox_address: string;
  mailbox_provider: string;
  campaign_id: string | null;
  campaign_name: string | null;
  subject: string;
  latest_activity_at: string;
  created_at: string;
  is_read: boolean;
  read_at: string | null;
  archived_at: string | null;
  reply_status: string;
  participant_email: string;
  participant_name: string | null;
  lead_id: string | null;
  lead_company: string | null;
  messages: MessageThreadItem[];
};

export type ConversationActionResult = {
  id: string;
  is_read: boolean;
  read_at: string | null;
  archived_at: string | null;
  updated_at: string | null;
};

export type MailboxSyncStatusItem = {
  mailbox_id: string;
  email_address: string;
  provider: string;
  connection_status: string;
  sync_scope: string;
  sync_status: string;
  last_complete_at: string | null;
  failure_count: number;
};

export type InboxSyncStatusResponse = {
  mailboxes: MailboxSyncStatusItem[];
};

export type WorkspaceMember = {
  membership_id: string;
  user_id: string;
  display_name: string | null;
  email: string | null;
  role_code: RoleCode;
  status: "ACTIVE" | "REVOKED";
  version: number;
  joined_at: string;
  revoked_at: string | null;
};

export type WorkspaceInvitation = {
  id: string;
  workspace_id: string;
  email: string;
  role_code: RoleCode;
  status: "PENDING" | "ACCEPTED" | "REVOKED" | "EXPIRED";
  invited_by_user_id: string | null;
  expires_at: string;
  created_at: string;
  updated_at: string;
};

export type VerifiedInvitation = {
  valid: boolean;
  email: string;
  workspace_name: string;
  role_code: RoleCode;
  expires_at: string;
};

export type NotificationItem = {
  id: string;
  workspace_id: string;
  user_id: string;
  category: string;
  title: string;
  body: string;
  data: Record<string, unknown>;
  is_read: boolean;
  read_at: string | null;
  created_at: string;
};

export type NotificationPreferences = {
  email_notifications_enabled: boolean;
  in_app_notifications_enabled: boolean;
  category_preferences: Record<string, boolean>;
};

export type DimensionUsage = {
  used: number;
  limit: number | null;
  unit: string;
};

export type WorkspaceUsage = {
  workspace_id: string;
  plan_name: string;
  dimensions: Record<string, DimensionUsage>;
  as_of: string;
};

