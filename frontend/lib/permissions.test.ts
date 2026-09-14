import { describe, expect, it } from "vitest";

import { canDraftCampaign, canExecuteCampaign } from "@/lib/permissions";

describe("canDraftCampaign", () => {
  it("denies VIEWER and undefined", () => {
    expect(canDraftCampaign("VIEWER")).toBe(false);
    expect(canDraftCampaign(undefined)).toBe(false);
  });

  it("allows MEMBER, MANAGER, ADMIN, OWNER", () => {
    expect(canDraftCampaign("MEMBER")).toBe(true);
    expect(canDraftCampaign("MANAGER")).toBe(true);
    expect(canDraftCampaign("ADMIN")).toBe(true);
    expect(canDraftCampaign("OWNER")).toBe(true);
  });
});

describe("canExecuteCampaign", () => {
  it("denies VIEWER, MEMBER, and undefined", () => {
    expect(canExecuteCampaign("VIEWER")).toBe(false);
    expect(canExecuteCampaign("MEMBER")).toBe(false);
    expect(canExecuteCampaign(undefined)).toBe(false);
  });

  it("allows MANAGER, ADMIN, OWNER", () => {
    expect(canExecuteCampaign("MANAGER")).toBe(true);
    expect(canExecuteCampaign("ADMIN")).toBe(true);
    expect(canExecuteCampaign("OWNER")).toBe(true);
  });
});
