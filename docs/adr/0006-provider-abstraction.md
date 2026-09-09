# Capability-based customer mailbox providers

## Status

Accepted source decision — recorded from existing repository specifications on 2026-09-09. This records an established constraint, not a new implementation/deployment approval.

## Context

Providers differ in authentication, identifiers, errors, lookup and inbound capabilities.

Source: MVP §§16–18 and SYSTEM_ARCHITECTURE §§36–43, 147. See [system architecture](../architecture/SYSTEM_ARCHITECTURE.md), [MVP](../product/MVP.md), and [project context](../product/PROJECT_CONTEXT.md).

## Decision

Keep provider behavior behind adapters; implement Gmail OAuth/API first, Microsoft OAuth/Graph next, authenticated Custom SMTP compatibility last. Shared send safety applies to every provider.

## Alternatives Considered

Scattered provider conditionals duplicate policy; direct-to-MX/MTA and IP-rotation architectures are excluded. These alternatives explain the documented rationale; this record does not assert an undocumented historical evaluation process.

## Consequences

Capability gaps must remain explicit. Adapter contracts classify evidence, especially uncertain sends; provider limits and scope details require current verification.

