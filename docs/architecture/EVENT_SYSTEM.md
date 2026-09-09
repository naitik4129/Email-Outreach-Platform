# Event System

## Purpose, scope and terminology

The events module normalizes external evidence, records business facts and reliably transfers committed work to asynchronous consumers. It refines [SYSTEM_ARCHITECTURE §§48–50, 57, 120–121](SYSTEM_ARCHITECTURE.md). It is not universal event sourcing, Kafka infrastructure, or a replacement for campaign/message tables.

| Kind | Authority / use |
|---|---|
| Provider event | Untrusted external evidence until authenticated, bounded, normalized and associated. |
| Domain event | Immutable committed business fact such as message.accepted or recipient.stopped. |
| Operational event | Logs/metrics/traces; useful diagnostics but not durable business truth. |
| Audit event | Immutable actor/action/reason evidence for sensitive commands, transactionally coupled where required. |
| Analytics event/projection | Derived reporting fact; deduplicated and rebuildable, never grants send eligibility. |

Database state and domain event/outbox commit together. A provider timeout is not fabricated as message.failed. Provider delivery/open/reply events do not rewind execution status.

## Internal outbox and delivery protocol

1. Business transaction mutates authoritative state and inserts a domain event where meaningful, an outbox work row, and one delivery row per required consumer. IDs and semantic business keys are unique. A work row may be a command to continue planning without pretending it is a historical domain fact.
2. Relay claims due delivery rows under short database leases with monotonically increasing lease generation, commits, then publishes stable work/resource IDs outside the transaction.
3. Relay records PUBLISHED observation conditionally on its lease. If publish fails, persist RETRY with `next_attempt_at`, safe error and attempt count. If process crashes after publication but before mark, publish may repeat.
4. Consumer loads durable work and starts a transaction. Lock/insert consumer receipt keyed `(consumer, work_id, effect_version)`, validate current state, apply the effect and mark receipt/delivery COMPLETED atomically. A duplicate observes completion and has no business effect.
5. Long work such as imports commits bounded chunk state and creates a durable continuation; the receipt marks the current chunk, not the entire job prematurely. Sending hands off to the dedicated durable message attempt protocol; the generic event transaction never encompasses provider I/O.

Delivery states: PENDING → LEASED → PUBLISHED → COMPLETED; LEASED/PUBLISHED may return RETRY after timeout only when durable consumer/resource state proves work still requires execution. RETRY → LEASED. Superseded message generations become SUPERSEDED. Invalid permanent work becomes FAILED with diagnostic/replay metadata. Message UNKNOWN_OUTCOME gets reconciliation work, never a new send dispatch.

PUBLISHED is not proof of consumption. A periodic sweep examines outstanding delivery receipts and durable resource progress, including published work lost during Redis restart. Requeueing retains `work_id` and consumer identity; message re-dispatch may instead create a new dispatch generation per scheduler. Outbox records are not deleted immediately on publication.

## External inbox/receipt protocol

Webhook HTTP handler bounds request size, validates provider signature/token/challenge according to adapter, resolves connection from server-owned subscription/account identity, and validates minimally required fields. Never trust supplied `workspace_id`. Return success only after durable receipt and processing work commit. A duplicate with the same identity can return success after confirming durable prior receipt. Database failure returns retryable failure, not false acknowledgment. Verification challenges follow provider handshake rules and do not imply a business event.

Receipt uniqueness uses provider + account/subscription scope + provider event ID. If no stable event ID exists, use a documented canonical fingerprint including mailbox, event type, provider message identity and stable occurrence identity; payload byte hashing alone cannot distinguish reserialized duplicates. If reliable identity is impossible, retain separate receipts but deduplicate the eventual semantic effect (for example one complaint fact for a message/source). Repeated opens may legitimately be different observations and are never treated as strong safety evidence.

Receipt states: RECEIVED → PROCESSING → PROCESSED / RETRY / FAILED; lease expiry returns uncompleted processing to RETRY. Unsupported schema is quarantined visibly; authentication failure is rejected before trust. Failed receipt retains safe error, source identity, timestamps, payload version and recovery action.

For validated safety events whose mailbox is known, receipt ingestion also increments a pending-safety counter/hold on that mailbox under its gate. Send authorization refuses work while this counter is nonzero. Processing decrements the counter only in the transaction that commits normalized safety effects or explicitly resolves a non-safety receipt. Duplicate receipts do not increment twice. If only a workspace can be resolved, use the workspace gate/hold instead. Unknown unauthenticated sources cannot impose tenant holds. This closes the gap between acknowledging known safety evidence and asynchronous processing.

## Normalization and business effect

Provider adapter yields bounded typed evidence with occurred_at, observed_at, provider identifiers, event category and evidence confidence. Backend service resolves workspace/mailbox/message through constrained links, then atomically writes normalized event, changes recipient/suppression/message evidence, writes audit where needed, and creates downstream outbox work. Inbox receipt completion is in that same transaction. Duplicates across sync and webhook may share one inbound-message or semantic event key even when their receipt IDs differ.

Unsubscribe endpoint is special: validate the recipient token and commit suppression directly before returning “unsubscribed.” Analytics/notifications fan out later. A provider “notification” that only signals mailbox changes schedules sync; it is not itself a reply. See [REPLY_SYNC](REPLY_SYNC.md).

## Ordering and late events

No global order is promised. Store source occurred_at separately from database observed/processed times. Aggregate version provides local transition ordering; consumers reload authoritative state, not a stale event snapshot. Reply/complaint/hard-bounce facts monotonically prohibit outreach until a separate approved release command; a delayed “delivered” event cannot clear them.

A reply may arrive before the send-result transaction commits. Preserve unmatched evidence and run matching again when outbound identifiers become known; positive reply evidence can assist attempt reconciliation only through the provider/message evidence rules. Duplicate late events after archive update history/suppression without reopening a campaign. Provider soft-bounce after acceptance does not cause platform resubmission of the accepted email.

## Retention and privacy

Domain events retain minimal IDs, type/version, safe reason and necessary timestamps. Do not copy complete lead/contact/body snapshots into every event. Raw payload retention is restricted, encrypted and time-limited by approved policy; remove token/authorization fields before storage. Prefer object references for large evidence, with immutable object version and content digest; metadata remains in PostgreSQL. Payload expiration must leave sufficient dedupe identity and normalized safety facts to reject replay.

Consumer receipts/outbox delivery retention must cover replay horizon, broker redelivery lifetime and operational restore procedures. Never purge dedupe tombstones while an old effect can still be replayed. Exact durations and raw-payload permissions are Open Decisions. Analytics may initially derive counts from unique message/recipient/outcome records. Attempt count and accepted-message count have different denominators; never equate SENT to DELIVERED or count two replies by one recipient as two replied leads.

## Failures, recovery and observability

| Scenario | Recovery |
|---|---|
| Database commit then queue failure | Durable outbox retry; no lost work. |
| Relay crashes after publish | Duplicate delivery; consumer transaction deduplicates. |
| Worker crashes mid-effect | Rollback includes consumer marker; replay safely. |
| Consumer commits then acknowledgment lost | Receipt proves effect; acknowledge duplicate. |
| Redis flush / task lost | Published-but-uncompleted sweep republishes from durable state. |
| Provider/webhook outage | Provider retry where available plus polling/cursor reconciliation; do not claim all providers guarantee redelivery. |
| Database temporary outage | No successful receipt acknowledgment/new effects; bounded backoff. |
| Deployment/schema mismatch | Compatible event versions during rollout; unknown versions quarantine/alert. |
| Concurrent/out-of-order events | Semantic unique keys, aggregate locks/versions and monotonic safety rules. |
| Stale unknown-send event | Evidence service decides; generic handler cannot turn it into safe retry. |

Replay is a controlled command scoped to receipt/work ID, consumer and expected version; audited operator capability is required. Replay cannot bypass suppression or message lifecycle. Log receipt/event/work/delivery/consumer IDs and safe processing status; measure oldest pending safety receipt, outbox publication/completion lag, duplicate rate, poison work, hold duration and consumer failure. Alert on safety backlog even when analytics is healthy.

## Open Decisions, tests and definition of done

Approve retry/replay/retention horizons, exact provider verification contracts and latency SLOs. External customer webhooks are later scope; this architecture does not add endpoint subscriptions as a product feature. Outbox/inbox protocol is the technical design selected here, not an ADR falsely claiming it was previously frozen.

Test all commit/publish/ack crash windows, complete Redis loss after PUBLISHED, consumer-marker atomicity, duplicate webhook versus sync observation, reordered safety/delivery events, missing/forged signature, cross-tenant provider identifiers, repeated unsubscribe, pending-safety holds and poison receipt recovery. Prove no duplicate analytics counts and no event-triggered resend of accepted/unknown messages. Related: [queues](QUEUES.md), [suppression](SUPPRESSION.md), [database](../database/DATABASE.md), [security](../security/SECURITY_ARCHITECTURE.md).
