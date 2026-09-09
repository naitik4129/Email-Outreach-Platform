# Repository and runtime boundaries

## Status

Accepted source decision — recorded from existing repository specifications on 2026-09-09. This records an established constraint, not a new implementation/deployment approval.

## Context

Browser, API, asynchronous entrypoints, schema and infrastructure have different responsibilities.

Source: SYSTEM_ARCHITECTURE §§6–14. See [system architecture](../architecture/SYSTEM_ARCHITECTURE.md), [MVP](../product/MVP.md), and [project context](../product/PROJECT_CONTEXT.md).

## Decision

Preserve frontend/, backend/, workers/, supabase/ and infrastructure/. Backend packages authoritative business logic for API and worker use.

## Alternatives Considered

Duplicated worker business code diverges; browser-owned critical mutations bypass backend business validation. These alternatives explain the documented rationale; this record does not assert an undocumented historical evaluation process.

## Consequences

Runtime processes can deploy independently with compatible package versions. Shared business code must not become a parallel workers domain.

