import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
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

function setup(referenceMode: boolean) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <EmailStepDialog
        open
        workspaceId="ws-1"
        campaignId="camp-1"
        step={step}
        stepNumber={1}
        day={1}
        previousEmailDay={null}
        precedingWait={null}
        readOnly={false}
        canTestSend
        referenceMode={referenceMode}
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
    expect(screen.getByText(/Review generated samples on the Personalization tab/i)).toBeInTheDocument();
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
});
