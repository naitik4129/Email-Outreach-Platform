/**
 * Mirrors backend/app/core/permissions.py's ROLE_PERMISSIONS for the two
 * campaign capabilities. This is UX only -- the backend re-checks every
 * mutation server-side regardless of what this returns; a frontend bypass
 * here can only hide/show buttons, never actually authorize a request.
 *
 * Campaigns need two distinct tiers reused across ~8 pages (unlike
 * templates/mailboxes, which only ever needed one), so this is factored out
 * once here instead of each page redefining a local canManageX() function.
 */

export type RoleCode = "OWNER" | "ADMIN" | "MANAGER" | "MEMBER" | "VIEWER" | undefined;

const DRAFT_ROLES = new Set(["OWNER", "ADMIN", "MANAGER", "MEMBER"]);
const EXECUTE_ROLES = new Set(["OWNER", "ADMIN", "MANAGER"]);

/** campaigns.draft: create/edit/configure a DRAFT campaign. */
export function canDraftCampaign(role: RoleCode): boolean {
  return Boolean(role && DRAFT_ROLES.has(role));
}

/** campaigns.execute: activate/pause/resume/archive an activated campaign.
 * Phase 7 never activates a campaign, but the Review screen still needs to
 * know this tier exists so a disabled "Start Campaign" placeholder can
 * explain who *would* be able to use it once Phase 8 ships. */
export function canExecuteCampaign(role: RoleCode): boolean {
  return Boolean(role && EXECUTE_ROLES.has(role));
}
