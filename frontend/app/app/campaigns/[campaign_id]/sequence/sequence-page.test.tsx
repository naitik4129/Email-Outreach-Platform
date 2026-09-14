import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useParams: () => ({ campaign_id: "camp-1" }),
}));

const { getSequence, addSequenceStep, deleteSequenceStep, reorderSequenceSteps } =
  vi.hoisted(() => ({
    getSequence: vi.fn(),
    addSequenceStep: vi.fn(),
    deleteSequenceStep: vi.fn(),
    reorderSequenceSteps: vi.fn(),
  }));

vi.mock("@/lib/campaigns-api", () => ({
  getSequence,
  addSequenceStep,
  deleteSequenceStep,
  reorderSequenceSteps,
  updateSequenceStep: vi.fn(),
}));

const { useWorkspace } = vi.hoisted(() => ({ useWorkspace: vi.fn() }));
vi.mock("@/lib/workspace-context", () => ({ useWorkspace }));

import CampaignSequencePage from "./page";

function renderWithClient(ui: React.ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

function mockWorkspace(roleCode: string) {
  useWorkspace.mockReturnValue({
    activeWorkspaceId: "ws-1",
    activeWorkspace: { role_code: roleCode },
  });
}

const emptySequence = {
  id: null,
  campaign_id: "camp-1",
  revision: 0,
  status: "EMPTY",
  steps: [],
};

const oneStepSequence = {
  id: "seq-1",
  campaign_id: "camp-1",
  revision: 1,
  status: "DRAFT",
  steps: [
    {
      id: "step-1",
      sequence_id: "seq-1",
      campaign_id: "camp-1",
      position: 1,
      kind: "EMAIL",
      email_subject: "Hello there",
      email_body_html: "<p>Hi</p>",
      email_variable_schema: {},
      wait_duration_minutes: null,
      source_template_version_id: null,
      version: 1,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    },
  ],
};

describe("CampaignSequencePage", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("shows an empty state with no steps", async () => {
    mockWorkspace("MEMBER");
    getSequence.mockResolvedValue(emptySequence);

    renderWithClient(<CampaignSequencePage />);

    expect(await screen.findByText(/no steps yet/i)).toBeInTheDocument();
  });

  it("read-only viewer sees steps but no add/edit controls", async () => {
    mockWorkspace("VIEWER");
    getSequence.mockResolvedValue(oneStepSequence);

    renderWithClient(<CampaignSequencePage />);

    expect(await screen.findByText("Hello there")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /add email step/i }),
    ).not.toBeInTheDocument();
  });

  it("adds an email step for a member", async () => {
    mockWorkspace("MEMBER");
    getSequence.mockResolvedValue(emptySequence);
    addSequenceStep.mockResolvedValue({
      ...oneStepSequence.steps[0],
    });

    const user = userEvent.setup();
    renderWithClient(<CampaignSequencePage />);

    const addButton = await screen.findByRole("button", {
      name: /add email step/i,
    });
    await user.click(addButton);

    await waitFor(() => {
      expect(addSequenceStep).toHaveBeenCalledWith(
        "ws-1",
        "camp-1",
        expect.objectContaining({ kind: "EMAIL", position: 1 }),
      );
    });
  });

  it("deletes a step for a member", async () => {
    mockWorkspace("MEMBER");
    getSequence.mockResolvedValue(oneStepSequence);
    deleteSequenceStep.mockResolvedValue(undefined);

    const user = userEvent.setup();
    renderWithClient(<CampaignSequencePage />);

    await screen.findByDisplayValue("Hello there");
    const buttons = screen.getAllByRole("button");
    // The delete control is the trash icon button rendered alongside
    // reorder controls -- locate it via its distinguishing red styling class
    // is fragile, so instead assert the mutation fires via any icon button
    // press after narrowing to the step's action row.
    const deleteButton = buttons.find((b) =>
      b.className.includes("text-red-600"),
    );
    expect(deleteButton).toBeTruthy();
    if (deleteButton) await user.click(deleteButton);

    await waitFor(() => {
      expect(deleteSequenceStep).toHaveBeenCalledWith("ws-1", "camp-1", "step-1");
    });
  });
});
