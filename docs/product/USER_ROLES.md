# Workspace roles and permissions

Approved by the project owner during the migration correction review. This is
the permission source of truth; [USER_FLOWS](USER_FLOWS.md) owns user journeys.
Permissions require a current ACTIVE membership in the selected workspace.
JWT role claims, resource IDs and frontend visibility are not authorization.

| Capability | VIEWER | MEMBER | MANAGER | ADMIN | OWNER |
|---|---|---|---|---|---|
| Read ordinary product data and safe team directory | Yes | Yes | Yes | Yes | Yes |
| Manage leads/lists, import leads, publish/archive templates | No | Yes | Yes | Yes | Yes |
| Create/edit/duplicate/archive draft campaigns | No | Yes | Yes | Yes | Yes |
| Execute/pause/resume/archive activated campaigns, controlled tests | No | No | Yes | Yes | Yes |
| Connect/reconnect/disconnect mailboxes, ordinary sender metadata | No | No | Yes | Yes | Yes |
| Archive inbox conversations | No | No | Yes | Yes | Yes |
| Add/import MANUAL suppression | No | Yes | Yes | Yes | Yes |
| Release MANUAL suppression with audit/reason | No | No | No | Yes | Yes |
| Workspace settings and tenant rate policy | No | No | No | Yes | Yes |
| Read safe workspace audit and invitation status | No | No | No | Yes | Yes |
| Invite/revoke/change other memberships | No | No | No | No | Yes |
| Transfer ownership / request workspace deletion | No | No | No | No | Yes |

All roles may edit their own profile preferences and mark their own notifications
read. Notification access additionally requires current workspace membership.
No customer can release UNSUBSCRIBE, HARD_BOUNCE, COMPLAINT or PLATFORM_BLOCK.
No role permits changing platform safety restrictions, bypassing broader limits,
reading stored credentials through ordinary product queries, or altering history.
Billing, CRM, AI, API keys and open/click tracking remain deferred; no grants.

Exactly one ACTIVE OWNER is required at commit. Ownership transfer demotes the
old Owner to ADMIN then promotes an existing active member in one workspace-lock
transaction with expected versions and audit. Invitations never assign OWNER.
Only the Owner manages other memberships; no self role changes or implicit
self-removal. Bootstrap/acceptance/transfer/deletion are explicit future commands;
this policy does not grant generic membership or workspace INSERT/DELETE.

Campaign execution still requires the campaign state machine. Members can edit
only DRAFT campaigns. After activation audience/content are immutable, schedule
and limit changes require PAUSED, and RUNNING must pause before archive.
Completed/archived outreach is duplicated into a new draft, never reopened.
Published content and sending history are retained. Hard deletion and retention
jobs remain disabled until their workflows are approved and implemented.

Hyper-personalized campaigns ([ADR-0011](../adr/0011-hyper-personalized-campaign-type.md)) add no capability: editing the objective and generating sample previews use campaigns.draft, approving the previews that unlock activation uses campaigns.execute, and reading generation progress uses product.read.

Database capability names: product.read, contacts.manage, templates.manage,
campaigns.draft, campaigns.execute, mailboxes.manage, inbox.manage,
suppression.add, suppression.release_manual, workspace.manage, audit.read,
team.manage, ownership.manage. Unknown capabilities deny. Backend action checks
must use this same matrix, in addition to resource/state validation.
