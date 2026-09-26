import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ useParams: () => ({ campaign_id: "camp-1" }) }));

const campaignsApi = vi.hoisted(() => ({
  activateCampaign: vi.fn(),
  getCampaignPlanning: vi.fn(),
  getReview: vi.fn(),
  pauseCampaign: vi.fn(),
  resumeCampaign: vi.fn(),
}));
vi.mock("@/lib/campaigns-api", () => campaignsApi);

const personalizationApi = vi.hoisted(() => ({
  getPersonalization: vi.fn(),
  getGenerationProgress: vi.fn(),
}));
vi.mock("@/lib/personalization-api", () => personalizationApi);

const { useWorkspace } = vi.hoisted(() => ({ useWorkspace: vi.fn() }));
vi.mock("@/lib/workspace-context", () => ({ useWorkspace }));

import CampaignReviewPage from "./page";

function review(overrides: Record<string, unknown> = {}, preflight: Record<string, unknown> = {}) {
  return {
    campaign: {
      id: "camp-1",
      name: "Founders",
      status: "DRAFT",
      version: 2,
      campaign_type: "HYPER_PERSONALIZED",
      ...overrides,
    },
    sequence: { id: "s", campaign_id: "camp-1", revision: 1, status: "DRAFT", steps: [] },
    mailboxes: [],
    settings: null,
    audience: null,
    preflight: { ready: false, errors: [], warnings: [], ...preflight },
  };
}

function renderPage() {
  useWorkspace.mockReturnValue({ activeWorkspaceId: "ws-1", activeWorkspace: { role_code: "MANAGER" } });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <CampaignReviewPage />
    </QueryClientProvider>,
  );
}

const state = (status: string) => ({
  config: { objective: "Book demos with founders" },
  approval: { status, approved_at: null, approved_by: null },
});

describe("Review page for a hyper-personalized campaign", () => {
  afterEach(() => vi.clearAllMocks());

  it("sends preflight personalization issues to the Personalization tab", async () => {
    campaignsApi.getReview.mockResolvedValue(
      review(
        {},
        {
          errors: [
            {
              code: "personalization_not_approved",
              message: "Generate sample emails and approve them before launching.",
              field_path: "personalization",
            },
          ],
        },
      ),
    );
    personalizationApi.getPersonalization.mockResolvedValue(state("NONE"));
    renderPage();
    expect(
      await screen.findByText("Generate sample emails and approve them before launching."),
    ).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Fix" })).toHaveAttribute(
      "href",
      "/app/campaigns/camp-1/personalization",
    );
  });

  it("summarizes the objective and approval", async () => {
    campaignsApi.getReview.mockResolvedValue(review());
    personalizationApi.getPersonalization.mockResolvedValue(state("APPROVED"));
    renderPage();
    expect(await screen.findByText("Objective: Book demos with founders")).toBeInTheDocument();
    expect(screen.getByText(/Sample emails approved\./)).toBeInTheDocument();
  });

  it("flags a stale approval", async () => {
    campaignsApi.getReview.mockResolvedValue(review());
    personalizationApi.getPersonalization.mockResolvedValue(state("STALE"));
    renderPage();
    expect(await screen.findByText(/Approval is out of date/)).toBeInTheDocument();
  });

  it("shows generation progress once the campaign is running", async () => {
    campaignsApi.getReview.mockResolvedValue(review({ status: "RUNNING" }));
    campaignsApi.getCampaignPlanning.mockResolvedValue({
      campaign_id: "camp-1",
      planning_status: "READY",
      enroll: null,
      render: null,
    });
    personalizationApi.getPersonalization.mockResolvedValue(state("APPROVED"));
    personalizationApi.getGenerationProgress.mockResolvedValue({
      campaign_id: "camp-1",
      pending: 9,
      succeeded: 1,
      failed: 0,
      superseded: 0,
      fallback: 0,
      oldest_pending_at: null,
      failure_codes: {},
      budget: {
        generation_used: 1,
        generation_cap: 10,
        preview_used: 0,
        preview_cap: 10,
        fetch_used: 0,
        fetch_cap: 10,
      },
    });
    renderPage();
    expect(await screen.findByTestId("pending")).toHaveTextContent("9");
  });

  it("does not load personalization data for a standard campaign", async () => {
    campaignsApi.getReview.mockResolvedValue(review({ campaign_type: "STANDARD" }));
    renderPage();
    await screen.findByText("Review");
    expect(personalizationApi.getPersonalization).not.toHaveBeenCalled();
    expect(screen.queryByText(/Sample emails/)).not.toBeInTheDocument();
  });
});
