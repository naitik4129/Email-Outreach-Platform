import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ useParams: () => ({ campaign_id: "camp-1" }) }));

const campaignsApi = vi.hoisted(() => ({ getCampaign: vi.fn() }));
vi.mock("@/lib/campaigns-api", () => campaignsApi);

const api = vi.hoisted(() => ({
  getPersonalization: vi.fn(),
  getPersonalizationCapabilities: vi.fn(),
  savePersonalization: vi.fn(),
  approvePersonalization: vi.fn(),
  createPersonalizationPreviews: vi.fn(),
  getLatestPersonalizationPreviews: vi.fn(),
  getGenerationProgress: vi.fn(),
  newBatchId: vi.fn(() => "batch-new"),
}));
vi.mock("@/lib/personalization-api", () => api);

const { useWorkspace } = vi.hoisted(() => ({ useWorkspace: vi.fn() }));
vi.mock("@/lib/workspace-context", () => ({ useWorkspace }));

import { ApiError } from "@/lib/api-client";
import type { PersonalizationState, PreviewBatch } from "@/types/domain";

import CampaignPersonalizationPage from "./page";

const config = {
  objective: "Book demos",
  offer: "Outbound automation",
  cta: "Open to a chat?",
  target: "",
  problem_solved: "",
  tone: "",
  must_mention: [],
  never_say: [],
};

function stateWith(overrides: Partial<PersonalizationState> = {}): PersonalizationState {
  return {
    campaign_id: "camp-1",
    campaign_type: "HYPER_PERSONALIZED",
    enabled: true,
    model: "m",
    config,
    config_version: 3,
    config_digest: "d".repeat(64),
    approval: { status: "NONE", approved_at: null, approved_by: null },
    ...overrides,
  };
}

function goodBatch(overrides: Partial<PreviewBatch> = {}): PreviewBatch {
  return {
    batch_id: "b1",
    config_digest: "d".repeat(64),
    current_digest: "d".repeat(64),
    stale: false,
    created_at: "2026-03-02T10:00:00Z",
    expires_at: "2026-03-09T10:00:00Z",
    complete: true,
    all_ok: true,
    items: [
      {
        id: "p1",
        recipient: {
          audience_member_id: "m1",
          first_name: "Sarah",
          last_name: null,
          company: "Acme",
          title: null,
        },
        step_id: "s1",
        step_position: 1,
        state: "OK",
        subject: "Quick idea for Acme",
        body_html: "<p>Hello Sarah</p>",
        facts: [],
        research_summary: null,
        fallback_used: false,
        failure_codes: [],
      },
    ],
    ...overrides,
  };
}

function campaign(overrides: Record<string, unknown> = {}) {
  return {
    id: "camp-1",
    name: "Founders",
    status: "DRAFT",
    campaign_type: "HYPER_PERSONALIZED",
    ...overrides,
  };
}

function setup(role = "MANAGER") {
  useWorkspace.mockReturnValue({ activeWorkspaceId: "ws-1", activeWorkspace: { role_code: role } });
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <CampaignPersonalizationPage />
    </QueryClientProvider>,
  );
  return { user: userEvent.setup(), client };
}

describe("Personalization tab", () => {
  beforeEach(() => {
    campaignsApi.getCampaign.mockResolvedValue(campaign());
    api.getPersonalizationCapabilities.mockResolvedValue({ enabled: true, model: "m" });
    api.getPersonalization.mockResolvedValue(stateWith());
    api.getLatestPersonalizationPreviews.mockResolvedValue(null);
    api.getGenerationProgress.mockResolvedValue(null);
  });
  afterEach(() => vi.clearAllMocks());

  it("loads the saved objective and offers to generate samples", async () => {
    setup();
    expect(await screen.findByDisplayValue("Book demos")).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Generate samples" })).toBeEnabled();
    expect(screen.getByText("Not approved yet")).toBeInTheDocument();
  });

  it("says a standard campaign has no personalization settings", async () => {
    campaignsApi.getCampaign.mockResolvedValue(campaign({ campaign_type: "STANDARD" }));
    setup();
    expect(await screen.findByText(/This is a standard campaign/i)).toBeInTheDocument();
    expect(api.getPersonalization).not.toHaveBeenCalled();
  });

  it("explains when the feature is not enabled for this deployment", async () => {
    api.getPersonalizationCapabilities.mockResolvedValue({ enabled: false, model: null });
    setup();
    expect(await screen.findByText(/not enabled for this deployment/i)).toBeInTheDocument();
  });

  it("saves the objective with the version guard and refreshes preflight", async () => {
    api.getPersonalization.mockResolvedValue(stateWith({ config: null, config_version: null }));
    api.savePersonalization.mockResolvedValue(stateWith());
    const { user, client } = setup();
    const invalidate = vi.spyOn(client, "invalidateQueries");
    await user.type(await screen.findByLabelText(/^Objective/), "Book demos");
    await user.type(screen.getByLabelText(/offering/i), "Outbound automation");
    await user.type(screen.getByLabelText(/call to action/i), "Open to a chat?");
    await user.click(screen.getByRole("button", { name: /save objective/i }));
    await waitFor(() => expect(api.savePersonalization).toHaveBeenCalled());
    const [ws, campaignId, payload] = api.savePersonalization.mock.calls[0];
    expect(ws).toBe("ws-1");
    expect(campaignId).toBe("camp-1");
    expect(payload.config).toMatchObject({ objective: "Book demos", cta: "Open to a chat?" });
    expect(payload.expected_version).toBeNull();
    await waitFor(() =>
      expect(
        invalidate.mock.calls.some((c) =>
          JSON.stringify((c[0] as { queryKey: unknown[] }).queryKey).includes("preflight"),
        ),
      ).toBe(true),
    );
  });

  it("shows a save conflict and reloads the latest objective", async () => {
    api.savePersonalization.mockRejectedValue(
      new ApiError("The objective was modified by another user.", 409, "conflict", null),
    );
    const { user } = setup();
    await user.type(await screen.findByLabelText(/^Tone/), "Warm");
    await user.click(screen.getByRole("button", { name: /save objective/i }));
    expect(await screen.findByText(/modified by another user/i)).toBeInTheDocument();
    await waitFor(() => expect(api.getPersonalization.mock.calls.length).toBeGreaterThan(1));
  });

  it("generates samples with a fresh batch id and shows them", async () => {
    // Like the real server: once created, the batch is what "latest" returns.
    api.createPersonalizationPreviews.mockImplementation(async () => {
      api.getLatestPersonalizationPreviews.mockResolvedValue(goodBatch());
      return goodBatch();
    });
    const { user } = setup();
    await user.click(await screen.findByRole("button", { name: "Generate samples" }));
    await waitFor(() => expect(api.createPersonalizationPreviews).toHaveBeenCalled());
    expect(api.createPersonalizationPreviews.mock.calls[0][2]).toEqual({ batch_id: "batch-new" });
    expect(await screen.findByText("Quick idea for Acme")).toBeInTheDocument();
  });

  it("does not offer samples until an objective exists", async () => {
    api.getPersonalization.mockResolvedValue(stateWith({ config: null }));
    setup();
    const button = await screen.findByRole("button", { name: "Generate samples" });
    expect(button).toBeDisabled();
    expect(screen.getByText(/Save the campaign objective first/)).toBeInTheDocument();
  });

  it("lets a manager approve the current batch, bound to its digest", async () => {
    api.getLatestPersonalizationPreviews.mockResolvedValue(goodBatch());
    const approvedState = stateWith({
      approval: { status: "APPROVED", approved_at: "2026-03-02T10:05:00Z", approved_by: "u1" },
    });
    api.approvePersonalization.mockImplementation(async () => {
      api.getPersonalization.mockResolvedValue(approvedState);
      return approvedState;
    });
    const { user } = setup("MANAGER");
    await user.click(await screen.findByRole("button", { name: "Approve samples" }));
    await waitFor(() => expect(api.approvePersonalization).toHaveBeenCalled());
    expect(api.approvePersonalization.mock.calls[0][2]).toEqual({
      batch_id: "b1",
      config_digest: "d".repeat(64),
    });
    expect(await screen.findByText("Approved")).toBeInTheDocument();
  });

  it("shows why an approval was refused and refreshes the state", async () => {
    api.getLatestPersonalizationPreviews.mockResolvedValue(goodBatch());
    api.approvePersonalization.mockRejectedValue(
      new ApiError("The objective or emails changed.", 409, "approval_stale", null),
    );
    const { user } = setup("MANAGER");
    await user.click(await screen.findByRole("button", { name: "Approve samples" }));
    expect(await screen.findByText(/objective or emails changed\./i)).toBeInTheDocument();
  });

  it("lets a member edit and generate but not approve", async () => {
    api.getLatestPersonalizationPreviews.mockResolvedValue(goodBatch());
    setup("MEMBER");
    expect(await screen.findByRole("button", { name: "Generate new samples" })).toBeEnabled();
    expect(screen.queryByRole("button", { name: "Approve samples" })).not.toBeInTheDocument();
    expect(screen.getByText(/Only Managers and above can approve/)).toBeInTheDocument();
  });

  it("is read-only for a viewer", async () => {
    setup("VIEWER");
    expect(await screen.findByLabelText(/^Objective/)).toBeDisabled();
    expect(screen.queryByRole("button", { name: /generate/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /approve/i })).not.toBeInTheDocument();
  });

  it("locks the objective and shows generation progress once activated", async () => {
    campaignsApi.getCampaign.mockResolvedValue(campaign({ status: "RUNNING" }));
    api.getGenerationProgress.mockResolvedValue({
      campaign_id: "camp-1",
      pending: 5,
      succeeded: 2,
      failed: 1,
      superseded: 0,
      fallback: 0,
      oldest_pending_at: null,
      failure_codes: { attempts_exhausted: 1 },
      budget: {
        generation_used: 3,
        generation_cap: 100,
        preview_used: 0,
        preview_cap: 10,
        fetch_used: 0,
        fetch_cap: 10,
      },
    });
    setup();
    expect(await screen.findByText(/objective is read-only/i)).toBeInTheDocument();
    expect(await screen.findByTestId("failed")).toHaveTextContent("1");
    expect(screen.queryByRole("button", { name: "Generate samples" })).not.toBeInTheDocument();
    expect(screen.getByLabelText(/^Objective/)).toBeDisabled();
  });
});
