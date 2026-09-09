# Durable PostgreSQL future message scheduling

## Status

Accepted source decision — recorded from existing repository specifications on 2026-09-09. This records an established constraint, not a new implementation/deployment approval.

## Context

Follow-ups may be due days later and must survive worker, scheduler and broker restarts.

Source: MVP §§20–22 and SYSTEM_ARCHITECTURE §§16–17, 33, 147. See [system architecture](../architecture/SYSTEM_ARCHITECTURE.md), [MVP](../product/MVP.md), and [project context](../product/PROJECT_CONTEXT.md).

## Decision

Persist sequence/enrollment progress and future message intent in PostgreSQL. Scheduler discovers due work and workers revalidate eligibility. Queue entries are transport.

## Alternatives Considered

Long-lived Celery ETA/countdown as the sole future-intent record loses business state with transient infrastructure; API/in-memory timers are not durable. These alternatives explain the documented rationale; this record does not assert an undocumented historical evaluation process.

## Consequences

Database claiming, idempotency and recovery are necessary. The exact outbox/lease protocol is a refinement in current subsystem documents, not asserted here as a previously frozen algorithm.

