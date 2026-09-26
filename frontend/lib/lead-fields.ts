import type { LeadProfile, LeadProfileKey } from "@/types/domain";

// Single frontend source for lead field metadata: the create/edit forms, the
// import column mapper and the template variable pickers all read from here.
// Field names must match backend app/modules/leads/fields.py (PROFILE_FIELDS).

export type LeadProfileGroup = "Professional" | "Location" | "Company";

export type LeadProfileField = {
  key: LeadProfileKey;
  label: string;
  group: LeadProfileGroup;
  input: "text" | "tel" | "url" | "number";
  placeholder?: string;
  min?: number;
  max?: number;
};

export const LEAD_PROFILE_GROUPS: LeadProfileGroup[] = [
  "Professional",
  "Location",
  "Company",
];

export const LEAD_PROFILE_FIELDS: LeadProfileField[] = [
  { key: "phone", label: "Phone", group: "Professional", input: "tel" },
  { key: "department", label: "Department", group: "Professional", input: "text" },
  {
    key: "experience_years",
    label: "Experience (years)",
    group: "Professional",
    input: "number",
    min: 0,
    max: 80,
  },
  {
    key: "linkedin_url",
    label: "LinkedIn URL",
    group: "Professional",
    input: "url",
    placeholder: "https://linkedin.com/in/…",
  },
  { key: "website", label: "Website", group: "Professional", input: "url" },
  { key: "city", label: "City", group: "Location", input: "text" },
  { key: "state", label: "State", group: "Location", input: "text" },
  { key: "country", label: "Country", group: "Location", input: "text" },
  { key: "company_website", label: "Company website", group: "Company", input: "url" },
  { key: "company_industry", label: "Company industry", group: "Company", input: "text" },
  {
    key: "company_founded_year",
    label: "Company founded year",
    group: "Company",
    input: "number",
    min: 1600,
    max: 2100,
  },
  {
    key: "company_linkedin_url",
    label: "Company LinkedIn URL",
    group: "Company",
    input: "url",
    placeholder: "https://linkedin.com/company/…",
  },
];

export type LeadProfileFormValues = Record<LeadProfileKey, string>;

export const emptyProfileValues: LeadProfileFormValues = Object.fromEntries(
  LEAD_PROFILE_FIELDS.map((field) => [field.key, ""]),
) as LeadProfileFormValues;

export function profileValuesFromLead(lead: LeadProfile): LeadProfileFormValues {
  return Object.fromEntries(
    LEAD_PROFILE_FIELDS.map((field) => [field.key, String(lead[field.key] ?? "")]),
  ) as LeadProfileFormValues;
}

// Blank means "no value": sent as null so an edit can clear a field. Format
// validation stays on the server, which is the authority.
export function profilePayload(values: LeadProfileFormValues): LeadProfile {
  return Object.fromEntries(
    LEAD_PROFILE_FIELDS.map((field) => {
      const raw = values[field.key].trim();
      if (raw === "") return [field.key, null];
      return [field.key, field.input === "number" ? Number(raw) : raw];
    }),
  ) as LeadProfile;
}

export function formatLocation(
  lead: Pick<LeadProfile, "city" | "state" | "country">,
): string {
  return [lead.city, lead.state, lead.country].filter(Boolean).join(", ");
}

// --- import column mapping -------------------------------------------------

export type LeadImportField = { key: string; label: string; aliases: string[] };

// Aliases are compared after normalizeHeader(), so "first_name", "First-Name"
// and "first name" are the same. Keys must match backend LEADS_MAPPABLE_FIELDS.
export const LEAD_IMPORT_FIELDS: LeadImportField[] = [
  { key: "email", label: "Email", aliases: ["email", "e mail", "email address", "work email"] },
  { key: "first_name", label: "First name", aliases: ["first name", "firstname", "given name"] },
  {
    key: "last_name",
    label: "Last name",
    aliases: ["last name", "lastname", "surname", "family name"],
  },
  {
    key: "company",
    label: "Company",
    aliases: ["company", "company name", "organization", "organisation", "employer"],
  },
  { key: "title", label: "Job title", aliases: ["title", "job title", "position", "role"] },
  {
    key: "phone",
    label: "Phone",
    aliases: ["phone", "phone number", "mobile", "mobile phone", "telephone", "cell"],
  },
  { key: "department", label: "Department", aliases: ["department", "dept"] },
  {
    key: "experience_years",
    label: "Experience (years)",
    aliases: ["experience", "experience years", "years of experience", "years experience"],
  },
  {
    key: "linkedin_url",
    label: "LinkedIn URL",
    aliases: ["linkedin", "linkedin url", "linkedin profile"],
  },
  { key: "website", label: "Website", aliases: ["website", "personal website", "web site"] },
  { key: "city", label: "City", aliases: ["city", "town"] },
  { key: "state", label: "State", aliases: ["state", "province", "region", "state province"] },
  { key: "country", label: "Country", aliases: ["country"] },
  {
    key: "company_website",
    label: "Company website",
    aliases: ["company website", "company url", "company domain", "organization website"],
  },
  {
    key: "company_industry",
    label: "Company industry",
    aliases: ["company industry", "industry", "sector"],
  },
  {
    key: "company_founded_year",
    label: "Company founded year",
    aliases: ["company founded year", "company founded", "founded", "founded year", "year founded"],
  },
  {
    key: "company_linkedin_url",
    label: "Company LinkedIn URL",
    aliases: ["company linkedin", "company linkedin url", "company linkedin page"],
  },
];

export const SUPPRESSION_IMPORT_FIELDS: LeadImportField[] = LEAD_IMPORT_FIELDS.filter(
  (field) => field.key === "email",
);

function normalizeHeader(header: string) {
  return header
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, " ")
    .trim();
}

// Returns { csvHeader: fieldKey }. Each field is claimed by at most one header
// (the first match) because the server takes one source column per field.
export function autoMapHeaders(
  headers: string[],
  fields: LeadImportField[],
): Record<string, string> {
  const mapped: Record<string, string> = {};
  const taken = new Set<string>();
  const claim = (header: string, key: string) => {
    if (taken.has(key)) return;
    mapped[header] = key;
    taken.add(key);
  };
  const allowed = new Set(fields.map((field) => field.key));

  for (const header of headers) {
    const normalized = normalizeHeader(header);
    const match = fields.find((field) => field.aliases.includes(normalized));
    if (match) claim(header, match.key);
  }
  // Looser fallbacks for headers no alias matched, as before this field set grew.
  for (const header of headers) {
    if (header in mapped) continue;
    const normalized = normalizeHeader(header);
    if (allowed.has("email") && normalized.includes("email")) claim(header, "email");
    else if (allowed.has("first_name") && normalized.includes("first"))
      claim(header, "first_name");
    else if (allowed.has("last_name") && normalized.includes("last"))
      claim(header, "last_name");
  }
  return mapped;
}

// The UI keeps { csvHeader: fieldKey }; the API wants { fieldKey: csvHeader }.
export function toApiColumns(mapping: Record<string, string>): Record<string, string> {
  return Object.fromEntries(Object.entries(mapping).map(([header, key]) => [key, header]));
}

export function duplicateMappedFields(mapping: Record<string, string>): string[] {
  const seen = new Set<string>();
  const duplicates = new Set<string>();
  for (const key of Object.values(mapping)) {
    if (seen.has(key)) duplicates.add(key);
    seen.add(key);
  }
  return [...duplicates];
}

// --- template variables ----------------------------------------------------

export type TemplateVariable = { label: string; code: string };

export const LEAD_TEMPLATE_VARIABLES: TemplateVariable[] = [
  { label: "First name", code: "{{first_name}}" },
  { label: "First name (with fallback)", code: "{{first_name|there}}" },
  { label: "Last name", code: "{{last_name}}" },
  { label: "Company", code: "{{company}}" },
  { label: "Job title", code: "{{title}}" },
  { label: "Email", code: "{{email}}" },
  ...LEAD_PROFILE_FIELDS.map((field) => ({
    label: field.label,
    code: `{{${field.key}}}`,
  })),
  { label: "Custom field", code: "{{custom.industry}}" },
];

export type TemplateVariableGroup = { label: string; variables: TemplateVariable[] };

function profileVariable(key: LeadProfileKey, label?: string): TemplateVariable {
  const field = LEAD_PROFILE_FIELDS.find((f) => f.key === key);
  return { label: label ?? field?.label ?? key, code: `{{${key}}}` };
}

// Grouped view of the same supported variables for the email editor's picker.
// Every code here must be accepted by backend app/modules/templates/variables.py.
export const TEMPLATE_VARIABLE_GROUPS: TemplateVariableGroup[] = [
  {
    label: "Contact",
    variables: [
      { label: "First name", code: "{{first_name}}" },
      { label: "Last name", code: "{{last_name}}" },
      { label: "Email", code: "{{email}}" },
    ],
  },
  {
    label: "Company",
    variables: [
      { label: "Company", code: "{{company}}" },
      { label: "Job title", code: "{{title}}" },
      profileVariable("company_industry", "Industry"),
      profileVariable("company_website"),
      profileVariable("company_linkedin_url"),
      profileVariable("company_founded_year"),
    ],
  },
  {
    label: "Location",
    variables: [profileVariable("city"), profileVariable("state"), profileVariable("country")],
  },
  {
    label: "More about the contact",
    variables: [
      profileVariable("phone"),
      profileVariable("department"),
      profileVariable("experience_years"),
      profileVariable("linkedin_url"),
      profileVariable("website"),
    ],
  },
];
