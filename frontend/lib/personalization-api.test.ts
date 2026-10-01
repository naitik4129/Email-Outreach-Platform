import { afterEach, describe, expect, it, vi } from "vitest";

const apiRequest = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api-client", () => ({ apiRequest }));

import {
  analyzeCompany,
  approvePersonalization,
  createPersonalizationPreviews,
  generateReferenceTemplates,
  getGenerationProgress,
  getLatestPersonalizationPreviews,
  getPersonalization,
  getPersonalizationCapabilities,
  getPersonalizationPreviews,
  newBatchId,
  previewEmailLayout,
  savePersonalization,
} from "@/lib/personalization-api";

const base = "/api/v1/workspaces/ws-1/campaigns/c-1/personalization";

describe("personalization api client", () => {
  afterEach(() => vi.clearAllMocks());

  it("reads capabilities from the workspace-scoped endpoint", async () => {
    apiRequest.mockResolvedValue({ data: { enabled: true, model: "m" } });
    expect(await getPersonalizationCapabilities("ws-1")).toEqual({ enabled: true, model: "m" });
    expect(apiRequest).toHaveBeenCalledWith("/api/v1/workspaces/ws-1/personalization/capabilities");
  });

  it("gets state, progress and previews from campaign-scoped paths", async () => {
    apiRequest.mockResolvedValue({ data: {} });
    await getPersonalization("ws-1", "c-1");
    await getGenerationProgress("ws-1", "c-1");
    await getLatestPersonalizationPreviews("ws-1", "c-1");
    await getPersonalizationPreviews("ws-1", "c-1", "b-1");
    expect(apiRequest.mock.calls.map((c) => c[0])).toEqual([
      base,
      `${base}/progress`,
      `${base}/previews/latest`,
      `${base}/previews/b-1`,
    ]);
  });

  it("saves the objective with a PUT carrying the version guard", async () => {
    apiRequest.mockResolvedValue({ data: {} });
    const config = {
      objective: "o",
      offer: "f",
      cta: "c",
      target: "",
      problem_solved: "",
      tone: "",
      must_mention: [],
      never_say: [],
    };
    await savePersonalization("ws-1", "c-1", { config, expected_version: 4 });
    const [path, init] = apiRequest.mock.calls[0];
    expect(path).toBe(base);
    expect(init.method).toBe("PUT");
    expect(JSON.parse(init.body)).toEqual({ config, expected_version: 4 });
  });

  it("creates samples with a caller-supplied batch id so retries are idempotent", async () => {
    apiRequest.mockResolvedValue({ data: {} });
    await createPersonalizationPreviews("ws-1", "c-1", {
      batch_id: "b-9",
      audience_member_ids: ["m-1"],
    });
    const [path, init] = apiRequest.mock.calls[0];
    expect(path).toBe(`${base}/previews`);
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ batch_id: "b-9", audience_member_ids: ["m-1"] });
  });

  it("approves a batch bound to its digest", async () => {
    apiRequest.mockResolvedValue({ data: {} });
    await approvePersonalization("ws-1", "c-1", { batch_id: "b-1", config_digest: "d".repeat(64) });
    const [path, init] = apiRequest.mock.calls[0];
    expect(path).toBe(`${base}/approve`);
    expect(JSON.parse(init.body)).toEqual({ batch_id: "b-1", config_digest: "d".repeat(64) });
  });

  it("generates distinct batch ids", () => {
    expect(newBatchId()).not.toEqual(newBatchId());
  });

  describe("AI authoring (ADR-0016)", () => {
    it("analyzes a website with a POST and the url only", async () => {
      apiRequest.mockResolvedValue({ data: { source: "WEBSITE" } });
      await analyzeCompany("ws-1", "c-1", { url: "https://acme.com/" });
      const [path, init] = apiRequest.mock.calls[0];
      expect(path).toBe(`${base}/company-analysis`);
      expect(init.method).toBe("POST");
      expect(JSON.parse(init.body)).toEqual({ url: "https://acme.com/" });
    });

    it("analyzes typed business info without a url", async () => {
      apiRequest.mockResolvedValue({ data: { source: "MANUAL" } });
      await analyzeCompany("ws-1", "c-1", {
        business: { company_name: "Acme", description: "We make anvils." },
      });
      expect(JSON.parse(apiRequest.mock.calls[0][1].body)).toEqual({
        business: { company_name: "Acme", description: "We make anvils." },
      });
    });

    it("previews the email layout through the server renderer", async () => {
      apiRequest.mockResolvedValue({ data: { html: "<p/>", warnings: [] } });
      const brand = {
        logo_url: null,
        logo_alt: "",
        primary: "#111111",
        accent: "#222222",
        text: "#333333",
        background: "#ffffff",
        font_key: "sans" as const,
        cta_url: null,
        cta_label: "",
      };
      await previewEmailLayout("ws-1", "c-1", { brand, company_name: "Acme", site_url: null });
      const [path, init] = apiRequest.mock.calls[0];
      expect(path).toBe(`${base}/email-layout-preview`);
      expect(init.method).toBe("POST");
      expect(JSON.parse(init.body)).toEqual({ brand, company_name: "Acme", site_url: null });
    });

    it("drafts every email, or one, with a POST", async () => {
      apiRequest.mockResolvedValue({ data: {} });
      await generateReferenceTemplates("ws-1", "c-1", { scope: "ALL", follow_up_count: 2 });
      await generateReferenceTemplates("ws-1", "c-1", { scope: "STEP", step_id: "s-1" });
      expect(apiRequest.mock.calls.map((c) => c[0])).toEqual([
        `${base}/reference-templates`,
        `${base}/reference-templates`,
      ]);
      expect(apiRequest.mock.calls.map((c) => JSON.parse(c[1].body))).toEqual([
        { scope: "ALL", follow_up_count: 2 },
        { scope: "STEP", step_id: "s-1" },
      ]);
      expect(apiRequest.mock.calls.every((c) => c[1].method === "POST")).toBe(true);
    });
  });
});
