# Follow-up progression as a level-triggered sweeper

## Status

Accepted for implementation — 2026-09-25. Deviates in one point from the wording of [CAMPAIGN_ENGINE](../architecture/CAMPAIGN_ENGINE.md) ("the send result service calls recipient progression transactionally"); that document is updated to match. Shipped behind `SEQUENCE_PROGRESSION_ENABLED` (default off).

## Context

The documented model creates each next email from the previous email's provider-acceptance time plus the sequence's wait, projected into the sending window, and only the next email is ever materialized ([CAMPAIGN_ENGINE](../architecture/CAMPAIGN_ENGINE.md), [ADR-0007](0007-durable-message-scheduling.md)). Before this decision nothing implemented it: waits were validated and stored but never read, so a campaign sent step 1 only.

"Acceptance" is recorded by three independent paths — the send worker, reconciliation of `UNKNOWN_OUTCOME`, and reply/provider sync — and only `app_worker_general` holds the column grants needed to insert the next message and move the enrollment pointer. `app_worker_send` (which runs the send-result path) has neither `INSERT` on `messages` nor `UPDATE` on `campaign_enrollments`.

## Decision

Add a level-triggered progression sweeper that runs as `app_worker_general`:

- The scheduler (which can read `campaigns` cross-tenant but not enrollments) queues one `campaigns.advance_enrollments_chunk` task per RUNNING/READY campaign on its own, slower interval, walking campaigns in id order across runs.
- The task selects ACTIVE enrollments whose current message (the one `next_step_id` still points at) is `SENT` or `FAILED`, `FOR UPDATE OF e SKIP LOCKED`, in bounded keyset chunks.
- Per enrollment, in one transaction: compute the next email and the sum of the waits before it (pure function), project `accepted_at + wait` into the campaign window, insert the next message idempotently (unique `(enrollment, step)` key), render it with the same renderer and frozen variables, and move the pointer (guarded on the step it expected to still point at). No later email: `COMPLETED`. Current email `FAILED`: enrollment `FAILED`.
- Pause, reply, unsubscribe and suppression need no special cases: a campaign that is not RUNNING is not selected, a STOPPED enrollment is not ACTIVE, and the send gates remain the authoritative check when a scheduled follow-up comes due.

## Alternatives Considered

**Progress inside the send-result transaction (as the engine text says).** Rejected: it would require widening `app_worker_send` grants on `messages` and `campaign_enrollments`, and would miss acceptances recorded by reconciliation and sync unless each path duplicated the logic (violating one-owner-per-rule).

**Domain event → consumer.** Rejected for now: the outbox/event pipeline is not authoritative for acceptance, and a lossy event must not decide progression. A level-triggered query over persisted state cannot miss an acceptance; a missed run only delays progression.

**Cross-tenant enrollment discovery by the scheduler.** Rejected: `app_scheduler` deliberately has only workspace-scoped enrollment policies; widening them is a security change, so the scheduler only names campaigns.

## Consequences

- No migration and no grant change: the required privileges already existed for `app_worker_general`.
- Cost: one cheap indexed task per RUNNING campaign per interval, plus keyset chunking inside a campaign. A campaign-level "has pending work" signal visible to the scheduler is a future optimization.
- Turning the flag on makes already-running campaigns start sending their step 2 (for enrollments whose step 1 was accepted earlier). Enable it deliberately.
- After a pause, follow-ups whose time already passed are planned from their original acceptance time and become due at the next allowed window start; the resume-without-burst policy in the scheduler document is not implemented by this change.
- Campaign-level completion (RUNNING → COMPLETED) is not implemented here; enrollments do complete.
