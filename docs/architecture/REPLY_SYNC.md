# Reply Synchronization

## Purpose and ownership

The backend conversations/mailbox services turn provider mailbox changes into normalized inbound messages, defensible outreach associations and recipient stopping effects. Worker-sync supplies runtime execution. This fulfills [MVP §§25–26](../product/MVP.md), [existing UF-25–UF-26](../product/USER_FLOWS.md) and [SYSTEM_ARCHITECTURE §§47, 56](SYSTEM_ARCHITECTURE.md). It does not implement a complete mail client, send automatic replies or infer campaign attribution from subject similarity.

## Provider sync contract

| Provider | Durable discovery model | Recovery and limitations |
|---|---|---|
| Gmail | Initial bounded full synchronization followed by history cursor; optional provider notifications wake sync. | Invalid/out-of-range history cursor requires full resync; notifications are hints, not complete reply payloads. |
| Microsoft | Folder-scoped delta traversal with opaque continuation/delta links; poll and optionally wake on verified notification. | Track folders explicitly, preserve opaque checkpoints, handle invalid cursor/resync and moved message identity. |
| SMTP | No inbound capability from SMTP itself. | Separate supported IMAP/API configuration, tested identifiers/cursor semantics and credentials are required. Never claim reply support from an SMTP test. |

Gmail's history recovery and Graph's folder-specific delta model are documented in [Gmail sync](https://developers.google.com/workspace/gmail/api/guides/sync) and [Graph message delta](https://learn.microsoft.com/en-us/graph/delta-query-messages), checked 2026-09-09. Provider implementation must verify current scope, subscription renewal and identifier behavior before enabling it. These protocol facts do not freeze a commercial provider limit.

## Checkpoints, scheduling and transactions

Persist mailbox/account generation, provider/folder scope, cursor, pending page continuation, lease owner/generation/expiry, last successful complete sync, next_due_at, failure count, sync status and optional subscription expiry. One owner per mailbox/folder; compare expected checkpoint version before commit. Periodic due discovery exists even when push notifications are enabled, so missed notification does not permanently lose replies.

Fetch one bounded page outside a database transaction. Validate source identity and normalize MIME/headers with size/depth limits. In a transaction under appropriate recipient/mailbox gates, upsert inbound records, record matching evidence, stop applicable enrollments, create/update conversations/events/outbox and advance the page checkpoint. Page cursor never advances before all its effects are durable. If bounded matching cannot finish in that transaction, persist unresolved inbox work and a mailbox pending-safety hold with the cursor; release hold only after durable processing completes.

End-of-scan checkpoint and `last_successful_sync_at` advance only after the entire incremental traversal finishes; one fetched page is not proof the mailbox is current. Database rollback causes page replay, protected by unique mailbox/provider-message identity. Process crash, Redis flush and scheduler restart resume from PostgreSQL. Lease generation prevents a late worker from overwriting a newer cursor. Never hold a transaction during provider paging.

Initial/full resync scans a documented relevant horizon, collecting headers/IDs before downloading unnecessary bodies. The horizon must cover active/retained outreach that can still receive replies; its exact duration is an Open Decision. A gap that cannot be covered is exposed as incomplete sync and holds follow-up sending. Reconnect to the same account increments generation, validates scopes and runs a catch-up/resync; reconnect to a different account must create a separate mailbox identity.

For IMAP, identity must include mailbox/folder identity, UIDVALIDITY and UID rather than a sequence number. UIDVALIDITY change invalidates the cursor and triggers deduplicated rescan. See the protocol's identifier semantics in [RFC 9051](https://www.rfc-editor.org/rfc/rfc9051.html), checked 2026-09-09. Supported move/duplicate semantics must be proven by adapter tests before claiming parity with API providers.

## Normalized message and association

Persist provider message/thread IDs scoped to mailbox, RFC Message-ID, In-Reply-To/References, normalized participants, subject, received/observed timestamps, direction, safe content or restricted content reference, automatic/DSN indicators, association status and evidence. Keep provider IDs distinct from RFC headers. Header claims are untrusted and bounded. A conversation can span several contexts; message links carry explicit campaign/enrollment attribution.

Matching order:

1. Verify mailbox/account/workspace and inbound direction; exclude own sent copies and obvious provider metadata notifications.
2. Match In-Reply-To/References against known outbound identifiers in that mailbox/workspace, then corroborate sender/participants and compatible thread context.
3. Provider thread/conversation identifiers may strengthen a unique candidate with corroborating participant/outbound context, but do not prove a campaign association alone.
4. If several campaigns/messages remain plausible or expected participants conflict, preserve UNRESOLVED with candidates/evidence restricted to the same workspace. No guessed campaign_id from subject or address alone.

Forwarded replies, aliases and wrong-person responses require explicit matching policy; do not broaden matching silently. Matching may be retried when the original send result/identifiers arrive later. Upgrading an unresolved record to matched happens once under version check and applies previously unapplied stop/event effects atomically.

## Stopping, classification and conversations

A confidently recognized reply stops the matched ACTIVE enrollment, records reply fact/time and invalidates not-yet-authorized follow-ups in the same transaction. No analytics or AI classification must complete first. If enrollment already terminal, add outcome facts without reopening it. SENDING/UNKNOWN attempts retain evidence reconciliation and the bounded in-flight semantics of [MESSAGE_STATE_MACHINE](MESSAGE_STATE_MACHINE.md).

Proposed conservative default: recognized human, automatic and out-of-office responses all stop the matched sequence; no automatic restart after an OOO date. Delivery-status notifications are routed to bounce processing, not counted as human replies. Suspicious/ambiguous evidence imposes a candidate-specific or mailbox hold for review rather than false attribution. Separate unsubscribe/complaint signals call suppression for workspace-wide prohibition. Reply alone stops the matched enrollment, not every campaign for that lead. Changing that product scope requires approval.

Conversation identity uses mailbox/provider thread when reliable, otherwise a local conversation anchored to known outbound context or unresolved inbound ID. Unique keys prevent duplicate conversation creation. Matching a previously unresolved conversation must merge/link references transactionally without duplicating inbound records or deleting history. Inbox read/unread state is per user if enabled, not a provider cursor. MVP offers conversation review; compose/forward/AI classification remain unapproved extensions.

## Freshness and failures

Track sync freshness independently of connection health. Proposed safe default: multi-step campaigns require working reply-sync capability; follow-up authorization holds when sync is stale, incomplete or pending safety work exists. No system can guarantee detection of a reply that has not yet become observable upstream. The promise is immediate stopping once recognition commits, plus a reviewed freshness budget to reduce exposure.

| Failure | Behavior |
|---|---|
| Provider timeout/outage | Preserve cursor, backoff respecting API quota; mark LAGGING and hold follow-ups under policy. |
| Expired cursor / reconnect | RESYNC_REQUIRED; deduplicated catch-up before CURRENT. |
| Worker crash / duplicate task | Lease/checkpoint and inbound/effect unique keys prevent duplication. |
| Redis loss / task loss | Due sync records and pending receipt sweep rebuild tasks. |
| Database temporary failure | Do not advance cursor or acknowledge effects; replay page. |
| Deployment / concurrent cursor owner | Schema/generation guards; stale result cannot overwrite current checkpoint. |
| Reply before send-result commit | Preserve unresolved evidence and retry association when identifiers arrive. |
| Reply races with send/pause | Shared enrollment/safety gate serializes authorization; never change sent history. |

## Security, observability and tests

Decrypt read credentials only in allowed sync runtime. Restrict raw inbound content; sanitize HTML and disable remote image loading in product previews. Validate opaque provider continuation URLs against known provider origin to prevent turning cursor fetches into SSRF. Do not log subjects, participants, bodies, cursor tokens or full provider payloads. Tenant-scoped queries prevent cross-account identifier collisions from crossing workspaces.

Measure oldest unsynced mailbox, checkpoint lag, invalid cursor count, unresolved matching age, duplicate observations, pending-safety holds and time from observed reply to committed stop. Alert on stale synchronization during active campaigns. User-facing mailbox state explains reconnect/resync/review action.

Tests must cover duplicate pages/webhooks, crash before/after checkpoint, lost notification, expired Gmail history, Graph folder traversal/moves, IMAP UIDVALIDITY reset, sent-copy exclusion, forged headers, shared subject across campaigns, aliases, ambiguous thread, reply before acceptance commit, late reply to completed campaign, cross-tenant ID collisions, HTML payloads and stale-sync follow-up holds. No live customer inbox is a test fixture.

## Open Decisions and definition of done

Approve exact reply recognition confidence rules, OOO/automatic-response policy, sync horizon/freshness SLO, SMTP receive compatibility, and single-step send-only exception. Recommended fail-closed follow-up behavior may reduce sending availability; it avoids pretending reply stopping works on an unsynchronized mailbox. Related: [events](EVENT_SYSTEM.md), [suppression](SUPPRESSION.md), [providers](PROVIDER_ARCHITECTURE.md), [database](../database/DATABASE.md).
