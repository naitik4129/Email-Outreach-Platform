import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({
  useParams: () => ({ campaign_id: "camp-1" }),
}));

const api = vi.hoisted(() => ({
  getSequence: vi.fn(),
  getCampaign: vi.fn(),
  listCampaignMailboxes: vi.fn(),
  listCampaignSettings: vi.fn(),
  addSequenceStep: vi.fn(),
  deleteSequenceStep: vi.fn(),
  duplicateSequenceStep: vi.fn(),
  reorderSequenceSteps: vi.fn(),
  updateSequenceStep: vi.fn(),
}));
vi.mock("@/lib/campaigns-api", () => api);

const { useWorkspace } = vi.hoisted(() => ({ useWorkspace: vi.fn() }));
vi.mock("@/lib/workspace-context", () => ({ useWorkspace }));

// The dialog (TipTap + preview) has its own tests; here it is a stub that just
// reports which step it was opened for.
vi.mock("@/components/campaigns/sequence/email-step-dialog", () => ({
  EmailStepDialog: (props: { step: { id: string }; stepNumber: number; readOnly: boolean }) => (
    <div role="dialog" aria-label="Step editor">
      editing {props.step.id} as step {props.stepNumber} {props.readOnly ? "read-only" : "editable"}
    </div>
  ),
}));

import { ApiError } from "@/lib/api-client";

import CampaignSequencePage from "./page";

function renderPage() {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const invalidate = vi.spyOn(client, "invalidateQueries");
  const utils = render(
    <QueryClientProvider client={client}>
      <CampaignSequencePage />
    </QueryClientProvider>,
  );
  return { ...utils, invalidate };
}

function mockWorkspace(roleCode: string) {
  useWorkspace.mockReturnValue({
    activeWorkspaceId: "ws-1",
    activeWorkspace: { role_code: roleCode },
  });
}

function makeStep(
  id: string,
  position: number,
  kind: "EMAIL" | "WAIT",
  extra: Record<string, unknown> = {},
) {
  return {
    id,
    sequence_id: "seq-1",
    campaign_id: "camp-1",
    position,
    kind,
    email_subject: kind === "EMAIL" ? `Subject ${id}` : null,
    email_body_html: kind === "EMAIL" ? `<p>Body of ${id}</p>` : null,
    email_preheader: null,
    email_variable_schema: kind === "EMAIL" ? {} : null,
    wait_duration_minutes: kind === "WAIT" ? 2880 : null,
    source_template_version_id: null,
    version: 1,
    created_at: "2026-01-01T00:00:00Z",
    updated_at: "2026-01-01T00:00:00Z",
    ...extra,
  };
}

function makeSequence(steps: ReturnType<typeof makeStep>[], status = "DRAFT") {
  return { id: "seq-1", campaign_id: "camp-1", revision: 1, status, steps };
}

const emptySequence = { id: null, campaign_id: "camp-1", revision: 0, status: "EMPTY", steps: [] };

// Email 1, wait 2 days, Email 2, wait 3 days, Email 3
const threeEmails = () =>
  makeSequence([
    makeStep("e1", 1, "EMAIL"),
    makeStep("w1", 2, "WAIT", { wait_duration_minutes: 2880 }),
    makeStep("e2", 3, "EMAIL"),
    makeStep("w2", 4, "WAIT", { wait_duration_minutes: 4320 }),
    makeStep("e3", 5, "EMAIL"),
  ]);

beforeEach(() => {
  api.getCampaign.mockResolvedValue({ id: "camp-1", status: "DRAFT", version: 1 });
  api.listCampaignMailboxes.mockResolvedValue([]);
  api.listCampaignSettings.mockResolvedValue([
    {
      id: "set-1",
      campaign_id: "camp-1",
      revision: 1,
      timezone: "Europe/London",
      weekdays: [1, 2, 3, 4, 5],
      window_start_local: "09:00:00",
      window_end_local: "17:00:00",
      daily_limit: null,
      created_at: "",
    },
  ]);
});

afterEach(() => {
  vi.clearAllMocks();
  vi.restoreAllMocks();
});

describe("CampaignSequencePage", () => {
  it("shows an empty state and adds the first email without a wait", async () => {
    mockWorkspace("MEMBER");
    api.getSequence.mockResolvedValue(emptySequence);
    api.addSequenceStep.mockResolvedValue(makeStep("new", 1, "EMAIL"));
    const user = userEvent.setup();
    renderPage();

    expect(await screen.findByText(/no steps yet/i)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /add email step/i }));

    await waitFor(() =>
      expect(api.addSequenceStep).toHaveBeenCalledWith(
        "ws-1",
        "camp-1",
        expect.objectContaining({
          kind: "EMAIL",
          position: 1,
          leading_wait_minutes: undefined,
        }),
      ),
    );
  });

  it("opens the new step's editor after adding it", async () => {
    mockWorkspace("MEMBER");
    api.getSequence
      .mockResolvedValueOnce(emptySequence)
      .mockResolvedValue(makeSequence([makeStep("new", 1, "EMAIL")]));
    api.addSequenceStep.mockResolvedValue(makeStep("new", 1, "EMAIL"));
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: /add email step/i }));

    expect(await screen.findByRole("dialog", { name: /step editor/i })).toHaveTextContent(
      "editing new as step 1 editable",
    );
  });

  it("shows the order of execution with the wait between steps and Day numbers", async () => {
    mockWorkspace("MEMBER");
    api.getSequence.mockResolvedValue(threeEmails());
    renderPage();

    expect(await screen.findByRole("article", { name: "Step 1: Email" })).toHaveTextContent(
      "Day 1",
    );
    expect(screen.getByRole("article", { name: "Step 2: Email" })).toHaveTextContent("Day 3");
    expect(screen.getByRole("article", { name: "Step 3: Email" })).toHaveTextContent("Day 6");

    const waits = screen.getAllByRole("group", { name: "Wait" });
    expect(waits).toHaveLength(2);
    expect(waits[0]).toHaveTextContent("Wait 2 days");
    expect(waits[0]).toHaveTextContent("Step 2 goes out 2 days after Step 1 is sent");
    expect(waits[1]).toHaveTextContent("Wait 3 days");
    expect(waits[1]).toHaveTextContent("Step 3 goes out 3 days after Step 2 is sent");

    // Timeline order matches execution order.
    const cards = screen.getAllByRole("article");
    expect(cards.map((c) => c.getAttribute("aria-label"))).toEqual([
      "Step 1: Email",
      "Step 2: Email",
      "Step 3: Email",
    ]);
    expect(screen.getByText(/Mon, Tue, Wed, Thu, Fri, 09:00–17:00 Europe\/London/)).toBeVisible();
  });

  it("opens an existing step for editing", async () => {
    mockWorkspace("MEMBER");
    api.getSequence.mockResolvedValue(threeEmails());
    const user = userEvent.setup();
    renderPage();

    const card = await screen.findByRole("article", { name: "Step 2: Email" });
    await user.click(within(card).getByRole("button", { name: /edit/i }));

    expect(await screen.findByRole("dialog", { name: /step editor/i })).toHaveTextContent(
      "editing e2 as step 2 editable",
    );
  });

  it("appends a new step with a leading wait in one request", async () => {
    mockWorkspace("MEMBER");
    api.getSequence.mockResolvedValue(threeEmails());
    api.addSequenceStep.mockResolvedValue(makeStep("e4", 7, "EMAIL"));
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: /^add step$/i }));

    await waitFor(() =>
      expect(api.addSequenceStep).toHaveBeenCalledWith(
        "ws-1",
        "camp-1",
        expect.objectContaining({
          kind: "EMAIL",
          position: 6,
          leading_wait_minutes: 2880,
        }),
      ),
    );
  });

  it("inserts a step after a given email", async () => {
    mockWorkspace("MEMBER");
    api.getSequence.mockResolvedValue(threeEmails());
    api.addSequenceStep.mockResolvedValue(makeStep("mid", 3, "EMAIL"));
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: /insert a step after step 1/i }));

    await waitFor(() =>
      expect(api.addSequenceStep).toHaveBeenCalledWith(
        "ws-1",
        "camp-1",
        expect.objectContaining({ kind: "EMAIL", position: 2, leading_wait_minutes: 2880 }),
      ),
    );
  });

  it("deletes an email together with its adjacent wait after confirming", async () => {
    mockWorkspace("MEMBER");
    api.getSequence.mockResolvedValue(threeEmails());
    api.deleteSequenceStep.mockResolvedValue(undefined);
    const confirm = vi.spyOn(window, "confirm").mockReturnValue(true);
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: /delete step 2/i }));

    expect(confirm).toHaveBeenCalled();
    await waitFor(() =>
      expect(api.deleteSequenceStep).toHaveBeenCalledWith("ws-1", "camp-1", "e2", {
        withAdjacentWait: true,
      }),
    );
  });

  it("does not delete when the confirmation is declined", async () => {
    mockWorkspace("MEMBER");
    api.getSequence.mockResolvedValue(threeEmails());
    vi.spyOn(window, "confirm").mockReturnValue(false);
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: /delete step 2/i }));

    expect(api.deleteSequenceStep).not.toHaveBeenCalled();
  });

  it("duplicates an email step", async () => {
    mockWorkspace("MEMBER");
    api.getSequence.mockResolvedValue(threeEmails());
    api.duplicateSequenceStep.mockResolvedValue(threeEmails());
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: /duplicate step 1/i }));

    await waitFor(() =>
      expect(api.duplicateSequenceStep).toHaveBeenCalledWith("ws-1", "camp-1", "e1"),
    );
  });

  it("moves an email by swapping email slots and leaving waits in place", async () => {
    mockWorkspace("MEMBER");
    api.getSequence.mockResolvedValue(threeEmails());
    api.reorderSequenceSteps.mockResolvedValue(threeEmails());
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: /move step 2 up/i }));

    await waitFor(() => expect(api.reorderSequenceSteps).toHaveBeenCalled());
    const [, , payload] = api.reorderSequenceSteps.mock.calls[0];
    const positions = Object.fromEntries(
      (payload as { step_id: string; position: number }[]).map((p) => [p.step_id, p.position]),
    );
    // e1 and e2 trade slots (1 <-> 3); both waits and e3 keep theirs.
    expect(positions).toEqual({ e1: 3, w1: 2, e2: 1, w2: 4, e3: 5 });
  });

  it("disables moving the first step up and the last step down", async () => {
    mockWorkspace("MEMBER");
    api.getSequence.mockResolvedValue(threeEmails());
    renderPage();

    expect(await screen.findByRole("button", { name: /move step 1 up/i })).toBeDisabled();
    expect(screen.getByRole("button", { name: /move step 3 down/i })).toBeDisabled();
  });

  it("edits a wait in hours and saves minutes with the step version", async () => {
    mockWorkspace("MEMBER");
    api.getSequence.mockResolvedValue(threeEmails());
    api.updateSequenceStep.mockResolvedValue(makeStep("w1", 2, "WAIT", { wait_duration_minutes: 180 }));
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: /edit wait: 2 days/i }));
    const amount = screen.getByRole("spinbutton");
    await user.clear(amount);
    await user.type(amount, "3");
    await user.selectOptions(screen.getByLabelText("Unit"), "hours");
    expect(
      screen.getByText(/Step 2 will be sent 3 hours after Step 1 is sent/i),
    ).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /save wait/i }));

    await waitFor(() =>
      expect(api.updateSequenceStep).toHaveBeenCalledWith("ws-1", "camp-1", "w1", {
        expected_version: 1,
        wait_duration_minutes: 180,
      }),
    );
  });

  it("rejects an invalid wait in the editor without calling the API", async () => {
    mockWorkspace("MEMBER");
    api.getSequence.mockResolvedValue(threeEmails());
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: /edit wait: 2 days/i }));
    const amount = screen.getByRole("spinbutton");
    await user.clear(amount);
    await user.type(amount, "0");

    expect(screen.getByRole("alert")).toHaveTextContent(/at least 1/i);
    expect(screen.getByRole("button", { name: /save wait/i })).toBeDisabled();
    expect(api.updateSequenceStep).not.toHaveBeenCalled();
  });

  it("shows a read-only view for viewers", async () => {
    mockWorkspace("VIEWER");
    api.getSequence.mockResolvedValue(threeEmails());
    const user = userEvent.setup();
    renderPage();

    const card = await screen.findByRole("article", { name: "Step 1: Email" });
    expect(screen.queryByRole("button", { name: /^add step$/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /delete step/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /duplicate step/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /edit wait/i })).not.toBeInTheDocument();
    expect(screen.getAllByText(/^Wait 2 days$/).length).toBeGreaterThan(0);

    await user.click(within(card).getByRole("button", { name: /view/i }));
    expect(await screen.findByRole("dialog", { name: /step editor/i })).toHaveTextContent(
      "read-only",
    );
  });

  it("is read-only once the campaign is no longer a draft", async () => {
    mockWorkspace("OWNER");
    api.getCampaign.mockResolvedValue({ id: "camp-1", status: "RUNNING", version: 3 });
    api.getSequence.mockResolvedValue(makeSequence(threeEmails().steps, "FROZEN"));
    renderPage();

    expect(await screen.findByText(/campaign is running, so its sequence is read-only/i)).toBeVisible();
    expect(screen.queryByRole("button", { name: /^add step$/i })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /delete step/i })).not.toBeInTheDocument();
  });

  it("warns about a sequence that breaks Email/Wait alternation and lets you remove the stray wait", async () => {
    mockWorkspace("MEMBER");
    api.getSequence.mockResolvedValue(
      makeSequence([makeStep("e1", 1, "EMAIL"), makeStep("w1", 2, "WAIT")]),
    );
    api.deleteSequenceStep.mockResolvedValue(undefined);
    vi.spyOn(window, "confirm").mockReturnValue(true);
    const user = userEvent.setup();
    renderPage();

    expect(await screen.findByText(/must end with an email/i)).toBeVisible();
    await user.click(screen.getByRole("button", { name: /remove this wait/i }));
    await waitFor(() =>
      expect(api.deleteSequenceStep).toHaveBeenCalledWith("ws-1", "camp-1", "w1", {
        withAdjacentWait: false,
      }),
    );
  });

  it("surfaces a failed add and re-syncs the campaign when it is no longer a draft", async () => {
    mockWorkspace("MEMBER");
    api.getSequence.mockResolvedValue(emptySequence);
    api.addSequenceStep.mockRejectedValue(
      new ApiError("Sequence can only be edited while the campaign is DRAFT", 409, "state_conflict", null),
    );
    const user = userEvent.setup();
    const { invalidate } = renderPage();

    await user.click(await screen.findByRole("button", { name: /add email step/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/only be edited while the campaign is DRAFT/i);
    await waitFor(() =>
      expect(invalidate).toHaveBeenCalledWith({
        queryKey: ["workspace", "ws-1", "campaigns", "camp-1"],
      }),
    );
  });

  it("shows a generic message when the network fails", async () => {
    mockWorkspace("MEMBER");
    api.getSequence.mockResolvedValue(threeEmails());
    api.duplicateSequenceStep.mockRejectedValue(new TypeError("Failed to fetch"));
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: /duplicate step 1/i }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/couldn.t complete that request/i);
  });
});
