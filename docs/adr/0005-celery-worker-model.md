# Celery runtime groups sharing backend logic

## Status

Accepted source decision — recorded from existing repository specifications on 2026-09-09. This records an established constraint, not a new implementation/deployment approval.

## Context

Sending, sync, imports and safety events need asynchronous execution with workload isolation.

Source: PROJECT_CONTEXT §87 and SYSTEM_ARCHITECTURE §§13–15, 126. See [system architecture](../architecture/SYSTEM_ARCHITECTURE.md), [MVP](../product/MVP.md), and [project context](../product/PROJECT_CONTEXT.md).

## Decision

Use Python/Celery with Redis and one worker codebase running specialized groups. Workers reload durable state and invoke backend services.

## Alternatives Considered

Bulk sending in HTTP requests violates bounded API execution; one unstructured queue starves latency-sensitive work; microservices are unnecessary. These alternatives explain the documented rationale; this record does not assert an undocumented historical evaluation process.

## Consequences

Tasks must tolerate duplicates/loss through domain state and reconciliation. Exact queues and acknowledgment settings are detailed design, not newly claimed historical decisions.

