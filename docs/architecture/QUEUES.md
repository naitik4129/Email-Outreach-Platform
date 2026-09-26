# Queues

## Purpose and invariant

Queues isolate operational workload classes; PostgreSQL owns work identity, progress, retry and terminal failure. This refines [SYSTEM_ARCHITECTURE §§124–128](SYSTEM_ARCHITECTURE.md) and [WORKERS](WORKERS.md). Redis/Celery transport is at least once in design and may lose transient work; recovery must not depend on broker contents or result history.

## MVP routing design

Names below are the proposed concrete routing contract for implementation review, refining the parent document's examples. Latency is a relative objective; numerical SLOs require approval/load measurement.

| Queue | Responsibility / producer | Consumer | Latency and concurrency | Retry, failure, scaling and starvation |
|---|---|---|---|---|
| `email.send` | First and safe retry dispatch; scheduler through outbox | worker-send | Timely within allowed window; low prefetch, per-message exclusive attempt and mailbox capacity. | Message policy only; unknown outcomes reconcile. Scale within limits; scheduler caps per-tenant outstanding work. |
| `mailbox.sync` | Due sync or verified notification hint; durable sync scheduler/event processor | worker-sync | Safety-sensitive freshness; one cursor lease per mailbox/folder. | Bounded read retry/checkpoint replay; separate from imports; provider-aware fairness. |
| `webhooks` | Durable verified external receipt | Reserved worker-general safety consumer | Highest operational urgency for reply/bounce/complaint safety; bounded tasks. | Inbox dedupe; poison receipt visible and mailbox safety hold where appropriate. Dedicated minimum slots. |
| `campaign.plan` | Audience capture, activation planning, next-message rendering (including follow-up progression, `campaigns.advance_enrollments_chunk`, see [ADR-0009](../adr/0009-sequence-progression-sweeper.md)); campaign service/outbox | worker-general planning consumer | Before planned send; bounded chunks. | Durable cursor/unique keys; separate quota so one campaign cannot monopolize safety capacity. |
| `imports` | Validated immutable upload job; leads service/outbox | worker-general import consumer | Background latency acceptable; streaming chunk/memory bound. | Row-level idempotency; failed job in PostgreSQL; split worker-import only after measured need. |
| `notifications` | Operational condition projection and optional platform delivery | worker-general notification consumer | Behind safety work; recipient/channel dedupe. | Separate delivery budget; failures do not revert business state. |
| `personalization` (implemented, flag-gated, [ADR-0011](../adr/0011-hyper-personalized-campaign-type.md)) | `personalization.generate_chunk` / `personalization.generate_previews`; scheduler generation sweep, preview API (ids only) | worker-personalization | Before the intended `due_at` (lead time); low concurrency, external LLM/fetch I/O outside any transaction. | Durable `message_generations` lease and attempt counters; separate from `email.send` and `campaign.plan` so a slow LLM cannot starve sends, planning or safety consumers. Only active when `PERSONALIZATION_ENABLED=true`. |
| `maintenance` | Reconciliation lookups, expired claim recovery, outbox support, completion sweeps, small analytics jobs | Scoped general/sync entrypoints by task type | Regular bounded progress; reserve reconciliation capacity. | Expected-version updates; unknown-send lookup never invokes send. Destructive retention disabled pending approval. |

Scheduler/relay may also run as dedicated polling entrypoints; their progress cannot depend exclusively on the queue they are repairing. They must be able to recover an empty broker. Read-only reconciliation using mailbox credentials runs in the sync group even if triggered by maintenance work.

No separate `email.retry` is needed initially: due retry intent follows the same `email.send` safety path, with fairness between old and new eligible work. No separate `reply.sync` duplicates mailbox sync ownership. Verification and advanced analytics queues are deferred with those features (the `personalization` queue is proposed by [ADR-0011](../adr/0011-hyper-personalized-campaign-type.md)); basic analytics can read durable tables or run bounded maintenance jobs. Queue names do not imply separate services.

## Envelope and publication

Envelope contains `schema_version`, `work_id`, `resource_id`, `workspace_id`, optional `dispatch_generation`, `correlation_id`. Use explicit registered task names, bounded primitive values and no arbitrary executable arguments. No credentials, complete campaigns, bodies or CSV contents. Resource IDs are reloaded and authorized within the trusted system capability; client-supplied task envelopes are never accepted as authority.

Business transaction + outbox work + per-destination delivery is atomic. Relay leases delivery, publishes outside transaction, then records broker-publication observation. “Published” is not business completion. A consumer commits its effect/deduplication marker before acknowledgment. Publisher crash can duplicate transport; lost tasks are rediscovered from durable work after lease/receipt deadline. See [EVENT_SYSTEM](EVENT_SYSTEM.md) for exact lifecycle.

## Backpressure and recovery

Cap outbox relay batches and per-queue outstanding deliveries. Long jobs split into bounded chunks and reenqueue by durable continuation. Do not sleep in a worker for a day-scale retry; persist due time. Avoid numerical priority as the sole safety isolation mechanism. High sending backlog cannot consume reply/webhook consumers or all database connections.

Redis outage pauses publish/consume; durable work accumulates. On restart, rebuild rate limiter first for sending, then resume relay and expired-claim sweeps. Duplicate messages are expected. Database outage stops mutation and new send authorization. Provider outage creates cooldowns rather than transport retry storms. Deployment supports current and previous envelope version during rolling rollout; unknown versions become visible rejected work, not lost silently. Stale tasks no-op only after durable state confirms no effect is required.

Terminal domain failures remain on message/job/receipt records with safe error class, retry count, timestamps and correlation. There is no reliance on a Redis dead-letter queue as the sole record. Operator replay is an explicit permission-checked command against durable state and cannot resend SENT/UNKNOWN_OUTCOME messages.

## Security, observability and validation

Private broker network, authentication, restricted runtime credentials and safe serialization are required. Producer/consumer allowlists enforce routing. Observe queue age/depth, oldest durable work, publication lag, durable completion latency, duplicate deliveries, consumer starvation and poison work. No tenant/recipient email values as metric labels.

Test every producer→queue→consumer route, missing/unsupported schema, credential-free payloads, broker flush, publish-success/mark-failure, acknowledged-but-uncompleted work recovery, competing tenants, import starvation, deployment version overlap and repeated operator replay. Define concrete latency/concurrency limits before production. Related: [scheduler](SCHEDULER.md), [rate limiting](RATE_LIMITING.md), [database](../database/DATABASE.md).
