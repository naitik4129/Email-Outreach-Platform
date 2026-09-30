# Archive, purge and privacy erasure

## Status

**Accepted for implementation — 2026-09-28.** The project owner asked for the plan to be completed in full, which adopted the recommended default for each open decision (see "Decisions taken" below). It supersedes the "hard delete is not enabled until deletion policy approved" position in [CAMPAIGN_STATE_MACHINE](../architecture/CAMPAIGN_STATE_MACHINE.md) and resolves the retention/erasure open decisions in [DOMAIN_MODEL](../architecture/DOMAIN_MODEL.md), [DATABASE](../database/DATABASE.md) and [SUPPRESSION](../architecture/SUPPRESSION.md); those documents now point here. Migration 0033 is prepared and **not applied**.

## Context

Operators need to remove campaigns, templates, leads, lead lists, mailboxes, imports and suppressions, and to erase personal data on request. Today:

- Archive exists for campaigns, templates, leads and lead lists. **Campaign archive is DRAFT-only** (`CampaignRepository.archive_campaign`), although the state machine allows archive from SCHEDULED, PAUSED, ERROR and COMPLETED, so a sent or paused campaign cannot be removed from the UI. Templates, leads and lists have no unarchive.
- There is no permanent delete or erasure anywhere. Every foreign key is `ON DELETE RESTRICT` (migrations 0002–0005, 0025, 0029); a campaign owns about fifteen dependent tables even before its first send. Many tables are immutable or append-only by trigger or grant (frozen sequences, message snapshots, attempt evidence, personalization approvals).
- Documentation forbids generic cascades ("Cascade only from an approved deletion workflow"), requires unknown/in-flight sends to stay reconcilable after archive, and leaves retention and erasure to product/security review.

The requested goal is **privacy / erasure**, including for items with send history.

## Decision

Three separate operations replace the single word "delete":

1. **Archive / unarchive** — reversible, removes from ordinary views, destroys nothing. Campaign archive is completed for every state the state machine already allows. Templates, leads and lead lists gain unarchive. **Campaigns are not restored**; the approved rule "no reopening COMPLETED/ARCHIVED" stands and users duplicate instead.
2. **Purge** — physical deletion of an aggregate that has no send history (or whose history was already erased): never-sent campaigns, unused templates and versions, unused lists, imports and their source files.
3. **Erase** — for data with send history, **redact personal data in place** (recipient address, names, rendered subject/body, generated text, previews, conversation bodies, provider ids where personal) and keep the rows, counters and evidence the engine and analytics depend on. Sent history is not physically deleted.

Rules that apply to all three:

- **Suppressions are never deleted.** When a person who has an ACTIVE suppression is erased, their `recipient_addresses` row (the minimal opt-out record: the normalized address and its suppression) is kept and everything else about them is erased. An address without an active suppression is replaced by a placeholder. Suppression release (existing MANUAL authority) is unchanged. *Amended from the first draft, which proposed a salted hash: send-time suppression checks and imports look addresses up by their normalized form, so a hash would have needed changes in every lookup and would have weakened the opt-out guarantee this rule exists to protect. Keeping the address on the suppression list is the standard, minimal way to keep honouring an opt-out.*
- **Not while live.** Archiving works from DRAFT, SCHEDULED, PAUSED, ERROR and COMPLETED (never RUNNING: pause first; the database guard enforces this too). A campaign is purged only if it was never activated (DRAFT or ARCHIVED with no activation); an activated campaign must be ARCHIVED and is then *erased*. Messages that are queued, sending, retrying or unknown, and unresolved provider attempts, block erasure until reconciled. Messages not yet sent are set to SKIPPED and their enrollments STOPPED (`ERASED`) so nothing more is sent to an erased person.
- **Mailboxes:** a mailbox row can be removed only when it is DISCONNECTED (the existing disconnect flow already revokes the provider token and destroys the credentials). *Amended 2026-09-29: sending history no longer blocks it, see "Amendment: hard delete".*
- **Authority:** `workspace.manage` (ADMIN/OWNER), checked in the API and again inside every database command; the API also compares a typed confirmation (the item's name, its email address, or the word DELETE for imports) on the server. Requests are POST with the confirmation in the body so names and addresses never appear in URLs or access logs.
- **Mechanism:** explicit, ordered SECURITY DEFINER commands per aggregate (migration 0033), owned by a dedicated `app_erasure` role that has only the table privileges and RLS policies it needs and no login. Each command runs in one transaction, so it either completes or changes nothing, and repeating it is safe. The immutability guards stay closed to every other role; the two INVOKER guards allow the change only for `app_erasure` while a transaction-local flag is set, and the campaign-configuration guard only for a campaign whose `erased_at` marker (not writable by runtime roles) is set. **Foreign keys are not changed to CASCADE.**
- **Audit:** every command writes an `audit_events` row (actor, action such as `lead.erase` / `campaign.purge`, target id, counts — no personal data), which outlives the erasure.
- **Files:** stored files (step attachments, import uploads and error files) are removed only after the database change has committed, and attachment files still referenced by another (duplicated) campaign are kept. A storage failure is reported (`files_pending_cleanup`) and never undoes the committed change.
- **Bulk:** archive and unarchive accept up to 100 items per request; each item runs in its own savepoint through the same single-item service call, so bulk can never do what the single action would refuse, and one failure does not block the rest. Permanent removal is deliberately single-item.

## Consequences

- **Benefits:** meets the erasure goal without breaking reconciliation, analytics or the audit trail; keeps opt-outs enforceable; reversible steps stay reversible; the RESTRICT model and its immutability guards are preserved.
- **Trade-offs:** erased history remains as anonymised rows, which some readings of a "right to erasure" might not accept; redaction needs a per-table inventory of personal fields that must be maintained as columns are added; more moving parts than a plain `DELETE`.
- **Risks:** an incomplete redaction inventory leaves personal data behind (mitigated by a test that scans every table for the erased identifier); a purge racing a send (mitigated by the campaign lock and gate); provider token revocation can fail or be ambiguous and must be retryable; irreversibility requires typed confirmation and, ideally, a short grace period before execution.
- **Migration impact:** `0033_archive_purge_erasure.sql`: two nullable columns (`campaigns.erased_at`, `leads.erased_at`), the `app_erasure` role with its grants and policies, the commands and three additive guard changes. No table is created; existing migrations are untouched. **Prepared, not applied** — it needs the review in CLAUDE.md §7 first, and applying it is a separate decision. Until it is applied the new API routes fail with a database error and the two new response fields are simply empty; nothing else changes.
- **Documentation impact:** DATABASE, CAMPAIGN_STATE_MACHINE, USER_ROLES, SUPPRESSION and DOMAIN_MODEL now point here for archive/purge/erasure.
- **Known limitations:** commands run synchronously in one transaction (no chunked background job), which is right for typical campaign sizes but a very large campaign could take long enough to time out and would then change nothing; archiving an activated campaign relies on the send gate (a non-RUNNING campaign never sends) rather than bulk-cancelling its planned messages, which erasure does cancel; notification summaries and platform-level records are not redacted (they hold no recipient data by design); `message_attempts` provider identifiers are kept as evidence.

## Decisions taken (2026-09-28)

1. **Redaction in place** for sent history; rows, statuses, counters and provider evidence are kept. Physical deletion of sent history was not adopted.
2. **Suppressions are never deleted**; the address of an actively suppressed person is retained as the opt-out record (amended from "hashed", see above).
3. **Campaigns are duplicated, not restored.** Templates, leads and lead lists can be unarchived.
4. **No grace period**; irreversible actions are protected by archive-first rules, a server-checked typed confirmation and the ADMIN/OWNER requirement.
5. **No automatic expiry or retention job**; every erasure is user-initiated.

## Alternatives Considered

**Hard-delete everything with `ON DELETE CASCADE`** — contradicts the approved database rules, destroys unknown-send reconciliation and analytics, deletes suppression evidence, and one mistake is unrecoverable. **Archive only** — safe, but does not satisfy erasure. **Soft delete (`deleted_at`) for everything** — hides data without removing it, so it is not erasure and adds a filter every query must remember. **Per-table ad-hoc deletes in services** — duplicates the rule across API and workers, contrary to AGENTS §13; ordered database functions keep one authoritative implementation.

## Amendment: hard delete of archived campaigns and disconnected mailboxes (2026-09-29)

The project owner asked to be able to remove archived campaigns and mailboxes completely. This **reverses decision 1 (redaction in place) for these two aggregates only**: an ARCHIVED (or DRAFT) campaign and a DISCONNECTED mailbox can be physically deleted **including their sent history**. Everything else in this ADR stands: suppressions are never deleted, leads/lists/templates keep their rules, `erase` for a lead is unchanged, and foreign keys are not changed to CASCADE.

- **Mechanism:** migration `0035_campaign_mailbox_hard_delete.sql` adds `app_delete_campaign` and `app_delete_mailbox` (SECURITY DEFINER, owned by `app_erasure`, `workspace.manage` re-checked inside, audited), reusing the guard-bypass approach of 0034 (`campaigns.erased_at` marker). The API routes `/campaigns/{id}/purge` and `/mailboxes/{id}/purge` now call them; the campaign `erase` route remains but the UI no longer offers it. **Prepared, not applied**; it needs 0033 and 0034.
- **Refusals:** campaign not DRAFT/ARCHIVED; mailbox not DISCONNECTED or used by a campaign that is not DRAFT/ARCHIVED or holding an ACTIVE safety hold; any message in flight or attempt unresolved (`in_flight`); any send attempt in the last 25 hours (`recent_sends`, the rate controller's reconstruction window, so shared rate windows are not under-counted).
- **Kept:** leads, recipient addresses, all suppressions, other campaigns and mailboxes.
- **Consequences:** deleting a mailbox deletes the provider receipts and their `suppression_sources` rows (the suppression itself stays ACTIVE), and stamps `erased_at` on archived campaigns that used it (the only marker the campaign-mailbox guard honours), which drops their counts by the deleted messages. Unsubscribe links in already-sent emails stop resolving because their tokens are deleted with the messages. Notification, domain-event and audit rows that only mention the deleted ids are left in place (no recipient data). Attachment files are removed after commit, as for a purge.

## Amendment: bulk permanent delete of archived leads and lists (2026-09-30)

The project owner asked to select several archived leads or lists and delete them together. This **reverses "permanent removal is deliberately single-item" for these two aggregates only**; campaigns, templates, imports and mailboxes stay single-item.

- **Routes:** `POST /leads/bulk-erase` and `POST /lead-lists/bulk-purge`, ADMIN/OWNER only, body `{ids, confirm}`, 1 to 100 unique ids. They call the same database commands as the single routes (`app_erase_lead`, `app_purge_lead_list`), so every refusal (list used by a campaign audience, send in flight, tenant mismatch) applies per item.
- **Confirmation:** there is no single name to type, so the server compares one fixed word, `DELETE`. This is weaker than typing an email address or list name per item; it is accepted because the action is limited to items that were already archived.
- **Archived only:** a bulk erase refuses a lead that is not archived (a single erase does not require it). Lists already require it in the database command.
- **"Delete" for a lead is erasure**, unchanged: personal data is redacted, list memberships are removed, an empty record stays, and an ACTIVE suppression is kept.
- **Transaction:** one transaction per request with a savepoint per item, like the bulk archive. One refused item is reported and does not undo or block the others. Each item still writes its own audit event.
