import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const { getLeadActivity } = vi.hoisted(() => ({ getLeadActivity: vi.fn() }));
vi.mock("@/lib/leads-api", () => ({ getLeadActivity }));

import { LeadActivityTimeline } from "./lead-activity";

function renderTimeline() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <LeadActivityTimeline workspaceId="ws-1" leadId="lead-1" />
    </QueryClientProvider>,
  );
}

const base = {
  campaign_id: "camp-1",
  campaign_name: "Fintech Outreach",
  conversation_id: null,
  sender_email: null,
  body_preview: null,
  occurrence_count: null,
  bounce_type: null,
};

describe("LeadActivityTimeline", () => {
  afterEach(() => vi.clearAllMocks());

  it("shows sent, opened, bounced and reply events with campaign, step and reply details", async () => {
    getLeadActivity.mockResolvedValue({
      lead_id: "lead-1",
      items: [
        {
          ...base,
          kind: "REPLY_RECEIVED",
          occurred_at: "2026-09-26T10:00:00Z",
          message_id: "in-1",
          subject: "Re: Quick idea",
          sequence_step_position: 2,
          conversation_id: "conv-1",
          sender_email: "jane@target.test",
          body_preview: "Sounds interesting, let's talk.",
        },
        {
          ...base,
          kind: "EMAIL_OPENED",
          occurred_at: "2026-09-25T10:00:00Z",
          message_id: "m-2",
          subject: "Following up",
          sequence_step_position: 2,
          occurrence_count: 3,
        },
        {
          ...base,
          kind: "EMAIL_BOUNCED",
          occurred_at: "2026-09-24T10:00:00Z",
          message_id: "m-3",
          subject: "Other campaign",
          sequence_step_position: 1,
          bounce_type: "HARD",
        },
        {
          ...base,
          kind: "EMAIL_SENT",
          occurred_at: "2026-09-23T10:00:00Z",
          message_id: "m-1",
          subject: "Quick idea",
          sequence_step_position: 1,
        },
      ],
    });

    renderTimeline();

    const items = await screen.findAllByTestId("lead-activity-item");
    expect(items).toHaveLength(4);
    const [reply, opened, bounced, sent] = items;

    expect(within(reply).getByText("Reply Received")).toBeInTheDocument();
    expect(within(reply).getByText("From jane@target.test")).toBeInTheDocument();
    expect(within(reply).getByText("Sounds interesting, let's talk.")).toBeInTheDocument();
    expect(within(reply).getByText("Fintech Outreach · Step 2")).toBeInTheDocument();
    expect(within(reply).getByText("Re: Quick idea")).toBeInTheDocument();
    expect(within(reply).getByRole("link", { name: "Open in inbox" })).toHaveAttribute(
      "href",
      "/app/inbox",
    );

    expect(within(opened).getByText("Email Opened (3 times)")).toBeInTheDocument();
    expect(within(bounced).getByText("Email Bounced (hard)")).toBeInTheDocument();
    expect(within(sent).getByText("Email Sent")).toBeInTheDocument();
    expect(within(sent).getByText("Fintech Outreach · Step 1")).toBeInTheDocument();
  });

  it("says so when the lead has no activity", async () => {
    getLeadActivity.mockResolvedValue({ lead_id: "lead-1", items: [] });
    renderTimeline();
    expect(await screen.findByText("No emails have been sent to this lead yet.")).toBeInTheDocument();
  });

  it("shows an error instead of an empty timeline when loading fails", async () => {
    getLeadActivity.mockRejectedValue(new Error("boom"));
    renderTimeline();
    expect(await screen.findByText(/couldn.t load this lead.s activity/i)).toBeInTheDocument();
  });
});
