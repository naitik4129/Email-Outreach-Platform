import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const campaignsApi = vi.hoisted(() => ({
  updateSequenceStep: vi.fn(),
  listSequencePreviewRecipients: vi.fn(),
  sendSequenceStepTestEmail: vi.fn(),
}));
vi.mock("@/lib/campaigns-api", () => campaignsApi);

const templatesApi = vi.hoisted(() => ({
  previewTemplate: vi.fn(),
  listTemplates: vi.fn(),
  getTemplate: vi.fn(),
  createTemplate: vi.fn(),
}));
vi.mock("@/lib/templates-api", () => templatesApi);

import { ApiError } from "@/lib/api-client";
import type { CampaignMailbox, SequenceStep } from "@/types/domain";

import { EmailStepDialog } from "./email-step-dialog";

const step: SequenceStep = {
  id: "e1",
  sequence_id: "seq-1",
  campaign_id: "camp-1",
  position: 1,
  kind: "EMAIL",
  email_subject: "Hello {{first_name|there}}",
  email_body_html: "<p>Hi {{first_name|there}}</p>",
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
    sender_display_name: "Sam Sales",
    connection_state: "CONNECTED",
    health_state: "HEALTHY",
    policy_state: "ENABLED",
    policy_reason: null,
    active: true,
    allocation_position: 1,
  },
  {
    mailbox_id: "mb-2",
    provider: "SMTP",
    email_address: "second@acme.test",
    sender_display_name: null,
    connection_state: "CONNECTED",
    health_state: "HEALTHY",
    policy_state: "ENABLED",
    policy_reason: null,
    active: true,
    allocation_position: 2,
  },
];

function prospect(n: number, name: string, company: string) {
  return {
    audience_member_id: `m${n}`,
    lead_id: `l${n}`,
    email: `${name.toLowerCase()}@example.com`,
    first_name: name,
    last_name: "Person",
    company,
    variables: { first_name: name, company, email: `${name.toLowerCase()}@example.com` },
  };
}

function setup(overrides: Partial<React.ComponentProps<typeof EmailStepDialog>> = {}) {
  const props: React.ComponentProps<typeof EmailStepDialog> = {
    open: true,
    workspaceId: "ws-1",
    campaignId: "camp-1",
    step,
    stepNumber: 1,
    day: 1,
    previousEmailDay: null,
    precedingWait: null,
    readOnly: false,
    canTestSend: true,
    mailboxes,
    onClose: vi.fn(),
    onSaved: vi.fn(),
    onRefetch: vi.fn().mockResolvedValue({ step, wait: null }),
    onLocked: vi.fn(),
    ...overrides,
  };
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <EmailStepDialog {...props} />
    </QueryClientProvider>,
  );
  return props;
}

beforeEach(() => {
  templatesApi.previewTemplate.mockImplementation(
    async (
      _ws: string,
      input: { subject: string; body_html: string; sample_data?: Record<string, unknown> | null },
    ) => {
      const name = (input.sample_data?.first_name as string | undefined) ?? "Alex";
      return {
        subject: input.subject.replace("{{first_name|there}}", name),
        body_html: input.body_html.replace("{{first_name|there}}", name),
        preheader: "",
        detected_variables: ["first_name"],
        missing_variables: [],
      };
    },
  );
  templatesApi.listTemplates.mockResolvedValue({ items: [], next_cursor: null });
  campaignsApi.listSequencePreviewRecipients.mockResolvedValue({
    source: "AUDIENCE",
    items: [prospect(1, "Ada", "Engine Co"), prospect(2, "Grace", "Navy"), prospect(3, "Linus", "Kernel")],
    total: 3,
    next_cursor: null,
  });
  vi.spyOn(window, "confirm").mockReturnValue(true);
});

afterEach(() => {
  vi.clearAllMocks();
  vi.restoreAllMocks();
});

describe("preview prospects", () => {
  it("previews the first real prospect and renders their data", async () => {
    setup();
    expect(await screen.findByText("Hello Ada")).toBeInTheDocument();
    expect(screen.getByLabelText("To")).toHaveDisplayValue("Ada Person <ada@example.com>");
    expect(screen.getByText(/Prospect 1 of 3/)).toBeInTheDocument();
    expect(templatesApi.previewTemplate).toHaveBeenCalledWith(
      "ws-1",
      expect.objectContaining({
        sample_data: expect.objectContaining({ first_name: "Ada", company: "Engine Co" }),
      }),
    );
  });

  it("navigates with next/previous and re-renders personalization", async () => {
    const user = userEvent.setup();
    setup();
    await screen.findByText("Hello Ada");

    await user.click(screen.getByRole("button", { name: /next prospect/i }));
    expect(await screen.findByText("Hello Grace")).toBeInTheDocument();
    expect(screen.getByText(/Prospect 2 of 3/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /next prospect/i }));
    expect(await screen.findByText("Hello Linus")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /next prospect/i })).toBeDisabled();

    await user.click(screen.getByRole("button", { name: /previous prospect/i }));
    expect(await screen.findByText("Hello Grace")).toBeInTheDocument();
  });

  it("jumps to a prospect chosen from the list", async () => {
    const user = userEvent.setup();
    setup();
    await screen.findByText("Hello Ada");
    await user.selectOptions(screen.getByLabelText("To"), "2");
    expect(await screen.findByText("Hello Linus")).toBeInTheDocument();
  });

  it("disables previous on the first prospect", async () => {
    setup();
    await screen.findByText("Hello Ada");
    expect(screen.getByRole("button", { name: /previous prospect/i })).toBeDisabled();
  });

  it("loads the next page of prospects as navigation nears the end", async () => {
    const user = userEvent.setup();
    campaignsApi.listSequencePreviewRecipients
      .mockResolvedValueOnce({
        source: "AUDIENCE",
        items: [prospect(1, "Ada", "A"), prospect(2, "Grace", "B")],
        total: 3,
        next_cursor: 1,
      })
      .mockResolvedValueOnce({
        source: "AUDIENCE",
        items: [prospect(3, "Linus", "C")],
        total: 3,
        next_cursor: null,
      });
    setup();
    await screen.findByText("Hello Ada");

    await waitFor(() =>
      expect(campaignsApi.listSequencePreviewRecipients).toHaveBeenCalledWith("ws-1", "camp-1", {
        limit: 25,
        afterOrdinal: 1,
      }),
    );
    await user.click(screen.getByRole("button", { name: /next prospect/i }));
    await user.click(screen.getByRole("button", { name: /next prospect/i }));
    expect(await screen.findByText("Hello Linus")).toBeInTheDocument();
  });

  it("falls back to labelled sample data when the campaign has no audience yet", async () => {
    campaignsApi.listSequencePreviewRecipients.mockResolvedValue({
      source: "NONE",
      items: [],
      total: 0,
      next_cursor: null,
    });
    setup();
    expect(await screen.findByText(/Sample data:/)).toBeInTheDocument();
    expect(screen.getByLabelText("To")).toHaveDisplayValue(
      "Alex Taylor <alex.taylor@acme.example.com>",
    );
    expect(screen.getByRole("button", { name: /next prospect/i })).toBeDisabled();
    // The preview is debounced, so wait for the call that uses sample data.
    await waitFor(() =>
      expect(templatesApi.previewTemplate).toHaveBeenCalledWith(
        "ws-1",
        expect.objectContaining({ sample_data: null }),
      ),
    );
  });

  it("keeps working with sample data when prospects can't be loaded", async () => {
    campaignsApi.listSequencePreviewRecipients.mockRejectedValue(
      new ApiError("Campaign not found", 404, "not_found", null),
    );
    setup();
    expect(await screen.findByText(/Couldn.t load prospects: Campaign not found/)).toBeInTheDocument();
    expect(screen.getByLabelText("To")).toHaveDisplayValue(
      "Alex Taylor <alex.taylor@acme.example.com>",
    );
  });

  it("lets the user choose which connected account the preview is from", async () => {
    const user = userEvent.setup();
    setup();
    await screen.findByText("Hello Ada");
    expect(screen.getByLabelText("From")).toHaveDisplayValue("Sam Sales <sales@acme.test>");
    await user.selectOptions(screen.getByLabelText("From"), "mb-2");
    expect(screen.getByLabelText("From")).toHaveDisplayValue("second@acme.test");
  });
});

describe("send test email", () => {
  async function openTestSend(user: ReturnType<typeof userEvent.setup>) {
    await screen.findByText("Hello Ada");
    await user.click(screen.getByRole("button", { name: /send test email/i }));
  }

  it("requires a valid address and an explicit confirmation", async () => {
    const user = userEvent.setup();
    setup();
    await openTestSend(user);

    await user.click(screen.getByRole("button", { name: /^send test$/i }));
    expect(await screen.findByText(/enter a valid email address/i)).toBeInTheDocument();

    await user.type(screen.getByLabelText(/^send to/i), "qa@example.com");
    await user.click(screen.getByRole("button", { name: /^send test$/i }));
    expect(await screen.findByText(/confirm that this address/i)).toBeInTheDocument();
    expect(campaignsApi.sendSequenceStepTestEmail).not.toHaveBeenCalled();
  });

  it("sends the on-screen content for the previewed prospect from the chosen mailbox", async () => {
    const user = userEvent.setup();
    campaignsApi.sendSequenceStepTestEmail.mockResolvedValue({
      message_id: "msg-1",
      status: "SENT",
      recipient_email: "qa@example.com",
    });
    setup();
    await screen.findByText("Hello Ada");
    await user.click(screen.getByRole("button", { name: /next prospect/i }));
    await screen.findByText("Hello Grace");

    // Edit the subject without saving: the test must use what is on screen.
    const subject = screen.getByLabelText("Subject", { selector: "input" });
    await user.clear(subject);
    await user.type(subject, "Unsaved subject");

    await user.click(screen.getByRole("button", { name: /send test email/i }));
    await user.type(screen.getByLabelText(/^send to/i), "qa@example.com");
    await user.click(screen.getByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: /^send test$/i }));

    await waitFor(() =>
      expect(campaignsApi.sendSequenceStepTestEmail).toHaveBeenCalledWith(
        "ws-1",
        "camp-1",
        "e1",
        {
          mailbox_id: "mb-1",
          recipient_email: "qa@example.com",
          confirm_recipient: true,
          audience_member_id: "m2",
          email_subject: "Unsaved subject",
          email_body_html: "<p>Hi {{first_name|there}}</p>",
          email_preheader: "",
        },
        expect.any(String),
      ),
    );
    expect(await screen.findByText(/test email sent to qa@example.com/i)).toBeInTheDocument();
    // Sending a test never saves the step.
    expect(campaignsApi.updateSequenceStep).not.toHaveBeenCalled();
  });

  it("does not send a prospect id when previewing sample data", async () => {
    const user = userEvent.setup();
    campaignsApi.listSequencePreviewRecipients.mockResolvedValue({
      source: "NONE",
      items: [],
      total: 0,
      next_cursor: null,
    });
    campaignsApi.sendSequenceStepTestEmail.mockResolvedValue({
      message_id: "m",
      status: "SENT",
      recipient_email: "qa@example.com",
    });
    setup();
    await screen.findByText(/Sample data:/);
    await user.click(screen.getByRole("button", { name: /send test email/i }));
    await user.type(screen.getByLabelText(/^send to/i), "qa@example.com");
    await user.click(screen.getByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: /^send test$/i }));

    await waitFor(() =>
      expect(campaignsApi.sendSequenceStepTestEmail).toHaveBeenCalledWith(
        "ws-1",
        "camp-1",
        "e1",
        expect.objectContaining({ audience_member_id: null }),
        expect.any(String),
      ),
    );
  });

  it("shows the server's reason when the test is refused", async () => {
    const user = userEvent.setup();
    campaignsApi.sendSequenceStepTestEmail.mockRejectedValue(
      new ApiError(
        "Recipient email address is suppressed and cannot receive email",
        400,
        "suppressed_recipient",
        null,
      ),
    );
    setup();
    await openTestSend(user);
    await user.type(screen.getByLabelText(/^send to/i), "blocked@example.com");
    await user.click(screen.getByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: /^send test$/i }));

    expect(await screen.findByText(/is suppressed and cannot receive email/i)).toBeInTheDocument();
  });

  it("reports a provider failure instead of claiming success", async () => {
    const user = userEvent.setup();
    campaignsApi.sendSequenceStepTestEmail.mockResolvedValue({
      message_id: "m",
      status: "FAILED",
      recipient_email: "qa@example.com",
      error_message: "Provider rejected message: AUTH",
    });
    setup();
    await openTestSend(user);
    await user.type(screen.getByLabelText(/^send to/i), "qa@example.com");
    await user.click(screen.getByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: /^send test$/i }));

    expect(await screen.findByText(/provider rejected message: auth/i)).toBeInTheDocument();
    expect(screen.queryByText(/test email sent/i)).not.toBeInTheDocument();
  });

  it("says when delivery could not be confirmed", async () => {
    const user = userEvent.setup();
    campaignsApi.sendSequenceStepTestEmail.mockResolvedValue({
      message_id: "m",
      status: "UNKNOWN_OUTCOME",
      recipient_email: "qa@example.com",
    });
    setup();
    await openTestSend(user);
    await user.type(screen.getByLabelText(/^send to/i), "qa@example.com");
    await user.click(screen.getByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: /^send test$/i }));

    expect(await screen.findByText(/couldn.t confirm delivery/i)).toBeInTheDocument();
  });

  it("reuses the idempotency key when a network failure makes the outcome unknown", async () => {
    const user = userEvent.setup();
    campaignsApi.sendSequenceStepTestEmail
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValueOnce({ message_id: "m", status: "SENT", recipient_email: "qa@example.com" });
    setup();
    await openTestSend(user);
    await user.type(screen.getByLabelText(/^send to/i), "qa@example.com");
    await user.click(screen.getByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: /^send test$/i }));

    expect(await screen.findByText(/can.t tell whether the test email was sent/i)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: /^send test$/i }));
    await waitFor(() => expect(campaignsApi.sendSequenceStepTestEmail).toHaveBeenCalledTimes(2));

    const firstKey = campaignsApi.sendSequenceStepTestEmail.mock.calls[0][4];
    const secondKey = campaignsApi.sendSequenceStepTestEmail.mock.calls[1][4];
    expect(secondKey).toBe(firstKey);
  });

  it("uses a fresh idempotency key after a definite result", async () => {
    const user = userEvent.setup();
    campaignsApi.sendSequenceStepTestEmail.mockResolvedValue({
      message_id: "m",
      status: "SENT",
      recipient_email: "qa@example.com",
    });
    setup();
    await openTestSend(user);
    await user.type(screen.getByLabelText(/^send to/i), "qa@example.com");
    await user.click(screen.getByRole("checkbox"));
    await user.click(screen.getByRole("button", { name: /^send test$/i }));
    await screen.findByText(/test email sent/i);
    await user.click(screen.getByRole("button", { name: /^send test$/i }));
    await waitFor(() => expect(campaignsApi.sendSequenceStepTestEmail).toHaveBeenCalledTimes(2));

    expect(campaignsApi.sendSequenceStepTestEmail.mock.calls[1][4]).not.toBe(
      campaignsApi.sendSequenceStepTestEmail.mock.calls[0][4],
    );
  });

  it("is disabled with an explanation when there is no sender", async () => {
    setup({ mailboxes: [] });
    const button = await screen.findByRole("button", { name: /send test email/i });
    expect(button).toBeDisabled();
    expect(button.parentElement).toHaveAttribute("title", expect.stringMatching(/assign a sender/i));
  });

  it("is disabled for roles that cannot execute campaigns", async () => {
    setup({ canTestSend: false });
    const button = await screen.findByRole("button", { name: /send test email/i });
    expect(button).toBeDisabled();
    expect(button.parentElement).toHaveAttribute("title", expect.stringMatching(/managers, admins and owners/i));
  });

  it("is disabled while the subject is empty", async () => {
    const user = userEvent.setup();
    setup();
    await screen.findByText("Hello Ada");
    await user.clear(screen.getByLabelText("Subject", { selector: "input" }));
    const button = screen.getByRole("button", { name: /send test email/i });
    expect(button).toBeDisabled();
  });
});
