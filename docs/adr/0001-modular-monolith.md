# Modular monolith with independent workers

## Status

Accepted source decision — recorded from existing repository specifications on 2026-09-09. This records an established constraint, not a new implementation/deployment approval.

## Context

Campaigns, recipients, messages and safety state need coherent transactions and shared rules.

Source: MVP §6 and SYSTEM_ARCHITECTURE §§3–4, 147. See [system architecture](../architecture/SYSTEM_ARCHITECTURE.md), [MVP](../product/MVP.md), and [project context](../product/PROJECT_CONTEXT.md).

## Decision

Keep one logical modular backend application with independently executable workers. Backend owns domain/application behavior; workers import it.

## Alternatives Considered

Microservices/database per module introduce distributed coordination before measured need; one undifferentiated module loses domain boundaries. These alternatives explain the documented rationale; this record does not assert an undocumented historical evaluation process.

## Consequences

Retain transaction simplicity and independent worker scaling. Future extraction needs measured justification and a new architectural decision.

