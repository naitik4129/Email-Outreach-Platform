import { afterEach, describe, expect, it, vi } from "vitest";

const apiRequest = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api-client", () => ({ apiRequest }));

import {
  approvePersonalization,
  createPersonalizationPreviews,
  getGenerationProgress,
  getLatestPersonalizationPreviews,
  getPersonalization,
  getPersonalizationCapabilities,
  getPersonalizationPreviews,
  newBatchId,
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
    await createPersonalizationPreviews("ws-1", "c-1", { batch_id: "b-9" });
    const [path, init] = apiRequest.mock.calls[0];
    expect(path).toBe(`${base}/previews`);
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body)).toEqual({ batch_id: "b-9" });
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
});
