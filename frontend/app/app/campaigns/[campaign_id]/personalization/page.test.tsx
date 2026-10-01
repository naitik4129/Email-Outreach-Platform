import { describe, expect, it, vi } from "vitest";

// Next's redirect() works by throwing; the mock does the same so the page stops.
const redirect = vi.hoisted(() =>
  vi.fn((path: string) => {
    throw new Error(`NEXT_REDIRECT:${path}`);
  }),
);
vi.mock("next/navigation", () => ({ redirect }));

import PersonalizationRedirect from "./page";

describe("Personalization route (merged into Sequence, ADR-0016)", () => {
  it("sends old links and bookmarks to the Sequence tab", async () => {
    await expect(
      PersonalizationRedirect({ params: Promise.resolve({ campaign_id: "camp-1" }) }),
    ).rejects.toThrow("NEXT_REDIRECT:/app/campaigns/camp-1/sequence");
    expect(redirect).toHaveBeenCalledWith("/app/campaigns/camp-1/sequence");
  });

  it("encodes the campaign id, so a crafted id cannot change the destination", async () => {
    redirect.mockClear();
    await expect(
      PersonalizationRedirect({ params: Promise.resolve({ campaign_id: "a/../b?x=1" }) }),
    ).rejects.toThrow();
    expect(redirect).toHaveBeenCalledWith(
      `/app/campaigns/${encodeURIComponent("a/../b?x=1")}/sequence`,
    );
  });
});
