import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("next/navigation", () => ({ useParams: () => ({ campaign_id: "camp-1" }) }));

const campaignsApi = vi.hoisted(() => ({
  getSequence: vi.fn(),
  getCampaign: vi.fn(),
  listCampaignMailboxes: vi.fn(),
  listCampaignSettings: vi.fn(),
  addSequenceStep: vi.fn(),
  deleteSequenceStep: vi.fn(),
  duplicateSequenceStep: vi.fn(),
  reorderSequenceSteps: vi.fn(),
  updateSequenceStep: vi.fn(),
  listSequencePreviewRecipients: vi.fn(),
}));
vi.mock("@/lib/campaigns-api", () => campaignsApi);

const api = vi.hoisted(() => ({
  getPersonalization: vi.fn(),
  getPersonalizationCapabilities: vi.fn(),
  savePersonalization: vi.fn(),
  approvePersonalization: vi.fn(),
  createPersonalizationPreviews: vi.fn(),
  getLatestPersonalizationPreviews: vi.fn(),
  getGenerationProgress: vi.fn(),
  analyzeCompany: vi.fn(),
  previewEmailLayout: vi.fn(),
  generateReferenceTemplates: vi.fn(),
  newBatchId: vi.fn(() => "batch-new"),
}));
vi.mock("@/lib/personalization-api", () => api);

const { useWorkspace } = vi.hoisted(() => ({ useWorkspace: vi.fn() }));
vi.mock("@/lib/workspace-context", () => ({ useWorkspace }));

// The dialog (TipTap + preview) has its own tests; here it only reports how it was opened.
vi.mock("@/components/campaigns/sequence/email-step-dialog", () => ({
  EmailStepDialog: (props: { step: { id: string }; designedEmail?: boolean }) => (
    <div role="dialog" aria-label="Step editor">
      editing {props.step.id}
      {props.designedEmail ? " (designed email)" : " (plain email)"}
    </div>
  ),
}));

import { ApiError } from "@/lib/api-client";
import type {
  CompanyAnalysis,
  PersonalizationConfig,
  PersonalizationState,
  PreviewBatch,
} from "@/types/domain";

import CampaignSequencePage from "./page";

// --- fixtures --------------------------------------------------------------

const D = "d".repeat(64);

const CONFIG: PersonalizationConfig = {
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
    config: CONFIG,
    config_version: 3,
    config_digest: D,
    approval: { status: "NONE", approved_at: null, approved_by: null },
    ...overrides,
  };
}

function goodBatch(overrides: Partial<PreviewBatch> = {}): PreviewBatch {
  return {
    batch_id: "b1",
    config_digest: D,
    current_digest: D,
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

function step(id: string, position: number, kind: "EMAIL" | "WAIT") {
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
    created_at: "",
    updated_at: "",
  };
}

const emptySequence = { id: null, campaign_id: "camp-1", revision: 0, status: "EMPTY", steps: [] };
const threeEmails = () => ({
  id: "seq-1",
  campaign_id: "camp-1",
  revision: 1,
  status: "DRAFT",
  steps: [
    step("e1", 1, "EMAIL"),
    step("w1", 2, "WAIT"),
    step("e2", 3, "EMAIL"),
    step("w2", 4, "WAIT"),
    step("e3", 5, "EMAIL"),
  ],
});

const BRAND = {
  logo_url: "https://acme.example/logo.png",
  logo_alt: "Acme",
  primary: "#123456",
  accent: "#e91e63",
  text: "#111827",
  background: "#ffffff",
  font_key: "serif" as const,
  cta_url: null,
  cta_label: "",
};

function analysis(overrides: Partial<CompanyAnalysis> = {}): CompanyAnalysis {
  return {
    source: "WEBSITE",
    final_url: "https://acme.example/",
    profile: {
      source: "WEBSITE",
      url: "https://acme.example/",
      company_name: "Acme",
      summary: "Anvils for blacksmiths.",
      services: ["Anvils"],
      industries: ["Manufacturing"],
      audience: "Blacksmiths",
      tone_of_voice: "dry",
      key_messages: [],
    },
    brand: BRAND,
    suggestions: {
      objective: "Book a demo with blacksmiths",
      offer: "Heavy duty anvils",
      cta: "Reply with a time",
      tone: "dry",
    },
    pages_read: 2,
    warnings: [],
    brand_warnings: [],
    model: "m",
    ...overrides,
  };
}

const DESCRIPTION = "We make heavy duty anvils for professional blacksmiths and film studios.";

// --- harness ---------------------------------------------------------------

type Options = {
  role?: string;
  ai?: boolean;
  config?: PersonalizationConfig | null;
  status?: string;
  campaignType?: string;
  sequence?: unknown;
  approval?: PersonalizationState["approval"];
  enabled?: boolean;
  // Replaces the capabilities answer, to model it arriving late or failing.
  capabilities?: () => Promise<unknown>;
};

function setup(options: Options = {}) {
  const {
    role = "MANAGER",
    ai = false,
    config = CONFIG,
    status = "DRAFT",
    campaignType = "HYPER_PERSONALIZED",
    sequence = threeEmails(),
    approval = { status: "NONE", approved_at: null, approved_by: null },
    enabled = true,
  } = options;
  useWorkspace.mockReturnValue({ activeWorkspaceId: "ws-1", activeWorkspace: { role_code: role } });
  campaignsApi.getCampaign.mockResolvedValue({
    id: "camp-1",
    name: "Founders",
    status,
    campaign_type: campaignType,
    version: 1,
  });
  campaignsApi.getSequence.mockResolvedValue(sequence);
  if (options.capabilities) {
    api.getPersonalizationCapabilities.mockImplementation(options.capabilities);
  } else {
    api.getPersonalizationCapabilities.mockResolvedValue({
      enabled,
      model: "m",
      ai_drafting_available: ai,
    });
  }
  api.getPersonalization.mockResolvedValue(
    stateWith({ config, config_version: config ? 3 : null, approval }),
  );
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const invalidate = vi.spyOn(client, "invalidateQueries");
  render(
    <QueryClientProvider client={client}>
      <CampaignSequencePage />
    </QueryClientProvider>,
  );
  return { user: userEvent.setup(), client, invalidate };
}

beforeEach(() => {
  campaignsApi.listCampaignMailboxes.mockResolvedValue([]);
  campaignsApi.listCampaignSettings.mockResolvedValue([]);
  api.getLatestPersonalizationPreviews.mockResolvedValue(null);
  api.getGenerationProgress.mockResolvedValue(null);
  api.previewEmailLayout.mockResolvedValue({ html: "<p>preview</p>", warnings: [] });
});
afterEach(() => vi.clearAllMocks());

const setupPanel = () => screen.findByRole("region", { name: "Campaign setup" });

// ---------------------------------------------------------------------------

describe("Sequence tab: hyper-personalized campaigns", () => {
  describe("what each campaign type gets", () => {
    it("leaves standard campaigns alone: no setup panel and no personalization requests", async () => {
      setup({ campaignType: "STANDARD" });
      expect(await screen.findByLabelText("Step 1: Email")).toBeInTheDocument();
      expect(screen.queryByRole("region", { name: "Campaign setup" })).not.toBeInTheDocument();
      expect(screen.queryByRole("region", { name: "Write emails with AI" })).not.toBeInTheDocument();
      expect(screen.queryByRole("region", { name: "Preview and approve" })).not.toBeInTheDocument();
      expect(api.getPersonalization).not.toHaveBeenCalled();
      expect(api.getPersonalizationCapabilities).not.toHaveBeenCalled();
    });

    it("explains when the feature is not enabled for this deployment", async () => {
      setup({ enabled: false });
      expect(await screen.findByText(/not enabled for this deployment/i)).toBeInTheDocument();
    });

    it("waits for the AI answer before choosing the AI or manual setup", async () => {
      // personalization state arrives first; capabilities arrive later
      let answer: (value: unknown) => void = () => undefined;
      setup({
        config: null,
        capabilities: () => new Promise((resolve) => (answer = resolve)),
      });
      await screen.findByLabelText("Step 1: Email");
      expect(screen.queryByRole("region", { name: "Campaign setup" })).not.toBeInTheDocument();
      expect(screen.queryByText(/AI drafting isn.t available/i)).not.toBeInTheDocument();

      answer({ enabled: true, model: "m", ai_drafting_available: true });
      // Opens on the company step, not stuck on the objective.
      expect(await screen.findByLabelText("Website")).toBeEnabled();
    });

    it("does not claim AI is unavailable when the check itself failed, and recovers on retry", async () => {
      let calls = 0;
      const { user } = setup({
        config: null,
        capabilities: async () => {
          calls += 1;
          if (calls === 1) throw new ApiError("The service is busy.", 503, "service_unavailable", null);
          return { enabled: true, model: "m", ai_drafting_available: true };
        },
      });
      expect(await screen.findByText(/couldn.t check whether AI writing is available/i)).toBeInTheDocument();
      expect(screen.queryByText(/AI drafting isn.t available/i)).not.toBeInTheDocument();
      expect(screen.queryByRole("region", { name: "Campaign setup" })).not.toBeInTheDocument();

      await user.click(screen.getByRole("button", { name: "Try again" }));
      expect(await screen.findByLabelText("Website")).toBeEnabled();
    });

    it("shows a saved setup collapsed, with an Edit action", async () => {
      setup();
      const panel = await setupPanel();
      expect(within(panel).getByText("Book demos")).toBeInTheDocument();
      expect(within(panel).getByText("Not added")).toBeInTheDocument(); // no company yet
      expect(within(panel).getByRole("button", { name: /edit setup/i })).toBeInTheDocument();
    });
  });

  describe("without AI (manual objective, exactly as before)", () => {
    it("tells the user AI drafting is unavailable and hides every AI control", async () => {
      setup({ ai: false, config: null });
      expect(await screen.findByText(/AI drafting isn.t available/i)).toBeInTheDocument();
      expect(screen.queryByRole("region", { name: "Write emails with AI" })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /regenerate/i })).not.toBeInTheDocument();
      expect(screen.queryByLabelText("Website")).not.toBeInTheDocument();
    });

    it("saves the objective with the version guard and refreshes preflight", async () => {
      api.savePersonalization.mockResolvedValue(stateWith());
      const { user, invalidate } = setup({ ai: false, config: null });
      await user.type(await screen.findByLabelText(/^Objective/), "Book demos");
      await user.type(screen.getByLabelText(/offering/i), "Outbound automation");
      await user.type(screen.getByLabelText(/call to action/i), "Open to a chat?");
      await user.click(screen.getByRole("button", { name: /save objective/i }));
      await waitFor(() => expect(api.savePersonalization).toHaveBeenCalled());
      const [ws, campaignId, payload] = api.savePersonalization.mock.calls[0];
      expect([ws, campaignId]).toEqual(["ws-1", "camp-1"]);
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

    it("preserves the company, style and brand already saved when only the objective changes", async () => {
      const saved = { ...CONFIG, email_format: "HTML" as const, brand: BRAND, company: analysis().profile };
      api.savePersonalization.mockResolvedValue(stateWith({ config: saved }));
      const { user } = setup({ ai: false, config: saved });
      await user.click(await screen.findByRole("button", { name: /edit setup/i }));
      await user.type(await screen.findByLabelText(/^Tone/), "Warm");
      await user.click(screen.getByRole("button", { name: /save objective/i }));
      await waitFor(() => expect(api.savePersonalization).toHaveBeenCalled());
      const config = api.savePersonalization.mock.calls[0][2].config;
      expect(config).toMatchObject({ tone: "Warm", email_format: "HTML" });
      expect(config.brand.primary).toBe("#123456");
      expect(config.company.company_name).toBe("Acme");
    });

    it("shows a save conflict and reloads the latest objective", async () => {
      api.savePersonalization.mockRejectedValue(
        new ApiError("The objective was modified by another user.", 409, "conflict", null),
      );
      const { user } = setup({ ai: false });
      await user.click(await screen.findByRole("button", { name: /edit setup/i }));
      await user.type(await screen.findByLabelText(/^Tone/), "Warm");
      await user.click(screen.getByRole("button", { name: /save objective/i }));
      expect(await screen.findByText(/modified by another user/i)).toBeInTheDocument();
      await waitFor(() => expect(api.getPersonalization.mock.calls.length).toBeGreaterThan(1));
      // still on the form, so the user's text is not lost
      expect(screen.getByLabelText(/^Tone/)).toHaveValue("Warm");
    });
  });

  describe("preview and approval (inline)", () => {
    it("offers samples, then shows them, using a fresh batch id", async () => {
      api.createPersonalizationPreviews.mockImplementation(async () => {
        api.getLatestPersonalizationPreviews.mockResolvedValue(goodBatch());
        return goodBatch();
      });
      campaignsApi.listSequencePreviewRecipients.mockResolvedValue({
        source: "AUDIENCE",
        items: [
          { audience_member_id: "m1", lead_id: "l1", email: "sarah@acme.test", first_name: "Sarah", last_name: null, company: "Acme", variables: {} },
          { audience_member_id: "m2", lead_id: "l2", email: "james@nova.test", first_name: "James", last_name: null, company: "Nova", variables: {} },
        ],
        total: 2,
        next_cursor: null,
      });
      const { user } = setup();
      expect(await screen.findByText("Not approved yet")).toBeInTheDocument();
      await user.click(await screen.findByRole("button", { name: "Generate samples" }));
      // Nothing is generated until the user picks exactly one lead.
      const confirm = await screen.findByRole("button", { name: "Generate sample" });
      expect(confirm).toBeDisabled();
      expect(api.createPersonalizationPreviews).not.toHaveBeenCalled();
      await user.click(await screen.findByRole("radio", { name: /James/ }));
      await user.click(confirm);
      await waitFor(() => expect(api.createPersonalizationPreviews).toHaveBeenCalled());
      expect(api.createPersonalizationPreviews.mock.calls[0][2]).toEqual({
        batch_id: "batch-new",
        audience_member_ids: ["m2"],
      });
      expect(await screen.findByText("Quick idea for Acme")).toBeInTheDocument();
    });

    it("does not offer samples until an objective is saved", async () => {
      setup({ config: null });
      const button = await screen.findByRole("button", { name: "Generate samples" });
      expect(button).toBeDisabled();
      expect(screen.getByText(/Save the campaign objective first/)).toBeInTheDocument();
    });

    it("lets a manager approve the current batch, bound to its digest", async () => {
      api.getLatestPersonalizationPreviews.mockResolvedValue(goodBatch());
      const approved = stateWith({
        approval: { status: "APPROVED", approved_at: "2026-03-02T10:05:00Z", approved_by: "u1" },
      });
      api.approvePersonalization.mockImplementation(async () => {
        api.getPersonalization.mockResolvedValue(approved);
        return approved;
      });
      const { user } = setup({ role: "MANAGER" });
      await user.click(await screen.findByRole("button", { name: "Approve samples" }));
      await waitFor(() => expect(api.approvePersonalization).toHaveBeenCalled());
      expect(api.approvePersonalization.mock.calls[0][2]).toEqual({ batch_id: "b1", config_digest: D });
      expect(await screen.findByText("Approved")).toBeInTheDocument();
    });

    it("shows why an approval was refused", async () => {
      api.getLatestPersonalizationPreviews.mockResolvedValue(goodBatch());
      api.approvePersonalization.mockRejectedValue(
        new ApiError("The objective or emails changed.", 409, "approval_stale", null),
      );
      const { user } = setup({ role: "MANAGER" });
      await user.click(await screen.findByRole("button", { name: "Approve samples" }));
      expect(await screen.findByText(/objective or emails changed\./i)).toBeInTheDocument();
    });

    it("tells the user to regenerate when the setup or emails changed since approval", async () => {
      setup({ approval: { status: "STALE", approved_at: null, approved_by: null } });
      expect(
        await screen.findByText(/setup or the emails changed\. Generate new samples/i),
      ).toBeInTheDocument();
    });

    it("lets a member generate but not approve", async () => {
      api.getLatestPersonalizationPreviews.mockResolvedValue(goodBatch());
      setup({ role: "MEMBER" });
      expect(await screen.findByRole("button", { name: "Generate new samples" })).toBeEnabled();
      expect(screen.queryByRole("button", { name: "Approve samples" })).not.toBeInTheDocument();
      expect(screen.getByText(/Only Managers and above can approve/)).toBeInTheDocument();
    });

    it("is read-only for a viewer", async () => {
      setup({ role: "VIEWER", ai: true });
      const panel = await setupPanel();
      expect(within(panel).queryByRole("button", { name: /edit setup/i })).not.toBeInTheDocument();
      expect(screen.queryByRole("region", { name: "Write emails with AI" })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /regenerate/i })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /^approve/i })).not.toBeInTheDocument();
    });

    it("locks everything and shows generation progress once activated", async () => {
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
      setup({ status: "RUNNING", ai: true });
      expect(await screen.findByTestId("failed")).toHaveTextContent("1");
      expect(screen.queryByRole("button", { name: "Generate samples" })).not.toBeInTheDocument();
      expect(screen.queryByRole("region", { name: "Write emails with AI" })).not.toBeInTheDocument();
      expect(screen.queryByRole("button", { name: /edit setup/i })).not.toBeInTheDocument();
    });
  });

  describe("company step (website or business info)", () => {
    it("starts on the company step for a campaign with no setup", async () => {
      setup({ ai: true, config: null });
      expect(await screen.findByLabelText("Website")).toBeEnabled();
      expect(screen.getByRole("button", { name: "Add Business info" })).toBeEnabled();
      expect(screen.getByRole("button", { name: "Continue" })).toBeDisabled();
    });

    it("keeps the website and business info mutually exclusive", async () => {
      const { user } = setup({ ai: true, config: null });
      const website = await screen.findByLabelText("Website");
      await user.type(website, "acme.com");
      expect(screen.getByRole("button", { name: "Add Business info" })).toBeDisabled();
      await user.clear(website);
      await user.click(screen.getByRole("button", { name: "Add Business info" }));
      expect(screen.getByLabelText("Website")).toBeDisabled();
      expect(screen.getByRole("group", { name: "Business information" })).toBeInTheDocument();
      await user.click(screen.getByRole("button", { name: "Remove business information" }));
      expect(screen.getByLabelText("Website")).toBeEnabled();
      expect(screen.queryByRole("group", { name: "Business information" })).not.toBeInTheDocument();
    });

    it.each([
      ["localhost", /public website address/i],
      ["10.0.0.1", /public website address/i],
      ["https://user:pw@acme.com", /username or password/i],
      ["my company", /can.t contain spaces/i],
    ])("explains why %s can't be used, and sends nothing", async (input, message) => {
      const { user } = setup({ ai: true, config: null });
      await user.type(await screen.findByLabelText("Website"), input);
      // Enter submits the form (Continue is enabled once something is typed).
      await user.keyboard("{Enter}");
      expect(await screen.findByText(message)).toBeInTheDocument();
      expect(api.analyzeCompany).not.toHaveBeenCalled();
    });

    it("validates business info: a name and a description of at least 40 characters", async () => {
      api.analyzeCompany.mockResolvedValue(analysis());
      const { user } = setup({ ai: true, config: null });
      await user.click(await screen.findByRole("button", { name: "Add Business info" }));
      expect(screen.getByRole("button", { name: "Continue" })).toBeDisabled(); // nothing typed yet
      await user.type(screen.getByLabelText(/^Company name/), "Acme");
      await user.type(screen.getByLabelText(/^Description/), "Too short");
      expect(screen.getByText("9 / 1500")).toBeInTheDocument();
      await user.click(screen.getByRole("button", { name: "Continue" }));
      expect(await screen.findByText(/at least 40 characters/i)).toBeInTheDocument();
      expect(api.analyzeCompany).not.toHaveBeenCalled();
      await user.type(screen.getByLabelText(/^Description/), " but now it is long enough to count.");
      await user.click(screen.getByRole("button", { name: "Continue" }));
      await waitFor(() => expect(api.analyzeCompany).toHaveBeenCalled());
    });

    it("analyzes a website, lets the user edit the review, then continues", async () => {
      api.analyzeCompany.mockResolvedValue(analysis());
      const { user } = setup({ ai: true, config: null });
      await user.type(await screen.findByLabelText("Website"), "Acme.example");
      await user.click(screen.getByRole("button", { name: "Continue" }));
      expect(await screen.findByDisplayValue("Acme")).toBeInTheDocument();
      expect(api.analyzeCompany).toHaveBeenCalledWith("ws-1", "camp-1", { url: "https://acme.example/" });
      expect(screen.getByText(/We read 2 pages of acme\.example/i)).toBeInTheDocument();
      expect(screen.getByDisplayValue("Anvils for blacksmiths.")).toBeInTheDocument();
      await user.clear(screen.getByDisplayValue("Acme"));
      expect(screen.getAllByRole("button", { name: "Continue" }).at(-1)).toBeDisabled();
    });

    it("analyzes typed business info without a website", async () => {
      api.analyzeCompany.mockResolvedValue(
        analysis({
          source: "MANUAL",
          final_url: null,
          brand: null,
          pages_read: 0,
          profile: { ...analysis().profile, source: "MANUAL", url: null, company_name: "Acme Anvils" },
        }),
      );
      const { user } = setup({ ai: true, config: null });
      await user.click(await screen.findByRole("button", { name: "Add Business info" }));
      await user.type(screen.getByLabelText(/^Company name/), "Acme Anvils");
      await user.type(screen.getByLabelText(/^Description/), DESCRIPTION);
      await user.click(screen.getByRole("button", { name: "Continue" }));
      expect(await screen.findByDisplayValue("Acme Anvils")).toBeInTheDocument();
      expect(api.analyzeCompany).toHaveBeenCalledWith("ws-1", "camp-1", {
        business: { company_name: "Acme Anvils", description: DESCRIPTION },
      });
      expect(screen.getByText(/what we understood from your description/i)).toBeInTheDocument();
    });

    it("recovers from a website that can't be read by offering business info, name prefilled", async () => {
      api.analyzeCompany.mockRejectedValue(
        new ApiError("We couldn't read enough from this website.", 422, "website_thin", null, {
          company_name: "Acme",
        }),
      );
      const { user } = setup({ ai: true, config: null });
      await user.type(await screen.findByLabelText("Website"), "acme.example");
      await user.click(screen.getByRole("button", { name: "Continue" }));
      expect(await screen.findByText(/couldn.t read enough/i)).toBeInTheDocument();
      await user.click(screen.getByRole("button", { name: "Enter business info instead" }));
      expect(screen.getByLabelText(/^Company name/)).toHaveValue("Acme");
      expect(screen.getByLabelText("Website")).toBeDisabled();
      expect(screen.queryByText(/couldn.t read enough/i)).not.toBeInTheDocument();
    });

    it.each(["website_unreachable", "website_blocked", "website_redirected_offsite"])(
      "offers business info after %s too",
      async (code) => {
        api.analyzeCompany.mockRejectedValue(new ApiError("Nope.", 422, code, null));
        const { user } = setup({ ai: true, config: null });
        await user.type(await screen.findByLabelText("Website"), "acme.example");
        await user.click(screen.getByRole("button", { name: "Continue" }));
        expect(await screen.findByRole("button", { name: "Enter business info instead" })).toBeInTheDocument();
      },
    );

    it("does not offer business info for an error that is not about the website", async () => {
      api.analyzeCompany.mockRejectedValue(new ApiError("Try later.", 503, "model_unavailable", null));
      const { user } = setup({ ai: true, config: null });
      await user.type(await screen.findByLabelText("Website"), "acme.example");
      await user.click(screen.getByRole("button", { name: "Continue" }));
      expect(await screen.findByText("Try later.")).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Enter business info instead" })).not.toBeInTheDocument();
    });

    it("retries, and after three failures suggests skipping", async () => {
      api.analyzeCompany
        .mockRejectedValueOnce(new ApiError("Try later.", 503, "model_unavailable", null))
        .mockRejectedValueOnce(new ApiError("Try later.", 503, "model_unavailable", null))
        .mockRejectedValueOnce(new ApiError("Try later.", 503, "model_unavailable", null))
        .mockResolvedValueOnce(analysis());
      const { user } = setup({ ai: true, config: null });
      await user.type(await screen.findByLabelText("Website"), "acme.example");
      await user.click(screen.getByRole("button", { name: "Continue" }));
      await screen.findByText("Try later.");
      expect(screen.queryByText(/Still not working/i)).not.toBeInTheDocument();
      await user.click(screen.getByRole("button", { name: "Try again" }));
      await waitFor(() => expect(api.analyzeCompany).toHaveBeenCalledTimes(2));
      await user.click(await screen.findByRole("button", { name: "Try again" }));
      expect(await screen.findByText(/Still not working\?/i)).toBeInTheDocument();
      await user.click(screen.getByRole("button", { name: "Try again" }));
      expect(await screen.findByDisplayValue("Acme")).toBeInTheDocument();
    });

    it("ignores a late answer after the user cancels", async () => {
      let finish: (value: CompanyAnalysis) => void = () => undefined;
      api.analyzeCompany.mockReturnValue(new Promise<CompanyAnalysis>((resolve) => (finish = resolve)));
      const { user } = setup({ ai: true, config: null });
      await user.type(await screen.findByLabelText("Website"), "acme.example");
      await user.click(screen.getByRole("button", { name: "Continue" }));
      expect(await screen.findByText(/Studying your website/i)).toBeInTheDocument();
      await user.click(screen.getByRole("button", { name: "Cancel" }));
      expect(await screen.findByLabelText("Website")).toHaveValue("acme.example");
      finish(analysis());
      await new Promise((resolve) => setTimeout(resolve, 20));
      expect(screen.getByLabelText("Website")).toBeInTheDocument();
      expect(screen.queryByDisplayValue("Anvils for blacksmiths.")).not.toBeInTheDocument();
    });

    it("asks before discarding an analysis to start over", async () => {
      api.analyzeCompany.mockResolvedValue(analysis());
      const { user } = setup({ ai: true, config: null });
      await user.type(await screen.findByLabelText("Website"), "acme.example");
      await user.click(screen.getByRole("button", { name: "Continue" }));
      await screen.findByDisplayValue("Acme");
      await user.click(screen.getByRole("button", { name: /use a different website or business info/i }));
      await user.click(await screen.findByRole("button", { name: "Cancel" }));
      expect(screen.getByDisplayValue("Acme")).toBeInTheDocument(); // kept
      await user.click(screen.getByRole("button", { name: /use a different website or business info/i }));
      await user.click(await screen.findByRole("button", { name: "Start over" }));
      expect(await screen.findByLabelText("Website")).toBeInTheDocument();
    });

    it("lets the user skip the company step", async () => {
      const { user } = setup({ ai: true, config: null });
      await user.click(await screen.findByRole("button", { name: "Skip this step" }));
      expect(await screen.findByText("Choose your email style")).toBeInTheDocument();
    });
  });

  describe("email style and objective", () => {
    async function throughCompany(user: ReturnType<typeof userEvent.setup>) {
      await user.type(await screen.findByLabelText("Website"), "acme.example");
      await user.click(screen.getByRole("button", { name: "Continue" }));
      await screen.findByDisplayValue("Acme");
      await user.click(screen.getAllByRole("button", { name: "Continue" }).at(-1) as HTMLElement);
      await screen.findByText("Choose your email style");
    }

    it("requires a choice before continuing", async () => {
      api.analyzeCompany.mockResolvedValue(analysis());
      const { user } = setup({ ai: true, config: null });
      await throughCompany(user);
      expect(screen.getByRole("button", { name: "Continue" })).toBeDisabled();
      await user.click(screen.getByRole("radio", { name: /Text Email/ }));
      expect(screen.getByRole("button", { name: "Continue" })).toBeEnabled();
    });

    it("saves a Text email setup: company kept, no brand, objective prefilled from the analysis", async () => {
      api.analyzeCompany.mockResolvedValue(analysis());
      api.savePersonalization.mockImplementation(async (_w, _c, payload) => {
        api.getPersonalization.mockResolvedValue(stateWith({ config: payload.config }));
        return stateWith({ config: payload.config });
      });
      const { user } = setup({ ai: true, config: null });
      await throughCompany(user);
      await user.click(screen.getByRole("radio", { name: /Text Email/ }));
      expect(screen.queryByRole("group", { name: "Brand settings" })).not.toBeInTheDocument();
      await user.click(screen.getByRole("button", { name: "Continue" }));

      expect(await screen.findByDisplayValue("Book a demo with blacksmiths")).toBeInTheDocument();
      expect(screen.getByDisplayValue("Heavy duty anvils")).toBeInTheDocument();
      expect(screen.getByDisplayValue("Reply with a time")).toBeInTheDocument();
      expect(screen.getByDisplayValue("Blacksmiths")).toBeInTheDocument(); // target from the audience
      await user.click(screen.getByRole("button", { name: "Save setup" }));

      await waitFor(() => expect(api.savePersonalization).toHaveBeenCalled());
      const { config, expected_version } = api.savePersonalization.mock.calls[0][2];
      expect(expected_version).toBeNull();
      expect(config).toMatchObject({
        objective: "Book a demo with blacksmiths",
        email_format: "TEXT",
        brand: null,
      });
      expect(config.company).toMatchObject({ source: "WEBSITE", company_name: "Acme" });
      // collapsed after a successful save
      expect(await screen.findByRole("button", { name: /edit setup/i })).toBeInTheDocument();
    });

    it("saves an HTML email setup with the detected brand and a live preview", async () => {
      api.analyzeCompany.mockResolvedValue(analysis());
      api.savePersonalization.mockResolvedValue(stateWith());
      const { user } = setup({ ai: true, config: null });
      await throughCompany(user);
      await user.click(screen.getByRole("radio", { name: /HTML Email/ }));
      expect(await screen.findByDisplayValue("https://acme.example/logo.png")).toBeInTheDocument();
      expect(screen.getByLabelText("Main colour")).toHaveValue("#123456");
      expect(await screen.findByTitle("Email design preview")).toBeInTheDocument();
      expect(api.previewEmailLayout).toHaveBeenCalled();
      expect(api.previewEmailLayout.mock.calls[0][2]).toMatchObject({
        brand: { primary: "#123456" },
        company_name: "Acme",
        site_url: "https://acme.example/",
      });
      await user.click(screen.getByRole("button", { name: "Continue" }));
      await user.click(await screen.findByRole("button", { name: "Save setup" }));
      await waitFor(() => expect(api.savePersonalization).toHaveBeenCalled());
      const config = api.savePersonalization.mock.calls[0][2].config;
      expect(config.email_format).toBe("HTML");
      expect(config.brand).toMatchObject({ primary: "#123456", logo_url: "https://acme.example/logo.png" });
    });

    it("explains that a brand can't be detected without a website, and defaults sensibly", async () => {
      api.analyzeCompany.mockResolvedValue(
        analysis({ source: "MANUAL", final_url: null, brand: null, pages_read: 0, profile: { ...analysis().profile, source: "MANUAL", url: null } }),
      );
      const { user } = setup({ ai: true, config: null });
      await user.click(await screen.findByRole("button", { name: "Add Business info" }));
      await user.type(screen.getByLabelText(/^Company name/), "Acme Anvils");
      await user.type(screen.getByLabelText(/^Description/), DESCRIPTION);
      await user.click(screen.getByRole("button", { name: "Continue" }));
      await screen.findByDisplayValue("Acme");
      await user.click(screen.getAllByRole("button", { name: "Continue" }).at(-1) as HTMLElement);
      await user.click(await screen.findByRole("radio", { name: /HTML Email/ }));
      expect(screen.getByText(/no website was provided/i)).toBeInTheDocument();
      expect(screen.getByLabelText("Main colour")).toHaveValue("#1f2937"); // neutral default
      expect(screen.getByRole("button", { name: "Continue" })).toBeEnabled();
    });

    it("blocks continuing with an invalid colour or web address, and skips the preview", async () => {
      api.analyzeCompany.mockResolvedValue(analysis());
      const { user } = setup({ ai: true, config: null });
      await throughCompany(user);
      await user.click(screen.getByRole("radio", { name: /HTML Email/ }));
      await screen.findByTitle("Email design preview");
      api.previewEmailLayout.mockClear();
      const primary = screen.getByLabelText("Main colour");
      await user.clear(primary);
      await user.type(primary, "blue");
      expect(screen.getByRole("button", { name: "Continue" })).toBeDisabled();
      expect(screen.getAllByText(/Use a colour like/i).length).toBeGreaterThan(0);
      await user.clear(primary);
      await user.type(primary, "#123456");
      const logo = screen.getByLabelText("Logo web address");
      await user.clear(logo);
      await user.type(logo, "http://insecure.example/logo.png");
      expect(screen.getByText(/starts with https:\/\//i)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Continue" })).toBeDisabled();
    });

    it("shows analysis warnings about the logo and colours in the brand card", async () => {
      api.analyzeCompany.mockResolvedValue(
        analysis({ warnings: ["logo_svg_only", "colors_defaulted"], brand: { ...BRAND, logo_url: null } }),
      );
      const { user } = setup({ ai: true, config: null });
      await throughCompany(user);
      await user.click(screen.getByRole("radio", { name: /HTML Email/ }));
      expect(screen.getByText(/logo is an SVG/i)).toBeInTheDocument();
      expect(screen.getByText(/couldn.t detect your brand colours/i)).toBeInTheDocument();
    });

    it("keeps the setup on screen with the server's message when saving fails", async () => {
      api.analyzeCompany.mockResolvedValue(analysis());
      api.savePersonalization.mockRejectedValue(new ApiError("Try again in a moment.", 503, null, null));
      const { user } = setup({ ai: true, config: null });
      await throughCompany(user);
      await user.click(screen.getByRole("radio", { name: /Text Email/ }));
      await user.click(screen.getByRole("button", { name: "Continue" }));
      await user.click(await screen.findByRole("button", { name: "Save setup" }));
      expect(await screen.findByText("Try again in a moment.")).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Save setup" })).toBeInTheDocument(); // not collapsed
    });
  });

  describe("writing the emails with AI", () => {
    const draftResult = (sequence: unknown, warnings: string[] = []) => ({
      sequence,
      model: "m",
      theme: "t",
      attempts: 1,
      warnings,
    });

    it("hides the AI controls when AI is unavailable", async () => {
      setup({ ai: false, sequence: emptySequence });
      await screen.findByText(/no steps yet/i);
      expect(screen.queryByRole("region", { name: "Write emails with AI" })).not.toBeInTheDocument();
    });

    it("asks for a saved setup first", async () => {
      setup({ ai: true, config: null, sequence: emptySequence });
      expect(await screen.findByText(/Save your setup first, then we can write your emails/i)).toBeInTheDocument();
      expect(screen.queryByRole("button", { name: "Generate emails" })).not.toBeInTheDocument();
    });

    it("asks how many follow-ups when there are no emails, then creates them", async () => {
      let current: unknown = emptySequence;
      campaignsApi.getSequence.mockImplementation(async () => current);
      api.generateReferenceTemplates.mockImplementation(async () => {
        current = threeEmails();
        return draftResult(current);
      });
      const { user } = setup({ ai: true, sequence: emptySequence });
      // setup() installs a fixed answer; make it follow the "server" instead.
      campaignsApi.getSequence.mockImplementation(async () => current);
      const select = await screen.findByLabelText("How many follow-ups?");
      expect(select).toHaveValue("2");
      await user.selectOptions(select, "4");
      await user.click(screen.getByRole("button", { name: "Generate emails" }));
      await waitFor(() =>
        expect(api.generateReferenceTemplates).toHaveBeenCalledWith("ws-1", "camp-1", {
          scope: "ALL",
          follow_up_count: 4,
        }),
      );
      expect(await screen.findByText(/Your emails are ready/i)).toBeInTheDocument();
      expect(await screen.findByLabelText("Step 3: Email")).toBeInTheDocument();
    });

    it("confirms before replacing existing emails, and only then writes", async () => {
      api.generateReferenceTemplates.mockResolvedValue(draftResult(threeEmails()));
      const { user } = setup({ ai: true });
      await user.click(await screen.findByRole("button", { name: "Regenerate all emails" }));
      expect(await screen.findByText(/replaces the content of all 3 emails/i)).toBeInTheDocument();
      await user.click(screen.getByRole("button", { name: "Cancel" }));
      expect(api.generateReferenceTemplates).not.toHaveBeenCalled();
      await user.click(screen.getByRole("button", { name: "Regenerate all emails" }));
      await user.click(await screen.findByRole("button", { name: "Replace emails" }));
      await waitFor(() =>
        expect(api.generateReferenceTemplates).toHaveBeenCalledWith("ws-1", "camp-1", { scope: "ALL" }),
      );
    });

    it("regenerates a single email after confirmation", async () => {
      api.generateReferenceTemplates.mockResolvedValue(draftResult(threeEmails()));
      const { user } = setup({ ai: true });
      await user.click(await screen.findByRole("button", { name: "Regenerate step 2" }));
      expect(await screen.findByText(/Regenerate email 2\?/)).toBeInTheDocument();
      await user.click(screen.getByRole("button", { name: "Regenerate" }));
      await waitFor(() =>
        expect(api.generateReferenceTemplates).toHaveBeenCalledWith("ws-1", "camp-1", {
          scope: "STEP",
          step_id: "e2",
        }),
      );
    });

    it("sends the optional change request with a single-email regeneration", async () => {
      api.generateReferenceTemplates.mockResolvedValue(draftResult(threeEmails()));
      const { user } = setup({ ai: true });
      await user.click(await screen.findByRole("button", { name: "Regenerate step 2" }));
      await user.type(
        await screen.findByLabelText(/What should change/i),
        "  make it shorter and friendlier  ",
      );
      await user.click(screen.getByRole("button", { name: "Regenerate" }));
      await waitFor(() =>
        expect(api.generateReferenceTemplates).toHaveBeenCalledWith("ws-1", "camp-1", {
          scope: "STEP",
          step_id: "e2",
          instructions: "make it shorter and friendlier",
        }),
      );
      // The note does not carry over to the next email.
      await screen.findByText(/Your emails are ready/i);
      await user.click(await screen.findByRole("button", { name: "Regenerate step 1" }));
      expect(await screen.findByLabelText(/What should change/i)).toHaveValue("");
    });

    it("discards the change request when the dialog is cancelled", async () => {
      const { user } = setup({ ai: true });
      await user.click(await screen.findByRole("button", { name: "Regenerate step 2" }));
      await user.type(await screen.findByLabelText(/What should change/i), "shorter");
      await user.click(screen.getByRole("button", { name: "Cancel" }));
      await user.click(await screen.findByRole("button", { name: "Regenerate step 2" }));
      expect(await screen.findByLabelText(/What should change/i)).toHaveValue("");
      expect(api.generateReferenceTemplates).not.toHaveBeenCalled();
    });

    it("shows a spinner while writing and disables the other Regenerate buttons", async () => {
      let finish: (value: unknown) => void = () => undefined;
      api.generateReferenceTemplates.mockReturnValue(new Promise((resolve) => (finish = resolve)));
      const { user } = setup({ ai: true });
      await user.click(await screen.findByRole("button", { name: "Regenerate step 1" }));
      await user.click(await screen.findByRole("button", { name: "Regenerate" }));
      expect(await screen.findByText(/Writing your emails/i)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Regenerate step 2" })).toBeDisabled();
      finish(draftResult(threeEmails()));
      expect(await screen.findByText(/Your emails are ready/i)).toBeInTheDocument();
      expect(screen.getByRole("button", { name: "Regenerate step 2" })).toBeEnabled();
    });

    it("shows a failed check in plain language with Retry, and suggests writing by hand after three", async () => {
      api.generateReferenceTemplates.mockRejectedValue(
        new ApiError("The AI couldn't produce emails that meet the checks.", 422, "reference_generation_rejected", null, {
          codes: ["missing_cta"],
        }),
      );
      const { user } = setup({ ai: true });
      await user.click(await screen.findByRole("button", { name: "Regenerate all emails" }));
      await user.click(await screen.findByRole("button", { name: "Replace emails" }));
      expect(await screen.findByText(/couldn.t produce emails that meet the checks/i)).toBeInTheDocument();
      expect(screen.getByText("An email left out your call to action.")).toBeInTheDocument();
      expect(screen.queryByText(/write the emails yourself/i)).not.toBeInTheDocument();
      await user.click(screen.getByRole("button", { name: "Try again" }));
      await waitFor(() => expect(api.generateReferenceTemplates).toHaveBeenCalledTimes(2));
      await user.click(await screen.findByRole("button", { name: "Try again" }));
      expect(await screen.findByText(/write the emails yourself/i)).toBeInTheDocument();
      // Retry repeats the same request rather than asking again
      expect(api.generateReferenceTemplates.mock.calls.map((c) => c[2])).toEqual([
        { scope: "ALL" },
        { scope: "ALL" },
        { scope: "ALL" },
      ]);
    });

    it("reloads the emails when they changed while the AI was writing", async () => {
      api.generateReferenceTemplates.mockRejectedValue(
        new ApiError("The campaign changed while the emails were being written.", 409, "conflict", null),
      );
      const { user } = setup({ ai: true });
      await user.click(await screen.findByRole("button", { name: "Regenerate all emails" }));
      await user.click(await screen.findByRole("button", { name: "Replace emails" }));
      expect(await screen.findByText(/changed while the emails were being written/i)).toBeInTheDocument();
      await waitFor(() => expect(campaignsApi.getSequence.mock.calls.length).toBeGreaterThan(1));
    });

    it("warns that follow-ups need to be switched on when the server says so", async () => {
      api.generateReferenceTemplates.mockResolvedValue(draftResult(threeEmails(), ["followups_require_progression"]));
      const { user } = setup({ ai: true });
      await user.click(await screen.findByRole("button", { name: "Regenerate all emails" }));
      await user.click(await screen.findByRole("button", { name: "Replace emails" }));
      expect(await screen.findByText(/Follow-up emails are switched off/i)).toBeInTheDocument();
    });

    it("refreshes the sequence, approval and readiness after writing", async () => {
      api.generateReferenceTemplates.mockResolvedValue(draftResult(threeEmails()));
      const { user, invalidate } = setup({ ai: true });
      await user.click(await screen.findByRole("button", { name: "Regenerate all emails" }));
      await user.click(await screen.findByRole("button", { name: "Replace emails" }));
      await screen.findByText(/Your emails are ready/i);
      const keys = invalidate.mock.calls.map((c) => JSON.stringify((c[0] as { queryKey: unknown[] }).queryKey));
      for (const part of ["sequence", "personalization", "preflight", "review"]) {
        expect(keys.some((k) => k.includes(`"${part}"`))).toBe(true);
      }
    });
  });

  describe("designed (HTML) emails", () => {
    it("opens a designed email in source-only mode", async () => {
      const { user } = setup({ config: { ...CONFIG, email_format: "HTML", brand: BRAND } });
      const card = await screen.findByLabelText("Step 1: Email");
      await user.click(within(card).getByRole("button", { name: "Edit" }));
      expect(await screen.findByText(/editing e1 \(designed email\)/)).toBeInTheDocument();
    });

    it("opens a text-style email normally", async () => {
      const { user } = setup({ config: { ...CONFIG, email_format: "TEXT" } });
      const card = await screen.findByLabelText("Step 1: Email");
      await user.click(within(card).getByRole("button", { name: "Edit" }));
      expect(await screen.findByText(/editing e1 \(plain email\)/)).toBeInTheDocument();
    });
  });
});
