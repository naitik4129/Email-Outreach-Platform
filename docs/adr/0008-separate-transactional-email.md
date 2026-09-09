# Separate platform transactional email from outreach

## Status

Accepted source decision — recorded from existing repository specifications on 2026-09-09. This records an established constraint, not a new implementation/deployment approval.

## Context

Account recovery, invitations and platform alerts serve a different purpose from customer campaigns.

Source: PROJECT_CONTEXT §§106, 125 and SYSTEM_ARCHITECTURE §§62, 147. See [system architecture](../architecture/SYSTEM_ARCHITECTURE.md), [MVP](../product/MVP.md), and [project context](../product/PROJECT_CONTEXT.md).

## Decision

Use a separate platform transactional provider/interface and credentials. Customer outreach uses authorized customer-connected providers and the common safety pipeline.

## Alternatives Considered

Using customer campaign mailboxes for account notifications couples account operations to customer connection failures; transactional-provider fallback must not become an outreach bypass. These alternatives explain the documented rationale; this record does not assert an undocumented historical evaluation process.

## Consequences

Notification failures do not alter campaign truth. Provider selection/terms remain review items; this ADR does not approve a specific vendor or send any message.

