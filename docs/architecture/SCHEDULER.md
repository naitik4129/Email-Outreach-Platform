# Scheduler

## Purpose and ownership

The scheduler discovers due PostgreSQL work and arranges short-lived execution. It implements [MVP §21](../product/MVP.md) and [SYSTEM_ARCHITECTURE §§16–19, 97, 120](SYSTEM_ARCHITECTURE.md). It invokes backend message/campaign services; it neither owns future intent nor calls email providers. See [CAMPAIGN_ENGINE](CAMPAIGN_ENGINE.md) for sequence anchors and [MESSAGE_STATE_MACHINE](MESSAGE_STATE_MACHINE.md) for dispatch transitions.

Future work consists of durable next-message intent, immutable sequence plus enrollment position, activation/planning jobs, sync checkpoints and outbox deliveries. Celery Beat may wake a bounded scheduler iteration; no day-scale ETA task is the only representation of a future email.

## Due discovery and transactional claim

Use database UTC time. Query SCHEDULED and RETRY_SCHEDULED campaign messages with `due_at <= now`, no future `blocked_until`, RUNNING campaign, planning READY and matching schedule generation. CONTROLLED_TEST messages instead require a current bounded test authorization, as defined in the message state machine; they use the same claim protocol. Order by due time and stable message ID, within bounded workspace/mailbox allocations. Use keyset traversal/partial due indexes, not offsets over millions of rows. Due discovery is approximate eligibility: the worker repeats all current gates before sending.

Candidate discovery does not lock messages and then reverse the documented lock order. For each bounded set, acquire parent gates in [engine order](CAMPAIGN_ENGINE.md), skip busy candidates, and lock message rows with `FOR UPDATE SKIP LOCKED`; re-evaluate conditions. Insert one outbox work item keyed by message ID and new dispatch generation, set QUEUED, `dispatch_origin`, `claim_expires_at`, and increment version in the same transaction. No provider attempt exists yet. Multiple schedulers can see the same candidate but only one generation commits.

Relay publishes the committed outbox through [EVENT_SYSTEM](EVENT_SYSTEM.md). QUEUED means dispatch requested, not guaranteed Redis presence. Publication failure leaves durable retryable delivery. Publish-success/local-mark failure can duplicate task transport but cannot duplicate message authorization.

## Dispatch lease and lost work

Dispatch lease bounds time until a worker authorizes an attempt, not how long an accepted provider request may run. Worker validates generation and lease. Expired QUEUED with no attempt returns to its recorded origin, increments generation and recomputes due time; stale outbox deliveries are superseded. Old workers/tasks cannot authorize the new generation.

A QUEUED record whose published task disappeared during Redis restart is recovered the same way. Periodic reconciliation searches expired claims and eligible durable jobs even if outbox says published. A SENDING record is never reset by this sweep: expired attempt ownership becomes UNKNOWN_OUTCOME and goes to reconciliation. No heartbeat absence proves a provider did not accept.

## Time and schedule calculation

Persist UTC instants and explicit IANA timezone, weekday set, local start/end, optional future campaign start, schedule revision and computation version. Initial proposed product policy supports one same-day window with start < end, interpreted half-open `[start,end)`; overnight schedules need explicit additional product support. An empty weekday set or no usable window is invalid.

Convert sequence lower bound into the earliest permitted UTC instant. Wait days are elapsed 24-hour durations as defined in engine. Proposed DST rule: a nonexistent local boundary shifts forward to the first valid local instant; an ambiguous start chooses the later occurrence and ambiguous end the earlier occurrence, conservatively shortening the window. If resulting end <= start, skip that date. Save resolved UTC bounds and test actual transition dates. The worker recomputes current eligibility; a stale conversion never forces a send outside the approved window.

Overdue work after pause/outage resumes in the current or next allowed window at available capacity; never replay missed windows as a burst. Persist scheduling jitter/allocation outcome once per generation. On schedule edit, campaign generation invalidates queued work immediately; recompute unsent messages in batches against preserved sequence anchors. Message send attempts/content/history are immutable. A tzdata upgrade requires bounded recomputation of future work and audit of computation version; it cannot rewrite historical due times.

## Fairness and backpressure

Use bounded per-workspace/campaign/mailbox batches with round-robin workspace cursors and oldest-due ordering within each slice. Rotate fairness cursor durably or tolerate reset as a throughput concern; correctness stays in message state. Limit outstanding QUEUED per scope so one mailbox backlog cannot fill the broker. Rate service returns a next-capacity time and reason; scheduler does not consume sending allowance during discovery. Hold unsendable work with a bounded recheck time to avoid hot loops.

Scheduler initially runs one desired replica for simplicity, but claims must pass two-scheduler tests. Pool sizes and batch sizes must be bounded against total API/worker database capacity. Queue age, database latency and publication backlog drive backpressure; increasing workers cannot exceed provider allowances.

## Failure and recovery matrix

| Scenario | Required result |
|---|---|
| Crash before claim commit | No state change; candidate rediscovered. |
| Commit succeeds, Redis publish fails | Outbox remains pending/retryable; no lost message. |
| Publish succeeds, acknowledgment persistence fails | Possible duplicate transport; generation and state guard prevent duplicate attempt. |
| Task lost / Redis emptied | Expired QUEUED sweep republishes a new generation; rate limiter must first recover safely. |
| Database temporarily unavailable | Stop iteration, backoff; no speculative claims or external sends. |
| Worker/provider timeout | Scheduler does not retry the send; message reconciler owns unknown result. |
| Scheduler/deployment restart | Resume durable jobs, due queries and expiration sweep; no memory reconstruction. |
| Concurrent pause/edit/start | Parent gate/version checks reject stale eligibility; worker repeats check. |
| Long provider outage | Cooldowns/holds retain future work; no repeated immediate dispatch loops. |

## Security and observability

Scheduler uses a narrowly authorized system discovery role returning workspace/resource IDs, not arbitrary tenant content or credentials. Each mutation enters a workspace-scoped worker transaction. Ordinary API requests never use global discovery credentials. No task carries lead fields or secret material.

Measure discovery lag, oldest due message, outbox age, expired claims, invalidated generations, per-scope queue occupancy, skipped locks and iteration/database latency. Alert on growing durable backlog even when Redis depth is zero. Log scheduler iteration, workspace/campaign/message IDs, dispatch generation and deferral reason.

## Open Decisions, tests and definition of done

Approve window/DST/elapsed-wait UX, exact batch/lease/polling thresholds and fairness objectives. Recommended defaults above make behavior reviewable; no numerical service level is claimed as approved. Beat versus a dedicated polling process remains an operational choice with the same database protocol.

Implementation must prove multi-scheduler claims, publication crash windows, complete Redis loss, queue task lost after publication, worker crash before/after authorization, stale generation, schedule edits during dispatch, overdue fairness, DST folds/gaps, midnight/window boundaries, clock skew and cross-tenant discovery/mutation separation. Release requires due-query plan review at expected volume and verified recovery without duplicate external sends. Related: [queues](QUEUES.md), [rate limiting](RATE_LIMITING.md), [database](../database/DATABASE.md).
