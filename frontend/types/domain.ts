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

export type Lead = {
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
  variable_schema: Record<string, unknown>;
  content_digest: string;
  renderer_version: number;
  created_at: string;
};

export type TemplatePreviewResult = {
  subject: string;
  body_html: string;
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
};

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
