import { describe, expect, it } from "vitest";

import {
  LEAD_IMPORT_FIELDS,
  LEAD_PROFILE_FIELDS,
  LEAD_TEMPLATE_VARIABLES,
  SUPPRESSION_IMPORT_FIELDS,
  autoMapHeaders,
  duplicateMappedFields,
  emptyProfileValues,
  formatLocation,
  profilePayload,
  profileValuesFromLead,
  toApiColumns,
} from "./lead-fields";

// Mirrors backend app/modules/leads/fields.py PROFILE_FIELD_NAMES.
const BACKEND_PROFILE_FIELDS = [
  "phone",
  "department",
  "experience_years",
  "linkedin_url",
  "website",
  "city",
  "state",
  "country",
  "company_website",
  "company_industry",
  "company_founded_year",
  "company_linkedin_url",
];

describe("lead field registry", () => {
  it("covers exactly the backend profile fields", () => {
    expect(LEAD_PROFILE_FIELDS.map((field) => field.key).sort()).toEqual(
      [...BACKEND_PROFILE_FIELDS].sort(),
    );
  });

  it("offers every backend-mappable field for lead imports and only email for suppressions", () => {
    expect(LEAD_IMPORT_FIELDS.map((field) => field.key).sort()).toEqual(
      ["email", "first_name", "last_name", "company", "title", ...BACKEND_PROFILE_FIELDS].sort(),
    );
    expect(SUPPRESSION_IMPORT_FIELDS.map((field) => field.key)).toEqual(["email"]);
  });

  it("offers every profile field as a template variable", () => {
    const codes = LEAD_TEMPLATE_VARIABLES.map((variable) => variable.code);
    for (const key of BACKEND_PROFILE_FIELDS) expect(codes).toContain(`{{${key}}}`);
    // Existing variables keep working.
    expect(codes).toEqual(
      expect.arrayContaining([
        "{{first_name}}",
        "{{first_name|there}}",
        "{{last_name}}",
        "{{company}}",
        "{{title}}",
        "{{email}}",
        "{{custom.industry}}",
      ]),
    );
  });
});

describe("profile form values", () => {
  it("turns blanks into null, numbers into numbers, and trims text", () => {
    const payload = profilePayload({
      ...emptyProfileValues,
      phone: "  +1 555 123 4567 ",
      experience_years: "0",
      company_founded_year: " 1999 ",
      city: "   ",
    });

    expect(payload.phone).toBe("+1 555 123 4567");
    expect(payload.experience_years).toBe(0);
    expect(payload.company_founded_year).toBe(1999);
    expect(payload.city).toBeNull();
    expect(payload.website).toBeNull();
    expect(Object.keys(payload).sort()).toEqual([...BACKEND_PROFILE_FIELDS].sort());
  });

  it("round-trips a lead into form strings and back without changing values", () => {
    const lead = {
      ...profilePayload(emptyProfileValues),
      phone: "+1 555 123 4567",
      experience_years: 7,
      country: "Germany",
    };

    const values = profileValuesFromLead(lead);

    expect(values.experience_years).toBe("7");
    expect(values.department).toBe("");
    expect(profilePayload(values)).toEqual(lead);
  });

  it("formats a location from whichever parts exist", () => {
    expect(formatLocation({ city: "Berlin", state: null, country: "Germany" })).toBe(
      "Berlin, Germany",
    );
    expect(formatLocation({ city: null, state: null, country: null })).toBe("");
  });
});

describe("import column mapping", () => {
  it("auto-maps common header spellings, including the old exact-key headers", () => {
    const mapped = autoMapHeaders(
      [
        "Email Address",
        "First Name",
        "last_name",
        "Company Name",
        "Job Title",
        "Phone Number",
        "LinkedIn Profile",
        "Company Website",
        "Industry",
        "Founded",
        "Notes",
      ],
      LEAD_IMPORT_FIELDS,
    );

    expect(mapped).toEqual({
      "Email Address": "email",
      "First Name": "first_name",
      last_name: "last_name",
      "Company Name": "company",
      "Job Title": "title",
      "Phone Number": "phone",
      "LinkedIn Profile": "linkedin_url",
      "Company Website": "company_website",
      Industry: "company_industry",
      Founded: "company_founded_year",
    });
  });

  it("keeps the previous loose fallbacks for email/first/last", () => {
    expect(
      autoMapHeaders(["Contact Email", "First", "Last Name (legal)"], LEAD_IMPORT_FIELDS),
    ).toEqual({
      "Contact Email": "email",
      First: "first_name",
      "Last Name (legal)": "last_name",
    });
  });

  it("maps each field at most once, first header wins", () => {
    const mapped = autoMapHeaders(["Email", "Work Email"], LEAD_IMPORT_FIELDS);
    expect(mapped).toEqual({ Email: "email" });
  });

  it("never maps profile fields for suppression imports", () => {
    expect(autoMapHeaders(["Email", "Phone"], SUPPRESSION_IMPORT_FIELDS)).toEqual({
      Email: "email",
    });
  });

  it("sends the mapping to the API as field -> header", () => {
    expect(toApiColumns({ "Email Address": "email", "Phone Number": "phone" })).toEqual({
      email: "Email Address",
      phone: "Phone Number",
    });
  });

  it("detects a field mapped from two columns", () => {
    expect(duplicateMappedFields({ A: "email", B: "phone", C: "email" })).toEqual(["email"]);
    expect(duplicateMappedFields({ A: "email", B: "phone" })).toEqual([]);
  });
});
