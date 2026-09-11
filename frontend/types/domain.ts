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
