import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import {
  ApprovalBar,
  approvalBlockedReason,
} from "@/components/campaigns/personalization/approval-bar";
import { describeFailureCode } from "@/components/campaigns/personalization/failure-codes";
import { GenerationProgressView } from "@/components/campaigns/personalization/generation-progress-panel";
import { SamplePreviewPanel } from "@/components/campaigns/personalization/sample-preview-panel";
import type {
  GenerationProgress,
  PersonalizationApproval,
  PreviewBatch,
  PreviewItem,
} from "@/types/domain";

function item(overrides: Partial<PreviewItem> = {}): PreviewItem {
  return {
    id: "p1",
    recipient: {
      audience_member_id: "m1",
      first_name: "Sarah",
      last_name: "Johnson",
      company: "Acme",
      title: "VP Sales",
    },
    step_id: "s1",
    step_position: 1,
    state: "OK",
    subject: "Quick idea for Acme",
    body_html: "<p>Hi Sarah, as VP Sales at Acme…</p>",
    facts: [
      { id: "F1", source: "LEAD", text: "Job title: VP Sales" },
      { id: "F2", source: "WEBSITE", text: "Acme builds tools for revenue teams." },
    ],
    research_summary: {
      status: "OK",
      source_url: "https://acme.test/",
      excerpt: "Acme builds tools for revenue teams.",
    },
    fallback_used: false,
    failure_codes: [],
    ...overrides,
  };
}

function batch(overrides: Partial<PreviewBatch> = {}): PreviewBatch {
  return {
    batch_id: "b1",
    config_digest: "d".repeat(64),
    current_digest: "d".repeat(64),
    stale: false,
    created_at: "2026-03-02T10:00:00Z",
    expires_at: "2026-03-09T10:00:00Z",
    complete: true,
    all_ok: true,
    items: [item()],
    ...overrides,
  };
}

const none: PersonalizationApproval = { status: "NONE", approved_at: null, approved_by: null };
const approved: PersonalizationApproval = {
  status: "APPROVED",
  approved_at: "2026-03-02T10:05:00Z",
  approved_by: "u1",
};
const stale: PersonalizationApproval = { status: "STALE", approved_at: null, approved_by: null };

function panel(props: Partial<React.ComponentProps<typeof SamplePreviewPanel>> = {}) {
  const onGenerate = vi.fn();
  render(
    <SamplePreviewPanel
      batch={null}
      loading={false}
      loadError={null}
      canGenerate
      blockedReason={null}
      generating={false}
      generateError={null}
      onGenerate={onGenerate}
      {...props}
    />,
  );
  return { onGenerate };
}

describe("SamplePreviewPanel", () => {
  it("offers to generate the first samples", async () => {
    const { onGenerate } = panel();
    expect(screen.getByText("No samples yet.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Generate samples" }));
    expect(onGenerate).toHaveBeenCalledTimes(1);
  });

  it("shows the generated email, the subject and what it was personalized with", () => {
    panel({ batch: batch() });
    expect(screen.getByText("Sarah Johnson")).toBeInTheDocument();
    expect(screen.getByText("VP Sales — Acme")).toBeInTheDocument();
    expect(screen.getByText("Quick idea for Acme")).toBeInTheDocument();
    expect(screen.getByText("Job title: VP Sales")).toBeInTheDocument();
    expect(screen.getByText("Website")).toBeInTheDocument();
    expect(screen.getByText(/Website research \(https:\/\/acme\.test\/\)/)).toBeInTheDocument();
    expect(screen.getByText("All samples were generated.")).toBeInTheDocument();
  });

  it("renders the generated body in a fully sandboxed frame", () => {
    panel({ batch: batch() });
    const frame = screen.getByTitle("Email 1 for Sarah");
    expect(frame).toHaveAttribute("sandbox", "");
    expect(frame.getAttribute("srcdoc")).toContain("as VP Sales at Acme");
  });

  it("numbers each recipient's emails and groups them by lead", () => {
    panel({
      batch: batch({
        items: [
          item({ id: "a", step_position: 3, subject: "Second" }),
          item({ id: "b", step_position: 1, subject: "First" }),
          item({
            id: "c",
            recipient: {
              audience_member_id: "m2",
              first_name: "James",
              last_name: null,
              company: "Nova",
              title: null,
            },
          }),
        ],
      }),
    });
    expect(screen.getByText("James")).toBeInTheDocument();
    const emails = screen.getAllByTestId("sample");
    // Sarah's two emails come out in step order.
    expect(emails[0]).toHaveTextContent("Email 1");
    expect(emails[0]).toHaveTextContent("First");
    expect(emails[1]).toHaveTextContent("Email 2");
    expect(emails[1]).toHaveTextContent("Second");
  });

  it("explains failures in plain language and warns about them", () => {
    panel({
      batch: batch({
        all_ok: false,
        items: [
          item({
            state: "FAILED",
            subject: null,
            body_html: null,
            facts: [],
            failure_codes: ["unsupported_number", "cta_missing"],
          }),
        ],
      }),
    });
    expect(screen.getAllByText(/could not be generated/i).length).toBeGreaterThan(0);
    expect(screen.getByText(/stated a number that is not in your reference/i)).toBeInTheDocument();
    expect(screen.getByText("The email lost your call to action.")).toBeInTheDocument();
    expect(screen.getByText(/1 sample could not be generated/i)).toBeInTheDocument();
    expect(screen.queryByText("All samples were generated.")).not.toBeInTheDocument();
  });

  it("says when an email falls back to the standard version", () => {
    panel({ batch: batch({ items: [item({ fallback_used: true })] }) });
    expect(screen.getByText(/not enough data to personalize/i)).toBeInTheDocument();
  });

  it("shows progress while samples are still being written and blocks regenerating", () => {
    panel({
      batch: batch({
        complete: false,
        all_ok: false,
        items: [item({ state: "PENDING", subject: null, body_html: null, facts: [] })],
      }),
    });
    expect(screen.getByRole("status")).toHaveTextContent("Writing samples…");
    expect(screen.getByRole("button", { name: "Generate new samples" })).toBeDisabled();
  });

  it("warns when the samples are stale", () => {
    panel({ batch: batch({ stale: true }) });
    expect(screen.getByText(/changed after these samples were generated/i)).toBeInTheDocument();
    expect(screen.queryByText("All samples were generated.")).not.toBeInTheDocument();
  });

  it("blocks generating without an objective and explains why", () => {
    panel({ blockedReason: "Save the campaign objective first, then generate samples." });
    expect(screen.getByRole("button", { name: "Generate samples" })).toBeDisabled();
    expect(screen.getByText(/Save the campaign objective first/)).toBeInTheDocument();
  });

  it("hides the generate button for people who cannot edit the campaign", () => {
    panel({ canGenerate: false, batch: batch() });
    expect(screen.queryByRole("button", { name: /generate/i })).not.toBeInTheDocument();
  });

  it("shows server errors", () => {
    panel({ generateError: new Error("x"), loadError: new Error("y") });
    expect(screen.getAllByRole("alert")).toHaveLength(2);
  });
});

describe("approvalBlockedReason", () => {
  it("only allows approving a complete, current, fully generated batch", () => {
    expect(approvalBlockedReason(none, null)).toMatch(/Generate sample emails first/);
    expect(approvalBlockedReason(none, batch({ stale: true }))).toMatch(/Generate new samples/);
    expect(approvalBlockedReason(none, batch({ complete: false }))).toMatch(/still being generated/);
    expect(approvalBlockedReason(none, batch({ all_ok: false }))).toMatch(/failed/);
    expect(approvalBlockedReason(approved, batch())).toMatch(/already approved/);
    expect(approvalBlockedReason(none, batch())).toBeNull();
    expect(approvalBlockedReason(stale, batch())).toBeNull(); // re-approval after edits
  });
});

describe("ApprovalBar", () => {
  function bar(props: Partial<React.ComponentProps<typeof ApprovalBar>> = {}) {
    const onApprove = vi.fn();
    render(
      <ApprovalBar
        approval={none}
        batch={batch()}
        canApprove
        readOnly={false}
        approving={false}
        error={null}
        onApprove={onApprove}
        {...props}
      />,
    );
    return { onApprove };
  }

  it("lets a manager approve a good batch", async () => {
    const { onApprove } = bar();
    expect(screen.getByText("Not approved yet")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Approve samples" }));
    expect(onApprove).toHaveBeenCalledTimes(1);
  });

  it("disables approval and says why when the batch is not ready", () => {
    bar({ batch: batch({ all_ok: false }) });
    expect(screen.getByRole("button", { name: "Approve samples" })).toBeDisabled();
    expect(screen.getByText(/Some samples failed/)).toBeInTheDocument();
  });

  it("shows an approved state with the date", () => {
    bar({ approval: approved });
    expect(screen.getByText("Approved")).toBeInTheDocument();
    expect(screen.getByText(/will require a new approval/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Approve samples" })).toBeDisabled();
  });

  it("flags an out-of-date approval", () => {
    bar({ approval: stale });
    expect(screen.getByText("Approval out of date")).toBeInTheDocument();
  });

  it("does not offer the button to people who cannot approve", () => {
    bar({ canApprove: false });
    expect(screen.queryByRole("button", { name: "Approve samples" })).not.toBeInTheDocument();
    expect(screen.getByText(/Only Managers and above can approve/)).toBeInTheDocument();
  });

  it("shows nothing actionable once the campaign left draft", () => {
    bar({ readOnly: true, approval: approved });
    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });

  it("surfaces an approval error", () => {
    bar({ error: "The objective or emails changed." });
    expect(screen.getByRole("alert")).toHaveTextContent(/changed/);
  });
});

describe("GenerationProgressView", () => {
  const base: GenerationProgress = {
    campaign_id: "c1",
    pending: 12,
    succeeded: 30,
    failed: 2,
    superseded: 1,
    fallback: 4,
    oldest_pending_at: "2026-03-02T12:00:00Z",
    failure_codes: { unsupported_number: 1, attempts_exhausted: 1 },
    budget: {
      generation_used: 40,
      generation_cap: 2000,
      preview_used: 3,
      preview_cap: 100,
      fetch_used: 5,
      fetch_cap: 2000,
    },
  };

  it("shows counts and explains why some emails were not sent", () => {
    render(<GenerationProgressView progress={base} />);
    expect(screen.getByTestId("pending")).toHaveTextContent("12");
    expect(screen.getByTestId("succeeded")).toHaveTextContent("30");
    expect(screen.getByTestId("fallback")).toHaveTextContent("4");
    expect(screen.getByTestId("failed")).toHaveTextContent("2");
    expect(screen.getByText(/were not sent because a safe, valid email/)).toBeInTheDocument();
    expect(screen.getByText(/1 × The email stated a number/)).toBeInTheDocument();
    expect(screen.getByText(/kept failing and the attempts were used up/)).toBeInTheDocument();
    expect(screen.getByText(/Today: 40 \/ 2000 emails written/)).toBeInTheDocument();
  });

  it("tells the user when the daily limit was reached", () => {
    render(
      <GenerationProgressView
        progress={{ ...base, failed: 0, failure_codes: {}, budget: { ...base.budget, generation_used: 2000 } }}
      />,
    );
    expect(screen.getByText(/daily writing limit \(2000\) has been reached/)).toBeInTheDocument();
    expect(screen.queryByText(/were not sent/)).not.toBeInTheDocument();
  });
});

describe("describeFailureCode", () => {
  it("humanizes unknown codes instead of showing nothing", () => {
    expect(describeFailureCode("something_new_happened")).toBe("Something new happened");
  });
});
