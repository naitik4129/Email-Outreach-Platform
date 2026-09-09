# Supabase migration correction review

Status: five **unapplied drafts corrected**, static review only. No database was
created, reset, connected to or migrated, and no provider operation was invoked.
This is a schema contract for backend implementation, not production certification.
Apply the complete chain in order in an isolated environment before shared use.
Once applied to a shared environment these files become immutable history.

The approved permission baseline is [USER_ROLES](../product/USER_ROLES.md).
The original journeys are preserved in [USER_FLOWS](../product/USER_FLOWS.md).
[DATABASE](DATABASE.md) owns schema contracts, and the subsystem documents own
business transitions. The migrations intentionally do not implement the backend
command layer, provider adapters, Redis protocol or scheduler algorithms.

## Per-migration review

### 0001_initial.sql — identity and security

Creates `profiles`, `workspaces`, `workspace_memberships`, two isolated helper
identities and eight runtime capabilities. `app_api` and the foundation reader
remain. Added connection, general worker, send worker, sync worker, scheduler,
outbox relay and rate controller roles; `app_integrity_guard` owns only fixed
trigger functions. No login, password, role inheritance, BYPASSRLS or object
ownership is conferred on a runtime capability. Temporary ownership-transfer
membership/schema privileges are revoked before commit; preflight rejects
inherited schema-creation privileges.

Retains transaction-local user/workspace helpers, private self-service profiles,
safe workspace selectors/directory and the nonrecursive membership reader.
Adds the closed action-permission function, membership `(workspace,user,id)` key
and narrow worker reads needed by notifications. Exactly one ACTIVE Owner remains
enforced by the active-Owner unique index and deferred owner constraint triggers.
Workspace name/default edits remain Admin/Owner; generic membership and workspace
creation/deletion remain denied. Safety counters are maintained by triggers added
in 0004. No objects or data are removed.

Compatibility/risk: requires Supabase Auth, standard browser roles, PostgreSQL 15+
and a migration identity able to create roles and transfer helper ownership.
Dedicated deployment login provisioning remains necessary. GUC context is trusted
backend input, not protection from arbitrary SQL in a compromised backend.
Validate owner races, revoked membership and pool cleanup at runtime.
Recovery: this file is transactional; on failure investigate prerequisites and
retry only after verifying rollback. After successful shared application, repair
through a new migration; never drop identity tables to undo a feature issue.

### 0002_contacts_content.sql — contacts, safety identity and content

Creates the 15 tables listed below. Fixes template current-version ownership;
adds canonical storage shape/version checks, invitation time/role coherence,
command receipt actor identity and expiry checks, import result-kind/shape checks,
lease consistency, immutable recipient identities and source identity checks.
List membership changes lock their parent; AFTER triggers advance revisions only
for actual row changes, including correct `ON CONFLICT DO NOTHING` behavior.
Lead contact revisions are computed by the database. Capture counters cannot be
set by runtime INSERT or UPDATE grants.

Manual suppression re-observation/reactivation updates the independent reason row;
source records and prior audit rows remain retained. Release requires Admin/Owner,
the current actor and matching action/target/reason audit in 0005. Worker grants
allow reactivation but deny release. No customer release of unsubscribe/bounce/
complaint/platform blocks. Invitation digests, unsubscribe mappings and source
evidence are absent from ordinary product reads. Import processing can read/write
leads and lists but cannot read credentials.

Indexes retain canonical uniqueness, active suppression lookup, import row identity,
expiry and due-work scans; adds scoped expired-import-lease recovery. All FKs retain
RESTRICT behavior. Evidence and unsubscribe message FKs are installed in 0004;
release-audit binding is installed in 0005, so deploy all five files before runtime.
No tables or existing-data columns are removed.

Compatibility/risk: normalization v1 rejects non-ASCII local parts and canonical
forms outside the initial storage shape. Backend parsing/IDNA validation is still
required. Full CSV mapping/limits, import idempotency transactions, token resolution
and invitation acceptance are backend milestones. Token resolution must use a
future dedicated digest lookup command; ordinary API address/token enumeration
is not granted. Recovery: transaction rollback on application failure; rerun jobs
only with durable row keys, cursors and lease generation. Never erase suppression
history to recover an import. New shared changes require additive migrations.

### 0003_mailboxes_campaigns.sql — provider accounts and campaign snapshots

Creates 15 tables. Adds owning campaign to draft/activated sequence and audience
pointers, sequence-step and planning references; binds enrollment to captured
campaign/audience/member/lead/address and configured mailbox. Current settings
reference an immutable version. Activation requires coherent identifiers, a frozen
sequence and READY audience. Parent-locking guards cover step INSERT/UPDATE/DELETE,
prevent sequence unfreezing and reject post-completion audience changes. Captured
addresses/revisions and enrolled recipient variables are checked against their
source rows. Planning references the actual activation.

Audience selection manifests record versioned list/manual selection. List capture
source insertion acquires a parent gate; source identity cannot change or reacquire
a released gate. Completion/failure must release all gates by commit. Audience
ordinal uniqueness replaces the redundant ordinary ordinal index. Adds OAuth,
refresh, sync and planning lease recovery indexes.

OAuth verifier envelopes are accessible only through a current-user, current-
membership connection capability. Connection ciphertext can be destroyed without
deleting nonsensitive generation history. Rotation creates a new generation;
disconnect fences stale workers. A composite current-connection pointer replaces
an unconstrained generation assumption. The single-active-workspace provider
account index remains. Controlled authorizations bind requester user/membership,
command receipt, mailbox and address, with nonempty approval evidence.

Member edits remain drafts; executing roles are Manager/Admin/Owner. Activated
content/audience and mailbox assignment are frozen, settings change only while
paused, running campaigns pause before archive, and terminal outreach is duplicated
into a new draft. No data-bearing object is removed.

Compatibility/risk: old draft INSERT shapes must supply a versioned selection
manifest and current-settings/activation fields. Backend commands must acquire
locks consistently, use expected versions, produce content digests, validate
schedules/timezones and revalidate permission before controlled dispatch. Connection
encryption/key management, refresh leases, conditional pointer movement and provider
account verification remain backend work. Recovery: roll back a failed migration;
recover captures using persisted selections, revisions and leases; never reopen
a completed snapshot or reuse a revoked credential generation. After shared apply,
use new migrations and compensation commands, preserving provider/send evidence.

### 0004_messages_events_inbox.sql — messages, evidence and work transport

Creates 13 tables and installs earlier evidence/token FKs. Messages bind campaign
recipient/enrollment/sequence/step or controlled mailbox/address authorization;
attempts bind the message mailbox and credential generation. Unsubscribe tokens
bind the originating message recipient. Reply attribution checks the actual outbound
campaign/enrollment, including NULL relationships that composite FKs alone skip.

PLANNED content may be incomplete. Progress requires complete frozen destination,
sender and rendered content; rendered snapshots are immutable. Due/anchor, queue
origin/expiry, retry metadata, acceptance evidence and terminal reasons are checked.
Terminal attempt evidence cannot be rewritten; new observations append separately.
IMAP folder + UIDVALIDITY + UID identity is retained alongside provider identifiers;
provider evidence fields are bounded. Safety-hold triggers maintain workspace/mailbox
counters under the workspace-first lock order.

Provider receipts have lease expiry and protected payload digests; adds expired
receipt and delivery lease scans, plus published-work recovery indexing. Authorized
API/connection mutations may append domain event, outbox work/delivery and audit
records atomically. Event uniqueness includes event type so one aggregate version
can have distinct effects. Consumer receipts remain the authority for completed
effects; broker publication alone is not completion. No history table is deletable
by runtime roles. No objects/data are removed.

Compatibility/risk: backend must freeze sender signature into rendered body, create
stable RFC Message-ID before sending, implement fenced claim/authorization/provider
invocation, reconcile UNKNOWN rather than blindly retry, and apply suppression/
reply outcomes atomically. A role-scoped outbox row is not permission to send;
consumers revalidate business state. Runtime tests must establish race behavior.
Recovery: retry DB transactions with idempotency keys; recover leases; reconcile
external uncertainty against durable evidence. Never reset attempts or delete
consumer receipts to force a resend. Shared schema rollback uses new migrations.

### 0005_operations_platform.sql — operations and capacity

Creates nine tables. Adds `notifications(workspace_id,id)` needed by its delivery
FK. PostgreSQL requires an appropriate candidate key on referenced columns; the
original draft would fail at this FK. See [PostgreSQL constraint documentation](https://www.postgresql.org/docs/current/ddl-constraints.html).
Notification recipient membership is bound to the actual user; reads/read-state
changes require current membership and self identity. General processing receives
scoped membership/preferences/notification reads. External delivery has SENDING
leases and an UNKNOWN state, plus recovery indexes, so ambiguity is not recorded
as success or a safe retry.

Audit rows distinguish USER/SYSTEM, bind API actor to current user, and deduplicate
request effects rather than entire requests. Manual release audit is checked against
its actor/action/target. Rate scope identity includes unit/window; tenant policy
identity also includes rolling/calendar-day mode. Historical debit scopes store
bounded policy snapshots, versions and calendar boundaries; scope accounting is
bound to the originating debit. The redundant attempt-prefix debit index is removed.
Only the rate-controller capability writes global recovery state. Missing or
unreconstructed control prevents capacity authorization; READY requires recovery
evidence. Runtime roles cannot change global configured limits or platform audit.

Compatibility/risk: debit writers must record all applicable units/windows and
policy snapshots atomically with attempts. Redis generation fencing/reconstruction,
calendar DST boundaries, conservative unknown accounting, notification address
resolution and verified provider results remain backend responsibilities. Platform
operator commands require a separately authenticated/audited implementation; no
generic operator grant is introduced. Recovery: reconstruct Redis from retained
debits under RECOVERING before READY. Reconcile UNKNOWN delivery; do not fabricate
provider success. Migration failure rolls back that file; shared corrections become
new migrations. No data-bearing object is removed.

## All-table review matrix

Every entry also inherits RLS, explicit grants, bounded state/configuration and
RESTRICT ownership rules. “Backend” means an implementation obligation, not a
missing table to be silently filled by UI logic.

| Migration/table | Schema implementation or correction | Backend dependency / explicit deferral |
|---|---|---|
| 0001 profiles | Auth FK; private preferences; generated version | Verified identity; transactional email address resolution through Auth service |
| workspaces | Owner gate, settings grants, safety counter | Bootstrap; workspace deletion and platform restriction commands |
| workspace_memberships | Active Owner uniqueness, deferred owner check, user identity key | Audited invitation acceptance, team changes and ownership transfer |
| 0002 workspace_invitations | Digest unique; accepted/revoked/expiry shape; no OWNER invites | Owner invitation issuance/acceptance/revocation commands; digest lookup |
| command_receipts | Actor/operation/request identity, payload digest, expiry and result shape | Expected-version commands, payload comparison and atomic effects |
| leads | Canonical version, archive/validation state, generated contact revision | ASCII/IDNA parser, business validation and bounded custom fields |
| lead_lists | Capture counter, membership revision, archive | Lock ordering and user-facing capture conflict handling |
| lead_list_memberships | Parent FKs, unique membership, locked mutation and revision | Batched edits/imports; duplicate additions must not increment revision |
| recipient_addresses | Immutable versioned canonical safety identity | Normalization and absent-address insert/lock before safety/send checks |
| suppressions | Independent reason unique; release audit; reactivation | Ordered address locks, release command and immutable source insertion |
| suppression_sources | Source identity/FKs and append-only bounded evidence | Provider verification and idempotent observation effects |
| unsubscribe_tokens | Private digest; origin message/recipient FK | Dedicated token-resolution command and safe public unsubscribe route |
| platform_controls | Internal singleton, browser denied | Audited platform operator controls; missing state fails closed in send command |
| platform_suppressions | Global canonical blocks, internal access | Operator commands; customer release remains denied |
| templates | Owning current-version FK; Member mutations | Publish/archive/duplicate commands |
| template_versions | Immutable version/content identity, bounded variables | Template renderer/validator and digest generation |
| import_jobs | Object version/digest, coherent counts, lease/recovery | Storage authorization, streaming parser, bounded batches and reclaim |
| import_row_results | Durable row identity and kind-correct result shape | Atomic row/result/cursor accounting and safe error artifact generation |
| 0003 mailboxes | One active workspace/account, current connection FK, generation fence | Provider identity verification and reconnect/disconnect command |
| mailbox_connections | Protected generations, refresh leases, explicit secret destruction | KMS/encryption, refresh/disconnect serialization, no stale pointer promotion |
| oauth_flows | Current-actor connection RLS, verifier envelope, claim/expiry/result | CSRF/state, PKCE, callback validation and conditional claim completion |
| mailbox_sync_states | Connection generation, bounded cursor/scope, leases | Gmail/Graph/IMAP checkpoints and provider subscription lifecycle |
| campaigns | Owning pointers, settings reference, activation/freeze/archive guards | Versioned state-machine commands and activation transaction |
| campaign_sequences | Immutable frozen state and digest | Validate EMAIL/WAIT shape and generate frozen content digest |
| sequence_steps | Same-campaign sequence FK; locked all-operation freeze guard | Positive sequence ordering, supported variables and renderer validation |
| campaign_settings_versions | Immutable owning settings; draft/paused permission | Timezone/DST/window validation and generation increment |
| campaign_mailboxes | Unique assignment; frozen after draft | Validate current mailbox readiness before activation/send |
| campaign_audiences | Versioned selection manifest; immutable completed snapshot | Deterministic selection recovery and final content/source digest |
| audience_capture_sources | Parent locks, immutable revisions, exact gate counter | Acquire sources deterministically; release on success/failure/abandon |
| campaign_audience_members | Unique lead/address/ordinal; observed revision identity | Capture bounded lead snapshots, eligibility and exclusion evidence |
| campaign_planning_jobs | Actual activation/sequence binding; progress and leases | Recoverable capture/enroll/render batches |
| campaign_enrollments | Full captured recipient binding and frozen variables | Enrollment stop/acceptance/progression commands |
| controlled_send_authorizations | Requester user/member/receipt/mailbox/address binding | Exact controlled command, recipient approval, expiry/revocation recheck |
| 0004 conversations | Mailbox-scoped thread/anchor identity | Thread matching, paging and Manager archive command; unread state deferred |
| messages | Recipient/purpose FKs, immutable rendered envelope, lifecycle checks | Render/claim/authorize/send/reconcile and stable RFC identity |
| message_attempts | Same mailbox/message/generation; one unresolved attempt | Bounded provider call and write-once final outcome |
| attempt_evidence | Append-only observation identity and bounded references | Verified acceptance/rejection/reconciliation classification |
| inbound_messages | Mailbox conversation, generation, provider/IMAP identities | Provider parsing, bounded retention/redaction and sync checkpoint transaction |
| inbound_outreach_links | Same outbound mailbox/enrollment including NULL checks | Conservative matching and confirmed-reply command |
| recipient_outcomes | Unique semantic outcome, inbound/event references | Stop only on documented confirmed outcomes |
| provider_receipts | Verified scope identity, digest, lease expiry/retry | Webhook authentication, tenant mapping and atomic processing |
| safety_holds | Source/target identity and database-maintained counter | Persist hold before acknowledgement; resolve after committed safety effects |
| domain_events | Semantic uniqueness and distinct type per aggregate version | Versioned event allowlist/payload validation |
| outbox_work | Immutable semantic work identity, durable availability | Enqueue effects in mutation transaction; safe supersession |
| outbox_deliveries | Lease/due/publication states; published recovery index | Relay redelivery until consumer receipt, with fencing |
| consumer_receipts | Unique effect completion identity | Commit effect and receipt in one transaction |
| 0005 audit_events | Current-user vs system actor, request/effect identity | Redacted action/effect snapshots, request correlation and reasons |
| platform_audit_events | Separate immutable internal history | Dedicated authenticated operator commands; no tenant grants |
| notifications | Delivery candidate key; user/member binding, self read state | Event fanout, current active recipients and preferences |
| notification_deliveries | Pending/sending/unknown/terminal outcomes and leases | Transactional provider delivery and unknown reconciliation |
| rate_control | Dedicated recovery capability and explicit readiness | Redis generation fence, reconstruction/watermark proof |
| rate_scopes | Multiple unit/window scopes; internal configured policy | Operator configuration and ordered authorization locks |
| tenant_rate_policies | Multiple rolling/calendar windows; Admin policy grants | Apply every scope without relaxing platform/provider ceilings |
| capacity_debits | Durable attempt/reservation accounting | Atomic authorization and evidence-based release only |
| capacity_debit_scopes | Immutable policy snapshot, version, unit/quantity/bucket | Conservative recovery over all windows, DST and uncertain sends |

## MVP requirement coverage and handoff

This maps database obligations in every MVP feature area. Requirements consisting
of UI, infrastructure or provider behavior remain backend/runtime work even where
their storage is present. No feature is marked operational merely because it has
a table.

| MVP sections | Database coverage | Remaining work |
|---|---|---|
| 3–6 foundation/principles | Tenant FKs/RLS, roles, events/outbox/receipts, versions | FastAPI/SQLAlchemy transaction layer, Redis/Celery, observability and deployments |
| 7 authentication | Auth-linked profiles and private preferences | JWT verification, login/recovery/session lifecycle |
| 8 workspaces / 9 RBAC | Workspaces/memberships, exact Owner, approved action matrix | Bootstrap, invites/team/transfer commands and endpoint enforcement |
| 10 leads / 11 lists | Canonical identity, revisions, list gate and uniqueness | Parsing, filtering, archive and conflict-aware batch APIs |
| 12 CSV imports | Immutable file identity, mapping, cursor/count/result/lease | Storage access, file/schema bounds and restart-safe worker |
| 13 suppression / 27 unsubscribe / 28 bounce / 29 complaint | Reason-specific blocks, sources, token origin, outcomes/holds | Public token command, verified classification and pre-send safety locks |
| 14 templates | Owning current pointer and immutable versions | Rendering, sanitization, placeholder validation and publishing |
| 15 mailboxes / 16 Gmail / 17 Microsoft / 18 SMTP | Provider identity/generations, OAuth and sync state | OAuth/KMS, Gmail/Graph/SMTP adapters, IMAP, SSRF/TLS and controlled tests |
| 19 campaigns / 20 sequences | Frozen audience/content, settings, assignments/enrollments | Activation, pause/resume/archive and sequence-stop command orchestration |
| 21 scheduling | Immutable settings, generations, due/anchor fields, scoped indexes | DST/local-day calculation, quiet windows and clock-skew tests |
| 22 sends / 24 retries | Render snapshots, message/attempt lifecycle and evidence | Atomic authorization, provider invocation, retry classification and reconciliation |
| 23 rate limiting | Multi-window policies, debit snapshots, recovery state | Redis protocol, ordered scope locks, day boundaries and reconstruction |
| 25 replies / 26 inbox | Provider/IMAP identity, conservative links, conversations | Sync checkpoint atomics, matching and inbox APIs; optional unread deferred |
| 30 analytics | Messages, attempts, outcomes and immutable domain events | Defined metric queries/projections without counting attempts as sends |
| 31 deliverability | Mailbox health/policy/circuit, outcomes, restrictions | Provider signals and DNS guidance; advanced scoring/warmup deferred |
| 32 notifications | Durable recipient/member/event key and delivery state | Preference-aware fanout, address resolution and transactional provider |
| 33 admin/abuse | Platform controls, safety holds and separate audit | Dedicated operator commands; workspace Admin is not platform operator |
| 34–36 out-of-MVP/later | No billing/CRM/AI/open-click/API-key schema or grants | Explicitly deferred; no inferred migration coverage |
| 37–39 milestone order | Five-file schema chain can precede campaign backend | Start auth/bootstrap/authorization, then controlled Gmail before campaign runtime |
| 40–41 release criteria/invariants | Structural support and regression scenarios below | Actual migration replay, role/race tests, provider/Redis recovery, load, monitoring and deployment validation |

## Static checks and limits

Recorded result: five migrations, 55 tables, 153 foreign keys, 318 policies,
38 functions and 318 table grants; zero structural errors and zero duplicate-index
warnings. All eight static-tool regression tests passed. These numbers describe
source inspection, not a database catalogue obtained by applying the migrations.

Run `python scripts/check_migrations.py` and
`python -m unittest discover -s tests/static -v` from the repository root.
The checker tokenizes quoted/commented SQL and checks file order, declared tables,
all FK source/target columns and candidate keys (including later ALTER constraints),
ENABLE/FORCE RLS, grant columns/required INSERT fields, browser/secret/delete grants,
function declarations/revocations and duplicate policy/index definitions.
It does **not** parse PostgreSQL semantics, execute PL/pgSQL, model RLS evaluation,
prove race safety or inspect production grants. Passing it is not evidence that
the migrations execute successfully. Manual review supplies the cross-parent,
NULL-check, permission, history and recovery analysis; runtime tests must confirm it.

Use [database regression scenarios](../../tests/database/REGRESSION_SCENARIOS.md)
as the acceptance contract for the next isolated-database validation phase.
All database/provider/Redis scenarios are **prepared, not executed**.

## Deployment and recovery sequence

1. Review these five files and provision isolated Supabase prerequisites; install
   dedicated login identities with only their one runtime capability. Do not grant
   `app_integrity_guard` or `app_foundation_reader` to any runtime identity.
2. Apply 0001 → 0002 → 0003 → 0004 → 0005 to an empty disposable test project.
   No application traffic may run against a partially applied chain.
3. Execute role, tenant, concurrency and recovery scenarios. Inspect query plans
   for tenant due scans, suppression, import/capture, fanout and inbox paging.
4. Implement and validate auth/bootstrap/authorization and controlled Gmail with
   test accounts. Campaign backend follows the proven safety foundation.
5. Before production, verify grants/default privileges, backup/restore, migration
   history, secrets provisioning, provider limits and operational controls.

These are create-only drafts for an empty schema. Each file has BEGIN/COMMIT;
failure should roll back that file, not earlier successful files. Investigate the
actual transaction state before retry. Do not reset a shared database or use a
down migration that drops evidence. A production correction requires a new reviewed
migration and an explicit execution decision. No environment configuration or
credentials were changed during this review.
