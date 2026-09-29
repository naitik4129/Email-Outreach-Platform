import { afterEach, describe, expect, it, vi } from "vitest";

const apiRequest = vi.hoisted(() => vi.fn());
vi.mock("@/lib/api-client", () => ({ apiRequest }));

import {
  listTeamMembers,
  removeMember,
  resendInvitation,
  revokeInvitation,
  transferOwnership,
  updateMemberRole,
} from "@/lib/team-api";

const base = "/api/v1/workspaces/ws-1";

// team.py (backend) mounts at /api/v1/workspaces/{workspace_id} and exposes
// /members, /members/{id}, /transfer-ownership, /invitations/{id}/revoke --
// NOT /team/*. A prior regression (commit ec4eacb) pointed these client
// functions at /team/* paths that don't exist on the backend, so every one
// of these calls 404'd in production despite looking wired in the UI.
describe("team api client", () => {
  afterEach(() => vi.clearAllMocks());

  it("lists members from /members, not /team", async () => {
    apiRequest.mockResolvedValue({ data: [] });
    await listTeamMembers("ws-1");
    expect(apiRequest).toHaveBeenCalledWith(`${base}/members`);
  });

  it("updates a member's role via PATCH /members/{id}", async () => {
    apiRequest.mockResolvedValue({ data: {} });
    await updateMemberRole("ws-1", "m-1", { role_code: "ADMIN", expected_version: 2 });
    expect(apiRequest).toHaveBeenCalledWith(`${base}/members/m-1`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ role_code: "ADMIN", expected_version: 2 }),
    });
  });

  it("removes a member via DELETE /members/{id} carrying expected_version", async () => {
    apiRequest.mockResolvedValue({ data: undefined });
    await removeMember("ws-1", "m-1", { expected_version: 3 });
    expect(apiRequest).toHaveBeenCalledWith(`${base}/members/m-1`, {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ expected_version: 3 }),
    });
  });

  it("transfers ownership via POST /transfer-ownership with membership ids and versions", async () => {
    apiRequest.mockResolvedValue({ data: {} });
    await transferOwnership("ws-1", {
      target_membership_id: "m-2",
      expected_owner_version: 1,
      expected_target_version: 1,
    });
    expect(apiRequest).toHaveBeenCalledWith(`${base}/transfer-ownership`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        target_membership_id: "m-2",
        expected_owner_version: 1,
        expected_target_version: 1,
      }),
    });
  });

  it("resends an invitation via POST /invitations/{id}/resend", async () => {
    apiRequest.mockResolvedValue({ data: {} });
    await resendInvitation("ws-1", "inv-1");
    expect(apiRequest).toHaveBeenCalledWith(`${base}/invitations/inv-1/resend`, {
      method: "POST",
    });
  });

  it("revokes an invitation via POST /invitations/{id}/revoke, not DELETE", async () => {
    apiRequest.mockResolvedValue({ data: undefined });
    await revokeInvitation("ws-1", "inv-1");
    expect(apiRequest).toHaveBeenCalledWith(`${base}/invitations/inv-1/revoke`, {
      method: "POST",
    });
  });
});
