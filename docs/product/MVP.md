# MVP — Email Outreach SaaS

## 1. Purpose

This document defines the functional boundary of the first production-ready release of the email outreach platform.

It answers four questions:

1. What must exist in the MVP?
2. What is explicitly excluded from the MVP?
3. What should follow immediately after the MVP?
4. What conditions must be satisfied before the MVP can be considered production-ready?

This document defines **product scope**, not detailed technical architecture.

Implementation details such as database schemas, queue topology, worker design, provider internals, API contracts, state machines, retry algorithms, and infrastructure belong in the appropriate architecture and engineering documents.

The master product vision remains:

```text
docs/product/PROJECT_CONTEXT.md
```

If this document and `PROJECT_CONTEXT.md` appear to conflict, the conflict must be surfaced and resolved explicitly rather than silently choosing one interpretation.

---

# 2. MVP Objective

The MVP must prove that the product can operate as a safe, reliable, multi-tenant email outreach platform rather than merely demonstrate isolated email-sending functionality.

A production user must be able to:

```text
Create an account
        ↓
Create or access a workspace
        ↓
Connect an email mailbox
        ↓
Import and organize leads
        ↓
Create reusable email content
        ↓
Create an email campaign
        ↓
Build a sequence
        ↓
Select sending mailbox(es)
        ↓
Configure sending schedule and limits
        ↓
Review the campaign
        ↓
Start the campaign
        ↓
Have messages scheduled and sent safely
        ↓
Receive delivery/failure/reply events
        ↓
Automatically stop outreach when appropriate
        ↓
View campaign activity and conversations
```

The MVP is complete only when this complete lifecycle works reliably.

The objective is not to maximize the number of features.

The objective is to establish the smallest production foundation upon which the remainder of the platform can safely grow.

---

# 3. MVP Product Principles

The following principles are mandatory for the MVP.

### 3.1 Reliability before feature breadth

A smaller campaign system that schedules, sends, retries, pauses, suppresses, and processes replies correctly is preferable to a feature-rich campaign builder with unreliable execution.

### 3.2 Safety before sending volume

Provider restrictions, suppression rules, user-configured limits, campaign state, and mailbox state must be respected.

The system must never intentionally bypass provider limits through random IP rotation or similar mechanisms.

### 3.3 Durable state

Business-critical state must survive:

- application restarts
- worker restarts
- Redis restarts
- scheduler restarts
- deployments
- temporary provider failures

PostgreSQL remains the durable source of truth.

Redis may coordinate execution but must not become the only record of scheduled outreach.

### 3.4 Tenant isolation

One workspace must never be able to access another workspace's:

- leads
- campaigns
- mailboxes
- templates
- messages
- conversations
- analytics
- settings
- suppression data
- credentials

Tenant isolation is a release requirement rather than a later hardening task.

### 3.5 Backend-owned business rules

Critical business rules must not depend on frontend behavior.

The backend must authoritatively enforce:

- authorization
- campaign transitions
- sending eligibility
- suppression
- mailbox eligibility
- scheduling rules
- limits
- provider constraints
- sequence stopping rules

### 3.6 Idempotent asynchronous processing

Retries and duplicate task delivery must not result in duplicate business effects.

In particular, retry behavior must not accidentally send the same intended email multiple times.

---

# 4. Scope Classification

Every product area is classified into one of four categories.

| Classification | Meaning |
|---|---|
| **MVP — Required** | Required before the first production release |
| **MVP — Basic** | Required, but intentionally limited in sophistication |
| **V1.1** | First major scope immediately following MVP |
| **Later** | Deliberately excluded until the core platform is stable |

---

# 5. MVP Scope Summary

| Product Area | Scope |
|---|---|
| Platform Foundation | MVP — Required |
| Authentication | MVP — Required |
| Workspaces | MVP — Required |
| RBAC | MVP — Required |
| Leads | MVP — Required |
| Lead Lists | MVP — Required |
| CSV Import | MVP — Required |
| Suppression | MVP — Required |
| Templates | MVP — Required |
| Mailboxes | MVP — Required |
| Gmail Integration | MVP — Required |
| Microsoft Integration | MVP — Required |
| Custom SMTP | MVP — Required |
| Campaigns | MVP — Required |
| Sequences | MVP — Required |
| Scheduling | MVP — Required |
| Sending Engine | MVP — Required |
| Rate Limiting | MVP — Required |
| Retry / Failure Handling | MVP — Required |
| Reply Synchronization | MVP — Required |
| Unified Inbox | MVP — Basic |
| Unsubscribe | MVP — Required |
| Bounce Handling | MVP — Required |
| Complaint Handling | MVP — Required where supported |
| Analytics | MVP — Basic |
| Deliverability | MVP — Basic |
| Notifications | MVP — Basic |
| Admin / Abuse Controls | MVP — Basic |
| Advanced Personalization | V1.1 / Later (Hyper-Personalized campaign type planned for V1.1 — [ADR-0011](../adr/0011-hyper-personalized-campaign-type.md); flag-gated, off by default, does not block the core sending platform) |
| AI Campaign Generation | Later |
| Advanced Workflow Automation | Later |
| Multi-channel Outreach | Later |

The classification above freezes the recommended MVP boundary established by this document.

Detailed functionality for every included area follows.

---

# 6. Platform Foundation

## 6.1 Requirement

The platform must provide the foundational application behavior required for a multi-tenant SaaS product.

The MVP must support:

- authenticated users
- workspaces
- workspace membership
- role-based authorization
- tenant-scoped data
- secure configuration and secret handling
- durable persistence
- asynchronous task execution
- structured application errors
- basic application observability

The MVP does not require distributed microservices or independently deployed services for each product domain.

The intended application remains a modular monolith with independently executable workers.

## 6.2 Acceptance Criteria

Platform foundation is acceptable when:

- authenticated and unauthenticated routes behave correctly
- workspace-scoped resources cannot be accessed across tenants
- background work can be executed safely
- application restarts do not lose durable product state
- critical operations produce actionable errors
- secrets are not exposed to frontend clients or logs

---

# 7. Authentication

## 7.1 MVP Requirements

Users must be able to:

- sign up
- sign in
- sign out
- recover access through the supported password-recovery mechanism
- maintain a valid authenticated session
- access only authorized application areas

Authentication establishes user identity.

Authorization remains a separate workspace-level responsibility.

## 7.2 Acceptance Criteria

Authentication is complete when a new user can progress from registration into an authorized workspace without requiring manual database intervention.

Protected application resources must reject invalid or unauthenticated access.

---

# 8. Workspaces

## 8.1 MVP Requirements

The product must support workspace-based tenancy.

A user must be able to belong to an authorized workspace.

Workspace ownership and membership must provide the foundation for isolating all business resources.

All major domain objects introduced by the MVP must have an unambiguous workspace ownership model.

## 8.2 Acceptance Criteria

A user operating in Workspace A cannot retrieve, mutate, send through, or otherwise interact with resources belonging exclusively to Workspace B.

Workspace boundaries must be enforced server-side and, where appropriate, reinforced through PostgreSQL/RLS policies.

---

# 9. Role-Based Access Control

## 9.1 MVP Requirement

RBAC is required before production release.

The anticipated role model includes:

```text
Owner
Admin
Manager
Member
Viewer
```

The exact permission matrix will be frozen separately in:

```text
docs/product/USER_ROLES.md
```

MVP implementation must not begin with ambiguous permission behavior.

## 9.2 Required Permission Areas

The eventual matrix must explicitly address permissions covering:

- campaigns
- leads
- templates
- mailboxes
- integrations
- workspace settings
- team membership
- billing-sensitive actions
- API/security-sensitive actions
- workspace deletion
- other destructive operations

## 9.3 Acceptance Criteria

Every protected backend operation must be authorized according to workspace membership and role.

Hiding a button in the frontend is not sufficient authorization.

---

# 10. Leads

## 10.1 MVP Requirements

Users must be able to maintain leads that can participate in outreach campaigns.

At minimum, the lead model must support the identity and contact data necessary to perform email outreach.

The product must support:

- creating leads
- viewing leads
- updating leads
- organizing leads
- determining whether a lead is eligible for sending
- tracking relevant lifecycle conditions such as reply or unsubscribe where required by campaign behavior

Exact field design belongs in the domain and database specifications.

## 10.2 Acceptance Criteria

A valid lead can be created, selected for outreach, sent an eligible campaign message, and prevented from receiving inappropriate future outreach after suppression or sequence-stopping conditions occur.

---

# 11. Lead Lists

## 11.1 MVP Requirements

Users must be able to organize leads into reusable lists.

Campaign creation must be able to use an appropriate lead source without requiring users to manually recreate recipient collections for every campaign.

List membership must remain tenant-scoped.

## 11.2 Acceptance Criteria

A user can create or select a lead list and use eligible members as campaign recipients.

A lead's inclusion in a list must never override suppression or other sending-safety rules.

---

# 12. CSV Import

## 12.1 MVP Requirements

CSV lead import is required because production outreach workflows cannot depend entirely on manual lead entry.

The import flow must provide:

- file ingestion
- validation
- field mapping where required
- invalid-row handling
- duplicate-safe behavior
- import result reporting

Large imports should not require one synchronous HTTP request to perform all processing.

## 12.2 Acceptance Criteria

A valid CSV can be imported without manual database changes.

The user must be able to determine:

- whether the import completed
- how many records were accepted
- which records could not be processed
- whether meaningful duplicates or invalid records were encountered

Import retries must not create uncontrolled duplication.

---

# 13. Suppression

## 13.1 MVP Requirement

Suppression is a core sending-safety capability and is mandatory before campaign sending is production-enabled.

The product must prevent outreach when a recipient is not eligible to receive another message.

Suppression must be considered close to actual send time rather than checked only during campaign creation.

Sources of suppression may include applicable states such as:

- unsubscribe
- hard bounce
- complaint
- manually suppressed recipient
- other permanent sending exclusions defined by the final domain model

## 13.2 Acceptance Criteria

If a recipient becomes suppressed after a campaign has already been planned but before a future message is sent, the future message must not be sent.

Suppression must override campaign intent.

---

# 14. Templates

## 14.1 MVP Requirements

Users must be able to create and reuse email content.

The MVP requires the standard/manual template path necessary to build production campaigns.

Template functionality should support the content needed by sequence email steps without introducing an advanced campaign-generation system.

Advanced or AI-driven personalization must not block release of the core sending platform.

## 14.2 Acceptance Criteria

A user can create reusable email content and apply it to an email sequence without manually reconstructing the content for every campaign.

Template behavior must remain separate from actual sending state.

---

# 15. Mailboxes

## 15.1 MVP Requirements

The mailbox domain represents an account the customer's outreach can be sent from.

The product must maintain enough mailbox state to determine:

- workspace ownership
- provider
- connection state
- whether sending is currently permitted
- relevant provider/account configuration
- configured sending limits
- health/error conditions necessary to protect campaign execution

Credential material must be protected.

## 15.2 Acceptance Criteria

A disconnected, revoked, invalid, or otherwise ineligible mailbox cannot continue silently sending campaign messages.

Mailbox failures must become visible to the system and must not disappear into worker logs without a corresponding business-visible state.

---

# 16. Gmail Integration

## 16.1 MVP Requirement

Gmail is the first provider implementation.

The intended authentication model is OAuth with the Gmail API.

The Gmail integration must establish the provider abstraction that subsequent provider implementations follow.

At minimum, the platform must be able to:

- connect an authorized Gmail account
- maintain or refresh credentials as appropriate
- determine connection validity
- send an authorized email
- classify meaningful sending failures
- support the mailbox/reply functionality required elsewhere in the MVP

## 16.2 Acceptance Criteria

The controlled Gmail milestone is:

```text
User signs up
→ workspace exists
→ authorization succeeds
→ Gmail is connected
→ eligible lead exists
→ suppression check succeeds
→ one controlled test email is sent
→ the result is persisted
```

This milestone must succeed before campaign-scale sending is treated as ready.

---

# 17. Microsoft Integration

## 17.1 MVP Requirement

Microsoft mailbox support follows the Gmail provider implementation.

The intended integration is OAuth with Microsoft Graph.

Microsoft-specific implementation details must remain behind the common provider/domain abstraction rather than leaking throughout campaign logic.

## 17.2 Acceptance Criteria

A supported Microsoft account can be connected and used by the common sending workflow without introducing a separate Microsoft-specific campaign engine.

---

# 18. Custom SMTP

## 18.1 MVP Requirement

Custom SMTP remains a compatibility sending path.

It must operate within the same safety model as other providers.

SMTP support must not be interpreted as permission to build direct-to-MX infrastructure or bypass provider restrictions.

SMTP configuration and credentials must be stored securely.

## 18.2 Acceptance Criteria

An appropriately configured SMTP mailbox can be validated and used by the normal sending pipeline.

SMTP failure must be categorized and surfaced rather than silently retried forever.

---

# 19. Campaigns

## 19.1 MVP Requirements

The MVP campaign experience must support the core email outreach lifecycle.

Users must be able to:

- create a campaign
- edit a campaign while its state permits editing
- select recipients
- configure a sequence
- select sending mailbox or mailboxes
- configure scheduling
- configure basic sending limits/settings
- review configuration
- start the campaign
- pause an active campaign
- resume an eligible paused campaign
- view meaningful campaign status
- archive or otherwise remove inactive campaigns where the final state model permits

Campaign state transitions must be controlled by backend business logic.

## 19.2 MVP Non-Requirements

The MVP does not require:

- a general-purpose workflow automation engine
- complex branching workflows
- LinkedIn steps
- arbitrary multi-channel actions
- advanced visual workflow construction
- fully automated campaign generation

## 19.3 Acceptance Criteria

A campaign cannot start unless its required configuration is valid.

Invalid or unauthorized state transitions must be rejected server-side.

Pausing a campaign must prevent new eligible campaign sends from continuing according to the campaign-state architecture.

---

# 20. Sequences

## 20.1 MVP Requirements

The MVP sequence model is intentionally narrow.

Required step concepts are:

```text
Email step
Wait interval
```

This is sufficient to construct sequences such as:

```text
Email 1
↓
Wait 2 days
↓
Email 2
↓
Wait 3 days
↓
Email 3
```

Complex branching is not required.

## 20.2 Required Stopping Behavior

Future outreach for a lead must stop when a definitive stopping condition occurs, including at least the reply and suppression conditions defined elsewhere in this document.

## 20.3 Acceptance Criteria

The system correctly determines the next eligible sequence action for a recipient without requiring long-lived in-memory timers.

---

# 21. Scheduling

## 21.1 MVP Requirements

Campaign sending must support scheduling rules including:

- permitted sending days
- permitted sending hours
- campaign/user-selected timezone behavior
- scheduled future sends
- basic sending limits

Future messages must be represented durably.

The application must not depend on a single long-lived Celery task as the only representation of a message that should be sent days later.

## 21.2 Acceptance Criteria

A message scheduled for the future remains recoverable after:

- Redis restart
- worker restart
- scheduler restart
- application deployment

When execution resumes, the system must determine what work is due from durable state.

---

# 22. Sending Engine

## 22.1 MVP Requirement

The sending engine is responsible for safely converting an eligible planned campaign message into an external provider send.

Immediately before an attempted send, the system must consider the authoritative conditions necessary to determine eligibility.

These include relevant conditions such as:

- campaign state
- message state
- lead state
- suppression
- mailbox state
- schedule eligibility
- rate limits
- provider restrictions

The exact decision pipeline belongs in architecture documentation.

## 22.2 Acceptance Criteria

A worker cannot blindly send a queued email simply because a task exists.

Current authoritative state must be considered before creating the external side effect.

A retry or duplicate queue delivery must not cause uncontrolled duplicate sends.

---

# 23. Rate Limiting

## 23.1 MVP Requirement

Rate limiting is required before meaningful campaign sending.

Limits must protect:

- mailbox/provider constraints
- configured customer limits
- system stability

Rate limiting exists to respect provider constraints, not circumvent them.

Redis may be used for temporary counters or coordination, while durable business state remains elsewhere.

## 23.2 Acceptance Criteria

When the applicable sending limit has been reached, additional work is delayed or rescheduled safely rather than force-sent or permanently lost.

A Redis restart must not corrupt durable campaign state.

---

# 24. Retry and Failure Handling

## 24.1 MVP Requirements

External systems will fail.

The platform must distinguish between conditions that may succeed later and conditions where retrying is inappropriate.

Where applicable, retry behavior should incorporate controlled backoff and jitter.

Permanent failures must eventually become terminal rather than retry indefinitely.

Failures must be visible through appropriate persisted states or events.

## 24.2 Acceptance Criteria

Temporary provider failures can be retried safely.

Permanent failures stop retrying.

Retries remain idempotent.

A worker crash does not silently mark an unsent message as successfully sent.

---

# 25. Reply Synchronization

## 25.1 MVP Requirement

Reply handling is a core campaign function rather than an analytics enhancement.

The platform must be able to detect supported replies through the connected mailbox model and associate them with the appropriate outreach context.

Conceptually:

```text
Outbound email sent
        ↓
Recipient replies
        ↓
Mailbox synchronization detects reply
        ↓
Reply matched to lead/conversation
        ↓
Lead/campaign recipient marked appropriately
        ↓
Future sequence outreach stopped
        ↓
Conversation updated
```

## 25.2 Acceptance Criteria

A recognized reply prevents future campaign sequence messages that should no longer be sent to that recipient.

Repeated synchronization of the same provider reply must not create duplicate conversations or duplicate reply records.

---

# 26. Unified Inbox

## 26.1 MVP Scope

A basic unified inbox is included.

Its role in MVP is to make campaign replies operationally usable.

It does not need to compete with a fully featured email client.

The inbox should allow users to identify and review conversations/replies generated through supported outreach activity.

Advanced inbox automation can follow later.

## 26.2 Acceptance Criteria

A user can identify that a campaign recipient replied and access the relevant synchronized conversation without inspecting provider logs or the database.

---

# 27. Unsubscribe

## 27.1 MVP Requirement

Unsubscribe behavior is mandatory.

An unsubscribe must result in a durable suppression outcome that prevents inappropriate future outreach.

The system must not rely solely on removing a recipient from one active campaign.

## 27.2 Acceptance Criteria

Once an unsubscribe has been successfully recorded, pending future outreach covered by the resulting suppression rule cannot proceed.

This condition must continue to hold after workers or Redis restart.

---

# 28. Bounce Handling

## 28.1 MVP Requirement

The platform must process bounce information available through supported providers/events.

Bounce classification should distinguish sufficiently between temporary and permanent failure behavior where the provider makes that possible.

Permanent recipient failures must participate in suppression/sending eligibility as defined by the final domain model.

## 28.2 Acceptance Criteria

A known permanently invalid recipient is not repeatedly targeted by future messages merely because they appear in another campaign.

Bounce information is persisted and available for operational/analytics use.

---

# 29. Complaint Handling

## 29.1 MVP Requirement

Where supported provider/event information exposes a recipient complaint or equivalent strong negative signal, the system must process it as a sending-safety event.

Complaint handling must not be treated merely as an analytics counter.

## 29.2 Acceptance Criteria

A confirmed complaint event updates the appropriate recipient/suppression state and prevents prohibited future sending.

---

# 30. Basic Analytics

## 30.1 MVP Scope

Analytics must provide enough visibility to understand whether campaigns are operating.

The MVP should prioritize reliable business signals over vanity metrics.

Important outcomes include concepts such as:

- messages attempted
- messages sent
- failures
- bounces
- replies
- unsubscribes
- complaints where available

Open tracking, where eventually supported, must be represented as an estimated signal rather than unquestioned proof of human engagement.

Advanced attribution and analytics exploration are not required for MVP.

## 30.2 Acceptance Criteria

A campaign operator can determine basic campaign outcomes without querying the database or infrastructure logs.

Analytics must not knowingly double-count duplicated provider events.

---

# 31. Basic Deliverability

## 31.1 MVP Scope

The MVP must expose enough mailbox/campaign health information to prevent operators from blindly continuing obviously unhealthy outreach.

This does not require building a complete deliverability optimization platform.

The MVP should focus on actionable platform-known signals generated from:

- sends
- failures
- bounces
- replies
- complaints
- mailbox connection state
- relevant sending conditions

## 31.2 Non-MVP Scope

Advanced automated deliverability optimization, complex reputation scoring, and other specialized deliverability systems may follow after the underlying signals are reliable.

---

# 32. Notifications

## 32.1 MVP Scope

Notifications are included only where they make important operational conditions visible.

Examples may include significant states such as:

- mailbox connection problems
- important campaign execution failures
- completed import jobs
- other conditions where silent failure would materially harm the user experience

The exact notification channels and UX belong in later product specifications.

## 32.2 Acceptance Criteria

Important recoverable user-facing failures must not exist exclusively inside backend logs.

---

# 33. Basic Admin and Abuse Controls

## 33.1 MVP Requirement

A production outreach SaaS cannot launch without basic operational control over misuse or unsafe activity.

The MVP must establish enough administrative capability to investigate and respond to serious platform misuse or malfunction.

The exact admin product surface remains to be specified separately.

## 33.2 Acceptance Criteria

The platform operator must have a controlled mechanism to intervene when a workspace or sending capability must be restricted for legitimate platform-safety reasons.

This must not rely on ad hoc production database editing as the normal administrative workflow.

---

# 34. Explicitly Out of MVP

The following capabilities are deliberately excluded from the initial MVP unless the master product context explicitly overrides a particular item.

## 34.1 Advanced Campaign Automation

Not required:

- arbitrary workflow graphs
- complex conditional branches
- general-purpose workflow automation
- advanced cross-campaign automation

## 34.2 Multi-Channel Outreach

Not required:

- LinkedIn sequence steps
- SMS
- calling
- arbitrary third-party outreach channels

The MVP remains email-first.

## 34.3 AI-Generated Campaign Systems

A system that automatically designs entire campaigns is not required for MVP.

AI capability must not delay establishment of reliable core outreach infrastructure.

## 34.4 Advanced Automated Optimization

Not required:

- sophisticated automated A/B optimization
- advanced campaign optimization engines
- complex automated decision systems

## 34.5 Direct-to-MX Infrastructure

The MVP will not implement its own general-purpose outbound mail delivery network.

The system will use supported customer mailbox/provider paths.

Random VPS IP switching is not an MVP architecture.

## 34.6 Premature Infrastructure Complexity

The MVP does not require:

- Kubernetes
- Kafka
- service mesh architecture
- separate database per product module
- dozens of independently deployed microservices

Infrastructure should remain proportional to actual operational requirements.

---

# 35. V1.1 Scope

V1.1 should deepen the product only after the MVP sending lifecycle has proven reliable in production.

Candidate V1.1 areas include:

- richer template capabilities
- deeper personalization
- improved campaign analytics
- richer inbox workflows
- improved deliverability insights
- more advanced campaign settings
- stronger operational/admin tooling
- improved automation around common repetitive outreach actions

Exact V1.1 scope must be frozen after reviewing production behavior and the master product vision.

V1.1 must not be used as an excuse to postpone safety-critical functionality that belongs in MVP.

---

# 36. Later Product Scope

Longer-term product evolution may include capabilities such as:

```text
advanced personalization
AI-assisted outreach
AI campaign creation
advanced A/B experimentation
complex conditional sequences
workflow automation
multi-channel outreach
advanced deliverability tooling
advanced analytics
advanced inbox automation
large-scale organizational/admin capabilities
```

These capabilities should build on the MVP's domain model rather than require rebuilding the core sending system.

---

# 37. MVP Dependency Order

MVP modules should not be implemented in arbitrary order.

The product dependency sequence is:

```text
Authentication
        ↓
Workspaces
        ↓
RBAC
        ↓
Leads
        ↓
Lead Lists / CSV Import
        ↓
Suppression
        ↓
Mailbox Domain
        ↓
Gmail Connection
        ↓
Controlled Gmail Test Send
        ↓
Microsoft
        ↓
Custom SMTP
        ↓
Templates
        ↓
Campaign Configuration
        ↓
Sequences
        ↓
Campaign Validation
        ↓
Durable Message Planning
        ↓
Scheduling
        ↓
Rate Limiting
        ↓
Send Worker
        ↓
Retry / Failure Handling
        ↓
Provider Events
        ↓
Bounce / Complaint Processing
        ↓
Reply Synchronization
        ↓
Unified Inbox
        ↓
Unsubscribe Completion
        ↓
Basic Analytics
        ↓
Basic Deliverability
        ↓
Operational Notifications
        ↓
Admin / Abuse Controls
        ↓
Production Hardening
```

Some modules may be developed in parallel where dependencies permit, but later stages must not be considered complete before their foundational dependencies are reliable.

---

# 38. Pre-Campaign Foundation Milestone

Before building full campaign execution, the platform must prove the following controlled lifecycle:

```text
User signs up
        ↓
Workspace created
        ↓
Workspace authorization verified
        ↓
Lead created/imported
        ↓
Suppression eligibility confirmed
        ↓
Gmail connected
        ↓
One controlled email prepared
        ↓
One controlled email sent
        ↓
Send result persisted
        ↓
Failure path validated
```

This is a mandatory engineering milestone.

Failure to prove this path means the project is not ready to introduce campaign-scale complexity.

---

# 39. First Production MVP Milestone

The first production MVP milestone is achieved when a real authorized workspace can complete the following workflow without manual database intervention:

```text
Sign up
        ↓
Enter workspace
        ↓
Connect supported mailbox
        ↓
Import leads
        ↓
Create/select template
        ↓
Create campaign
        ↓
Configure email + wait sequence
        ↓
Select mailbox(es)
        ↓
Configure schedule
        ↓
Review
        ↓
Start
        ↓
Messages become durably scheduled
        ↓
Eligible messages are sent within configured rules
        ↓
Temporary failures retry safely
        ↓
Permanent failures stop appropriately
        ↓
Replies are synchronized
        ↓
Future sequence stops after reply
        ↓
Unsubscribe/suppression prevents future sends
        ↓
User can view campaign outcome and relevant conversations
```

This lifecycle defines the central promise of the MVP.

---

# 40. Production Release Criteria

The MVP is not production-ready merely because the happy path works.

Production release requires all of the following categories to pass.

## 40.1 Functional

The primary workflow defined in the previous section succeeds end to end.

Supported mailbox providers perform according to their documented MVP contract.

Campaign pause/resume and recipient stopping rules behave correctly.

## 40.2 Data Integrity

Critical campaign and message state is durable.

Database constraints protect important invariants where appropriate.

Duplicate task/event processing does not corrupt business state.

Applied database migrations are version controlled under:

```text
supabase/migrations/
```

There is no competing Alembic migration history.

## 40.3 Tenant Security

Cross-workspace access tests pass.

Backend authorization is enforced.

Relevant RLS/security policies have been reviewed.

Secrets and provider credentials are protected.

## 40.4 Sending Safety

Suppression is enforced close to sending time.

Unsubscribed recipients cannot continue receiving prohibited sequence messages.

Permanent bounce/complaint behavior is handled appropriately.

Provider and configured sending limits are respected.

## 40.5 Asynchronous Reliability

Worker restart does not lose durable work.

Redis restart does not erase campaign schedules.

Retries are idempotent.

Duplicate tasks do not knowingly cause duplicate business effects.

Scheduler recovery behavior is tested.

## 40.6 Provider Reliability

Invalid/revoked connections are detected.

Temporary provider errors can recover.

Permanent errors terminate appropriately.

Provider failures are surfaced in meaningful application state.

## 40.7 Operational Visibility

Important failures can be diagnosed.

Campaign operators can see meaningful campaign outcomes.

Administrators have enough visibility to identify critical system or abuse conditions.

## 40.8 Testing

Critical domain rules have automated tests.

Key integration paths have automated coverage where practical.

Campaign state transitions and message execution rules are tested.

Suppression, retry, duplicate processing, and authorization edge cases are tested.

## 40.9 Deployment

The application can be deployed using the established infrastructure approach without manual source-code modification.

Required configuration is documented.

Production secrets are not committed to the repository.

## 40.10 Migration Safety

Every production database change follows the repository migration process.

No migration may be considered approved merely because an agent generated it.

The migration must be explained, reviewed, tested, validated in the appropriate environment, and explicitly approved before production application.

---

# 41. Critical MVP Invariants

The following rules must always remain true.

### Tenant invariant

A workspace can operate only on resources it is authorized to access.

### Suppression invariant

A suppressed recipient must not receive outreach that the applicable suppression rule prohibits.

### Campaign invariant

A campaign can execute only while its authoritative state permits execution.

### Message invariant

A message may be externally sent only when the current authoritative state permits the send.

### Provider invariant

Provider-specific behavior must not cause the campaign domain to bypass shared safety rules.

### Durability invariant

Redis must never be the only durable representation of future campaign work.

### Retry invariant

A retry must not knowingly duplicate an already completed external business effect.

### Reply invariant

A recognized reply must stop applicable future sequence outreach.

### Unsubscribe invariant

A successfully recorded unsubscribe must prevent applicable future outreach.

### Authorization invariant

Frontend visibility does not substitute for server-side authorization.

Breaking any of these invariants is a release-blocking defect.

---

# 42. What Does Not Define MVP Completion

The MVP is not considered complete because:

- the frontend screens exist
- a provider test endpoint can send email
- a worker consumes Celery tasks
- campaign CRUD works
- a Redis queue contains scheduled jobs
- analytics charts render
- Docker containers start successfully
- one happy-path demo succeeds

Those are component milestones.

MVP completion requires the complete production lifecycle and release criteria described in this document.

---

# 43. Gate Before Architecture Phase

Completion of this document does not authorize immediate application scaffolding.

Before implementation begins, the following product documents must also be completed:

```text
docs/product/USER_ROLES.md
docs/product/PAGE_MAP.md
docs/product/USER_FLOWS.md
```

Once those are frozen, proceed into the planned architecture documentation.

---

# 44. Gate Before Database Design

Database modeling must follow product and architecture definition rather than lead it.

Before the initial production schema is finalized, the project should define:

```text
SYSTEM_ARCHITECTURE
DOMAIN_MODEL
CAMPAIGN_STATE_MACHINE
MESSAGE_STATE_MACHINE
PROVIDER_ARCHITECTURE
SCHEDULER
WORKERS / QUEUES
RATE_LIMITING
EVENT SYSTEM
REPLY_SYNC
SUPPRESSION
```

These specifications determine the data model required to implement the product safely.

---

# 45. Gate Before `0001_initial.sql`

Do not generate the initial Supabase migration merely because the repository contains an empty migration directory.

The initial database design must first define foundational concepts such as:

- profile/user relationship
- workspaces
- workspace membership
- tenancy boundaries
- role foundation
- required constraints
- security/RLS foundation
- timestamp conventions
- indexes required by initial access patterns

Only then should:

```text
supabase/migrations/0001_initial.sql
```

be designed.

The migration must follow the repository's required migration review process before execution.

---

# 46. Gate Before Application Scaffolding

Application scaffolding begins only when the following are sufficiently defined:

```text
Product scope
        ✓

User roles
        ↓
Page map
        ↓
User flows
        ↓
System architecture
        ↓
Domain model
        ↓
State machines
        ↓
Provider model
        ↓
Scheduler / worker model
        ↓
Rate-limit model
        ↓
Event / reply model
        ↓
Database model
        ↓
Initial reviewed migration
        ↓
Application scaffolding
```

The objective is to prevent implementation agents from making foundational product or architectural decisions implicitly while writing feature code.

---

# 47. Open Decisions Deferred to Subsequent Documents

This MVP document intentionally does not freeze details that belong elsewhere.

These include:

| Decision | Owner Document |
|---|---|
| Exact role permissions | `USER_ROLES.md` |
| Exact frontend routes | `PAGE_MAP.md` |
| Detailed user journeys | `USER_FLOWS.md` |
| Campaign states and transitions | `CAMPAIGN_STATE_MACHINE.md` |
| Message states/events | `MESSAGE_STATE_MACHINE.md` |
| Provider interface contract | `PROVIDER_ARCHITECTURE.md` |
| Queue topology | `QUEUES.md` |
| Scheduler claiming algorithm | `SCHEDULER.md` |
| Rate-limit algorithms | `RATE_LIMITING.md` |
| Reply matching model | `REPLY_SYNC.md` |
| Detailed suppression semantics | `SUPPRESSION.md` |
| Tables and relationships | `DATABASE.md` |
| RLS implementation | Database/security specifications |
| Exact API contracts | Backend/API specifications |
| Exact UI component behavior | Frontend specifications |

An implementation agent must not treat an unresolved detail in this document as permission to invent a permanent architecture.

---

# 48. Definition of Done for `MVP.md`

This document is considered complete when the project owner has reviewed and accepted:

```text
MVP boundary
✓

Explicit exclusions
✓

V1.1 / later boundary
✓

Required MVP modules
✓

Core functionality of those modules
✓

Implementation dependency order
✓

Controlled-send milestone
✓

First production milestone
✓

Release criteria
✓

Critical invariants
✓

Pre-implementation gates
✓
```

Once accepted, changes to MVP scope should be made deliberately through updates to this document rather than emerging accidentally during implementation.

---

# 49. Next Document

After approval of this MVP definition, the next product specification is:

```text
docs/product/USER_ROLES.md
```

Its job is to freeze the exact workspace role and permission model before authentication/authorization architecture or RBAC implementation begins.

The expected starting roles are:

```text
Owner
Admin
Manager
Member
Viewer
```

Their exact permissions remain intentionally outside the responsibility of this document.

---

# 50. Final MVP Definition

The MVP is a production-capable, email-first outreach platform in which an authorized workspace can connect supported sending accounts, manage leads, construct simple email sequences, schedule and execute outreach safely, respect provider and suppression rules, recover from operational failures, process recipient outcomes and replies, stop inappropriate future messages, and provide enough inbox, analytics, deliverability, notification, and administrative visibility to operate the system responsibly.

Everything beyond that boundary must justify delaying the first reliable production release.