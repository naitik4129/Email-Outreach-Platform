# Workers

## Purpose and ownership

Workers are independent Python/Celery runtime entrypoints into one authoritative backend application package. They execute the asynchronous MVP work in [MVP](../product/MVP.md) and [SYSTEM_ARCHITECTURE §§13–15](SYSTEM_ARCHITECTURE.md). They do not implement their own campaign, suppression, permission or provider policies. Their job is validate task envelope, establish trusted system context, load durable state, invoke service, persist result, and acknowledge.

## Runtime groups and entrypoints

| Group | Entrypoint responsibilities | Resources and isolation |
|---|---|---|
| worker-send | Dispatch one authorized message attempt; safe send retry uses the same service. | Bounded provider I/O; low prefetch; credential access; independently scalable within rate limits. |
| worker-sync | Mailbox page synchronization, normalization/matching and reconciliation lookups. | Bounded page size, one cursor owner per mailbox/folder, read-capability credentials. |
| worker-general | Separate consumers for safety webhooks, import chunks, planning, notifications and maintenance. | Reserve capacity for safety events; imports must not occupy all general slots. |
| scheduler | Bounded durable due discovery and claim/recovery iteration. | Small database pool; no provider credentials. One initial replica, concurrency-safe protocol. |
| outbox relay | Bounded delivery leasing and broker publication. May be a specialized general entrypoint. | Broker publish access plus scoped delivery metadata; no email credentials/content. |

One codebase can run several process invocations with different queue selections. `worker-general` is not a license for a single FIFO where a large import blocks unsubscribe processing. Dedicated safety-consumer capacity is required even if it uses the same image. Later worker-import/analytics groups split measured workloads; worker-ai, verification and personalization remain later capabilities and have no MVP queues or secrets.

## Durable task contract

Tasks carry schema version, task/work ID, resource ID, workspace ID, generation where relevant, correlation ID and bounded transport metadata. Workspace ID is a routing hint, not authority: validate against the durable resource and allowed service capability. Backend application service owns the transaction. Every task has an explicit durable completion/retry/reconciliation status; Celery result backend is optional operational tooling, not product truth.

Use non-owner, non-BYPASSRLS database identities; transaction-local workspace and system capability are established for each job and cleared by transaction completion. Avoid session-wide tenant state across pooled connections. Close database sessions between bounded units and external I/O. Worker pool size times replicas must fit the shared database connection budget.

## Acknowledgements and retries

Configure late acknowledgment for idempotent work only; acknowledgment occurs after durable completion, durable deferral or durable unknown-outcome handoff. Celery's worker-loss and acknowledgment behavior must be tested against the selected version: late acknowledgment alone does not guarantee redelivery after every process loss. See [Celery task guidance](https://docs.celeryq.dev/en/stable/userguide/tasks.html), checked 2026-09-09. PostgreSQL reconciliation provides recovery even if transport acknowledges/loss occurs unexpectedly.

Never wrap send tasks in generic `autoretry_for=Exception` that can repeat provider submission. [MESSAGE_STATE_MACHINE](MESSAGE_STATE_MACHINE.md) owns send retries. Other retriable work uses bounded durable attempt counts/backoff; broker redelivery does not reset the budget. Short transport retries may wake an existing durable job, but future work is persisted in PostgreSQL. Malformed/unsupported task versions become operational incidents with safe metadata and cannot invoke a provider.

## Execution and shutdown

Each task declares maximum wall time, connect/read timeouts for external calls, bounded input/page/chunk sizes, memory budget and retry policy. Soft timeout should allow persistence/cleanup; hard timeout is a last resort and creates unknown send outcome when invocation may have occurred. Provider deadline must expire before shutdown grace/hard kill budget. Graceful shutdown stops fetching new work, drains bounded tasks and commits progress. Forced kill relies on durable recovery, not a cleanup handler assumed to always run.

Never share ORM sessions or provider clients unsafely across process forks. Instantiate process-local pools/clients at worker initialization. Recycle measured leaking/large-memory processes only between tasks. CSV processing streams bounded rows; planning chunks must not deserialize an entire audience. Analytics and notification failures are isolated from safety transitions.

## Retry-safe work by subsystem

| Work | Durable unit / duplicate protection | Failure recovery |
|---|---|---|
| Planning | Activation + cursor; unique enrollment/step | Replay committed checkpoint. |
| Sending | Message dispatch generation + attempt | Unattempted claim recovery or unknown reconciliation, never automatic resend. |
| Mailbox sync | Mailbox/folder generation + page/checkpoint | Replay page and deduplicate inbound IDs before cursor advance. |
| Provider event | Provider receipt ID + consumer effect key | Apply state/event/dedupe marker atomically. |
| Import | Object version/mapping + row ordinal + chunk checkpoint | Replay rows; no duplicate leads or overwritten contacts. |
| Notification | Source event + recipient/channel | In-app upsert; external delivery separately tracked, uncertain delivery does not alter campaign. |
| Maintenance | Resource ID + expected version + operation | Recheck current state, process bounded batch. No unapproved destructive retention job. |
| Analytics | Domain event ID + aggregate consumer | Deduplicate or recompute from durable facts; never blocks sends. |

## Failure, security and operations

Process crash, deployment and duplicate task are normal recovery inputs. Redis restart triggers durable rediscovery and rate recovery. Database outage prevents any new external send authorization; retry database access with bounded backoff. Provider outage uses persisted cooldowns. Scheduler restart has no effect on durable job identity. Stale commands/tasks are denied by current state/generation. Repeated poison jobs reach a visible durable failed/needs-attention state rather than endless broker loops.

Task producers and broker access are private authenticated services. Accept JSON-compatible validated envelopes only, not executable serialization from untrusted producers. Least-privilege credentials differ by runtime group. No OAuth tokens, SMTP passwords, bodies or upload contents in queue payloads or error logs. Privileged maintenance/operator actions require an auditable capability distinct from workspace user roles.

Monitor queue age/depth, durable job age, task latency, process memory/restarts, database pool utilization, provider latency, unknown attempts and dead work. Correlate task/job/workspace/message/attempt IDs with deployment version. Readiness depends on required dependencies; liveness should not trigger restart storms on a provider outage. Scaling follows queue age within rate/database limits, with minimum safety/sync capacity.

## Open Decisions, tests and definition of done

Approve exact concurrency, prefetch, time budgets, shutdown grace and retry counts after load/failure tests; numbers must be compatible with provider deadlines and broker visibility settings. Deployment runtime conflict (older EC2/Compose recommendation versus newer ECS/Fargate recommendation) remains an infrastructure approval item; these worker boundaries support both.

Tests must cover forced process loss with real broker semantics, duplicate delivery, task loss despite publication, complete Redis restart, database outage, unsupported version during rolling deployment, starvation by import load, concurrent sync cursor updates, secret redaction and cross-tenant task injection. Provider fakes must detect accidental second invocation. Operational launch requires runbooks for stuck jobs, unsafe retries, backlog and credential failure. Related: [queues](QUEUES.md), [scheduler](SCHEDULER.md), [events](EVENT_SYSTEM.md), [security](../security/SECURITY_ARCHITECTURE.md).
