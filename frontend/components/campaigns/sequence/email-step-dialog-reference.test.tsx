import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const campaignsApi = vi.hoisted(() => ({
  updateSequenceStep: vi.fn(),
  listSequencePreviewRecipients: vi.fn(),
  sendSequenceStepTestEmail: vi.fn(),
  uploadStepAttachment: vi.fn(),
  deleteStepAttachment: vi.fn(),
  getStepAttachmentUrl: vi.fn(),
}));
vi.mock("@/lib/campaigns-api", () => campaignsApi);

const templatesApi = vi.hoisted(() => ({
  previewTemplate: vi.fn(),
  listTemplates: vi.fn(),
  getTemplate: vi.fn(),
  createTemplate: vi.fn(),
}));
vi.mock("@/lib/templates-api", () => templatesApi);

import type { CampaignMailbox, SequenceStep } from "@/types/domain";

import { EmailStepDialog } from "./email-step-dialog";

const step: SequenceStep = {
  id: "e1",
  sequence_id: "seq-1",
  campaign_id: "camp-1",
  position: 1,
  kind: "EMAIL",
  email_subject: "Quick idea for {{company|your team}}",
  email_body_html: "<p>Hi {{first_name|there}},</p>",
  email_preheader: null,
  email_variable_schema: {},
  wait_duration_minutes: null,
  source_template_version_id: null,
  version: 1,
  created_at: "",
  updated_at: "",
};

const mailboxes: CampaignMailbox[] = [
  {
    mailbox_id: "mb-1",
    provider: "GMAIL",
    email_address: "sales@acme.test",
    sender_display_name: "Sam",
    connection_state: "CONNECTED",
    health_state: "HEALTHY",
    policy_state: "ENABLED",
    policy_reason: null,
    active: true,
    allocation_position: 1,
  },
];

function setup(referenceMode: boolean, designedEmail = false, body?: string) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <EmailStepDialog
        open
        workspaceId="ws-1"
        campaignId="camp-1"
        step={body ? { ...step, email_body_html: body } : step}
        stepNumber={1}
        day={1}
        previousEmailDay={null}
        precedingWait={null}
        readOnly={false}
        canTestSend
        referenceMode={referenceMode}
        designedEmail={designedEmail}
        mailboxes={mailboxes}
        onClose={vi.fn()}
        onSaved={vi.fn()}
        onRefetch={vi.fn()}
        onLocked={vi.fn()}
      />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  campaignsApi.listSequencePreviewRecipients.mockResolvedValue({
    source: "NONE",
    items: [],
    total: 0,
    next_cursor: null,
  });
  templatesApi.previewTemplate.mockResolvedValue({
    subject: "Quick idea for your team",
    body_html: "<p>Hi there,</p>",
    preheader: "",
    detected_variables: [],
    missing_variables: [],
  });
  templatesApi.listTemplates.mockResolvedValue({ items: [], next_cursor: null });
});

afterEach(() => vi.clearAllMocks());

describe("EmailStepDialog reference mode", () => {
  it("labels the step as a reference email and explains what it is", async () => {
    setup(true);
    expect(await screen.findByText("Reference email")).toBeInTheDocument();
    expect(
      screen.getByText(/Each lead receives a version personalized to them/i),
    ).toBeInTheDocument();
    // The Personalization tab no longer exists; samples are on the Sequence tab.
    expect(
      screen.getByText(/Preview and approve section of the Sequence tab/i),
    ).toBeInTheDocument();
    expect(screen.queryByText(/Personalization tab/i)).not.toBeInTheDocument();
  });

  it("does not offer test sends, because the reference is not what leads receive", async () => {
    setup(true);
    await screen.findByText("Reference email");
    expect(screen.queryByRole("button", { name: /send test email/i })).not.toBeInTheDocument();
    expect(screen.getByText(/Test sends are not available for a reference email/i)).toBeInTheDocument();
  });

  it("blocks inline images but still allows attachments", async () => {
    setup(true);
    await screen.findByText("Reference email");
    expect(screen.getByRole("button", { name: "Insert an image" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Insert an image" })).toHaveAttribute(
      "title",
      expect.stringMatching(/aren't supported in personalized emails/i),
    );
    expect(screen.getByRole("button", { name: "Attach a file" })).toBeEnabled();
  });

  it("is unchanged for standard campaigns", async () => {
    setup(false);
    expect(await screen.findByText("Email")).toBeInTheDocument();
    expect(screen.queryByText("Reference email")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: /send test email/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Insert an image" })).toBeEnabled();
  });

  describe("designed (HTML) emails", () => {
    const designed =
      '<table role="presentation" width="600"><tr><td style="padding: 32px">' +
      "<p>Hi {{first_name|there}},</p></td></tr></table>";

    it("opens as source with an explanation, never the visual editor", async () => {
      setup(true, true, designed);
      await screen.findByText("Reference email");
      expect(screen.getByText(/designed email in your brand.s style/i)).toBeInTheDocument();
      expect(screen.getByLabelText("HTML source")).toHaveValue(designed);
    });

    it("stays in source mode when the user tries to switch, so the layout is not flattened", async () => {
      const user = userEvent.setup();
      setup(true, true, designed);
      await screen.findByText("Reference email");
      await user.click(screen.getByRole("button", { name: /source|html|visual/i }));
      expect(screen.getByLabelText("HTML source")).toHaveValue(designed);
      expect(screen.queryByText(/was simplified/i)).not.toBeInTheDocument();
    });

    it("opens a plain email in the visual editor as before", async () => {
      setup(true, false);
      await screen.findByText("Reference email");
      expect(screen.queryByLabelText("HTML source")).not.toBeInTheDocument();
      expect(screen.queryByText(/designed email in your brand/i)).not.toBeInTheDocument();
    });

    it("still tells a non-designed table email that it opened as source, without the designed copy", async () => {
      setup(true, false, designed);
      await screen.findByText("Reference email");
      expect(screen.getByText(/uses HTML the visual editor can.t reproduce/i)).toBeInTheDocument();
      expect(screen.queryByText(/designed email in your brand/i)).not.toBeInTheDocument();
    });
  });
});
