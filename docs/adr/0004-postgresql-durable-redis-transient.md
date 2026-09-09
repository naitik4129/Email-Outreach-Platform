# PostgreSQL durable truth; Redis temporary coordination

## Status

Accepted source decision — recorded from existing repository specifications on 2026-09-09. This records an established constraint, not a new implementation/deployment approval.

## Context

Queue/cache loss must delay processing without erasing campaigns, suppressions or outcomes.

Source: PROJECT_CONTEXT §88, MVP §3.3 and SYSTEM_ARCHITECTURE §§18–19, 147. See [system architecture](../architecture/SYSTEM_ARCHITECTURE.md), [MVP](../product/MVP.md), and [project context](../product/PROJECT_CONTEXT.md).

## Decision

Persist business state and recoverable work in Supabase PostgreSQL. Redis carries transport, rate coordination, temporary locks and caches.

## Alternatives Considered

Redis-only business state cannot satisfy complete Redis-loss recovery; a second durable product database adds unnecessary ownership complexity. These alternatives explain the documented rationale; this record does not assert an undocumented historical evaluation process.

## Consequences

Recovery must rebuild coordination conservatively. Redis persistence can improve operations but is not a business-correctness guarantee.

