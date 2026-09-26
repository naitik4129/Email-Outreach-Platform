import { describe, expect, it } from "vitest";

import {
  canApprovePersonalization,
  canDraftCampaign,
  canExecuteCampaign,
} from "@/lib/permissions";

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

describe("canApprovePersonalization", () => {
  it("is the same tier as activating: managers and above only", () => {
    for (const role of ["OWNER", "ADMIN", "MANAGER"] as const) {
      expect(canApprovePersonalization(role)).toBe(true);
    }
    for (const role of ["MEMBER", "VIEWER", undefined] as const) {
      expect(canApprovePersonalization(role)).toBe(false);
    }
  });
});
