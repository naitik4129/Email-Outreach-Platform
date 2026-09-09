# System Architecture — Email Outreach SaaS

## 1. Purpose

This document defines the top-level production architecture of the email outreach platform.

It establishes the architectural rules that all implementation work and lower-level architecture documents must follow.

It defines:

- system boundaries
- runtime components
- component responsibilities
- communication patterns
- durable versus transient state
- frontend/backend boundaries
- worker responsibilities
- database ownership
- multi-tenant isolation
- provider integration boundaries
- asynchronous execution model
- campaign execution model
- reliability principles
- security boundaries
- deployment topology
- observability requirements
- failure and recovery behavior
- scaling strategy
- architectural non-goals

This document deliberately does **not** define every implementation detail.

Detailed behavior belongs in focused documents such as:

```text
docs/architecture/DOMAIN_MODEL.md
docs/architecture/CAMPAIGN_STATE_MACHINE.md
docs/architecture/MESSAGE_STATE_MACHINE.md
docs/architecture/PROVIDER_ARCHITECTURE.md
docs/architecture/SCHEDULER.md
docs/architecture/WORKERS.md
docs/architecture/QUEUES.md
docs/architecture/RATE_LIMITING.md
docs/architecture/EVENT_SYSTEM.md
docs/architecture/REPLY_SYNC.md
docs/architecture/SUPPRESSION.md
docs/database/DATABASE.md
```

The architecture described here is authoritative at the system level.

---

# 2. Architectural Goal

The platform must behave as a reliable multi-tenant outreach system, not merely as a web application that submits email requests.

Its fundamental execution model is:

```text
User intent
    ↓
Authenticated application request
    ↓
Backend authorization
    ↓
Business validation
    ↓
Durable state change
    ↓
Asynchronous execution
    ↓
Current-state safety checks
    ↓
External provider interaction
    ↓
Durable result/event
    ↓
Next business decision
```

The core architectural objective is:

> Every externally meaningful action should be explainable and recoverable from durable system state.

---

# 3. Architecture Style

The platform will use a:

# Modular Monolith + Independently Executable Workers

This means the product is one logical business application, but different runtime processes perform different operational responsibilities.

The platform is **not** initially a microservice architecture.

Conceptually:

```text
                   ┌─────────────────────┐
                   │      Frontend       │
                   │ Next.js / TypeScript│
                   └──────────┬──────────┘
                              │
                              │ HTTPS
                              ▼
                   ┌─────────────────────┐
                   │       Backend       │
                   │ FastAPI / Python    │
                   │                     │
                   │ Domain + App Logic  │
                   └──────────┬──────────┘
                              │
              ┌───────────────┼────────────────┐
              │               │                │
              ▼               ▼                ▼
        PostgreSQL          Redis        External Providers
        / Supabase                      Gmail / Microsoft /
                                        SMTP / other services

                              │
                              ▼
                    ┌─────────────────┐
                    │     Workers     │
                    │ Celery / Python │
                    └─────────────────┘
```

The same authoritative domain logic is reused by the API and asynchronous workers.

---

# 4. Why a Modular Monolith

The initial product contains strongly related domains:

- workspaces
- users
- leads
- campaigns
- sequences
- mailboxes
- messages
- suppression
- conversations
- events
- analytics

Splitting these into independent services too early would introduce unnecessary:

- distributed transactions
- duplicated models
- API coordination
- network failure modes
- observability complexity
- deployment complexity
- consistency problems
- operational overhead

A modular monolith preserves clear internal boundaries while keeping business transactions coherent.

The architecture should allow future service extraction if scale or organizational requirements justify it.

Service extraction must be driven by measured operational need, not architectural fashion.

---

# 5. Top-Level Runtime Components

The platform consists of the following primary runtime components:

```text
Frontend
Backend API
Worker Processes
Scheduler
Redis
PostgreSQL / Supabase
Object Storage
External Email Providers
Transactional Notification Provider
Observability Infrastructure
```

Each component has a narrow responsibility.

---

# 6. Repository-to-Runtime Mapping

Repository structure:

```text
frontend/
backend/
workers/
supabase/
infrastructure/
docs/
scripts/
tests/
```

Maps conceptually to:

```text
frontend/
    → browser-facing application

backend/
    → API + authoritative business/domain services

workers/
    → asynchronous entrypoints and task execution

supabase/
    → database migrations only

infrastructure/
    → deployment and operational configuration
```

Workers are separate runtime processes but remain part of the same business system.

---

# 7. Frontend Architecture

Technology:

```text
Next.js
React
TypeScript
Tailwind CSS
shadcn/ui
```

The frontend is responsible for:

- user interface
- navigation
- forms
- client-side validation
- user feedback
- optimistic UX where safe
- loading/error states
- authenticated application session UX
- presenting server-authoritative state

The frontend is **not** responsible for authoritative business decisions.

It must never determine:

- whether a user is authorized
- whether a campaign can start
- whether a recipient may receive email
- whether suppression applies
- whether a campaign transition is valid
- whether a mailbox is currently allowed to send
- whether a provider limit may be exceeded

Those decisions belong to the backend.

---

# 8. Frontend Data Access Boundary

The production application should use:

```text
Frontend
    ↓
FastAPI
    ↓
Application / Domain Services
    ↓
PostgreSQL
```

for ordinary product data.

The frontend should **not directly write core product tables in Supabase**.

This includes:

- campaigns
- leads
- mailboxes
- messages
- suppression
- conversations
- workspace configuration

Direct browser-to-database business mutations would weaken:

- authorization consistency
- auditability
- business validation
- state-machine enforcement
- idempotency
- concurrency controls

RLS remains an additional security layer rather than a replacement for the backend.

---

# 9. Backend Architecture

Technology:

```text
Python
FastAPI
Pydantic
SQLAlchemy
```

The backend owns authoritative application behavior.

Conceptual request pipeline:

```text
HTTP Request
    ↓
Authentication
    ↓
Workspace Context
    ↓
Authorization
    ↓
Input Validation
    ↓
Application Service
    ↓
Domain Policy / Business Logic
    ↓
Database Transaction
    ↓
Response
```

The API layer should remain thin.

Route handlers should not contain large amounts of campaign, provider, or suppression logic.

---

# 10. Backend Module Boundaries

The backend should be organized by product domain rather than by generic technical folders alone.

Conceptually:

```text
backend/app/
├── core/
├── db/
├── modules/
│   ├── workspaces/
│   ├── users/
│   ├── leads/
│   ├── lists/
│   ├── suppression/
│   ├── templates/
│   ├── mailboxes/
│   ├── campaigns/
│   ├── messages/
│   ├── conversations/
│   ├── events/
│   ├── analytics/
│   ├── notifications/
│   └── admin/
│
├── providers/
└── services/
```

Exact folders may evolve during scaffolding, but domain ownership must remain explicit.

---

# 11. Internal Layering

Each significant module should conceptually separate:

```text
API / Transport
        ↓
Application Service
        ↓
Domain Rules
        ↓
Repository / External Ports
```

Example:

```text
POST /campaigns/{id}/start
        ↓
CampaignApplicationService.start()
        ↓
Campaign domain validation
        ↓
Campaign repository
        ↓
Message planning
```

The route itself must not directly manipulate raw campaign status columns.

---

# 12. Domain Logic Ownership

Business logic has one authoritative implementation.

For example:

```text
Can campaign start?
Can message be sent?
Should recipient stop?
Can suppression be removed?
Can campaign resume?
```

must not have separate implementations in:

```text
frontend/
backend/
workers/
```

The backend domain/application layer owns these decisions.

Frontend code may mirror validation for UX but cannot become authoritative.

Workers invoke the same authoritative logic.

---

# 13. Worker Architecture

Technology:

```text
Python
Celery
Redis
```

Workers perform asynchronous work that should not execute inside normal HTTP request lifetimes.

Examples:

```text
email sending
scheduled work
mailbox synchronization
provider event processing
CSV imports
verification
personalization
analytics processing
notifications
maintenance
```

Workers do not own separate business rules.

Conceptually:

```text
Celery task
    ↓
Load durable state
    ↓
Invoke backend/application service
    ↓
Perform external operation if permitted
    ↓
Persist result
```

---

# 14. Sharing Backend Logic with Workers

`workers/` must reuse the backend's authoritative Python application/domain package.

Preferred model:

```text
backend application package
        ↑
        │
    imported by
        │
workers task entrypoints
```

The worker runtime image should install or include the backend application package.

Avoid:

```text
backend/campaign_logic.py

workers/campaign_logic.py
```

with independent implementations.

---

# 15. Worker Runtime Groups

One worker codebase can execute multiple process groups.

Initial conceptual groups:

```text
worker-send
worker-sync
worker-general
```

Later, where necessary:

```text
worker-ai
worker-import
worker-analytics
```

Potential responsibilities:

```text
worker-send
    email sending
    send retries

worker-sync
    mailbox synchronization
    reply synchronization

worker-general
    imports
    webhooks
    notifications
    maintenance

worker-ai
    personalization
    classification
```

Separate processes allow independent scaling without turning each worker group into an independent application.

---

# 16. Scheduler Architecture

The scheduler is responsible for discovering durable future work that has become eligible for execution.

The scheduler must **not** be the durable owner of future messages.

Conceptually:

```text
PostgreSQL
    ↓
Find due durable messages
    ↓
Safely claim / enqueue work
    ↓
Redis / Celery
    ↓
Worker
```

Future messages remain represented in PostgreSQL regardless of queue state.

---

# 17. Scheduler Safety Requirement

The scheduler must tolerate:

- restart
- duplicate execution
- multiple scheduler iterations
- worker delays
- Redis restart
- process deployment

A scheduler crash must not cause campaign plans to disappear.

Later `SCHEDULER.md` must define the exact claiming mechanism.

The preferred architecture will use transactional database claiming/leases rather than relying on in-memory ownership.

---

# 18. PostgreSQL as Durable Truth

Supabase PostgreSQL is the system of record.

Durable product state belongs in PostgreSQL.

Examples:

```text
workspace
membership
lead
list
suppression
mailbox
campaign
campaign recipient progress
sequence
message intent
message execution state
conversation
reply
event
import job
audit data
```

If losing a piece of information after a Redis restart would break the product, that information does not belong only in Redis.

---

# 19. Redis Responsibility

Redis is operational infrastructure.

It may hold:

```text
Celery queues
temporary task metadata
rate-limit counters
token buckets
distributed locks
short-lived cache
coordination state
```

Redis is **not** the primary business database.

The architecture must remain recoverable if Redis is emptied.

---

# 20. Database Migration Strategy

The sole schema migration system is:

```text
supabase/migrations/
```

All PostgreSQL schema changes belong there.

This includes:

- tables
- columns
- indexes
- constraints
- RLS
- policies
- functions
- triggers
- views
- extensions

There is no Alembic migration history.

SQLAlchemy is for runtime database access.

Supabase SQL migrations are for schema evolution.

---

# 21. Migration Immutability

Once a migration has been applied to a shared environment:

```text
DO NOT edit it
DO NOT rename it
DO NOT reorder it
DO NOT rewrite it
```

Subsequent changes require a new migration.

---

# 22. Core Data Ownership Model

Every workspace-owned domain resource must have an unambiguous tenant relationship.

Conceptually:

```text
Workspace
    ├── Members
    ├── Leads
    ├── Lists
    ├── Templates
    ├── Mailboxes
    ├── Campaigns
    ├── Messages
    ├── Conversations
    ├── Suppression
    └── Analytics Data
```

A resource must never rely on indirect assumptions to determine tenancy where explicit workspace ownership is appropriate.

---

# 23. Multi-Tenant Security Model

Tenant isolation is implemented in multiple layers.

```text
Authentication
        ↓
Workspace Membership
        ↓
Backend Authorization
        ↓
Workspace-scoped Queries
        ↓
Database Constraints / RLS
```

No single layer is sufficient by itself.

---

# 24. Database RLS Strategy

RLS should function as defense in depth for tenant-owned data.

Because the backend uses SQLAlchemy rather than direct browser database access, the database architecture should support a dedicated application database role that does not casually bypass tenant policies.

The detailed implementation belongs in `DATABASE.md` and the security specifications.

A production-ready pattern is:

```text
API authenticates user
    ↓
API resolves workspace
    ↓
Database transaction starts
    ↓
Transaction-local user/workspace context established
    ↓
RLS policies evaluate that context
    ↓
Queries execute
```

The final implementation must remain compatible with connection pooling.

Global superuser/service-role database credentials must not become the default mechanism for normal user-scoped queries.

---

# 25. Authentication vs Authorization

Authentication answers:

> Who is this user?

Authorization answers:

> What may this user do inside this workspace?

These remain distinct.

Conceptually:

```text
Identity
    ↓
Workspace membership
    ↓
Role
    ↓
Permission
    ↓
Resource/business-state validation
```

The final permission matrix belongs in `USER_ROLES.md`.

---

# 26. API Boundary

The backend API is the primary product boundary for frontend requests.

Recommended namespace:

```text
/api/v1/*
```

Versioning provides room for future evolution.

Example conceptual resources:

```text
/api/v1/workspaces
/api/v1/leads
/api/v1/campaigns
/api/v1/mailboxes
/api/v1/templates
/api/v1/inbox
/api/v1/analytics
```

Exact endpoints belong in API design during implementation.

---

# 27. API Design Principles

APIs should follow these rules:

```text
resource-oriented
predictable
workspace-safe
idempotent where required
pagination-ready
explicit errors
versioned
validated
observable
```

Avoid generic endpoints such as:

```text
POST /do-action
POST /update-status
```

when a clear domain operation exists.

---

# 28. Command vs Query Separation

The architecture should distinguish conceptually between:

```text
Queries
    read current state

Commands
    attempt a state-changing business operation
```

For example:

```text
GET campaign
```

is a query.

```text
Start campaign
```

is a business command.

Commands must validate business rules rather than directly mutate arbitrary fields.

This does not require introducing a full CQRS infrastructure.

---

# 29. Concurrency Control

Concurrency is a first-class architectural concern.

Potential conflicts include:

```text
two workers attempting same message
campaign paused during send
reply received while next message becomes due
unsubscribe received during scheduling
multiple schedulers finding same message
duplicate provider event
duplicate webhook delivery
multiple campaign-start requests
```

The database must participate in preventing unsafe races.

Later architecture documents must define appropriate combinations of:

- transactional state transitions
- unique constraints
- row-level locking
- compare-and-set behavior
- idempotency keys
- claims/leases

---

# 30. Idempotency

Every asynchronous or externally retried workflow must be designed for duplicate delivery.

The system assumes:

> A task, webhook, sync result, or client command may arrive more than once.

Critical idempotent workflows include:

```text
campaign start
message planning
message enqueueing
send task
send retry
webhook processing
reply synchronization
bounce processing
complaint processing
unsubscribe processing
CSV import
```

Idempotency must be supported by durable database state and constraints, not only in-memory checks.

---

# 31. Campaign Execution Architecture

Campaign execution separates:

```text
configuration
planning
scheduling
execution
provider interaction
event processing
```

Conceptually:

```text
Campaign configured
    ↓
Backend validates
    ↓
Campaign activated
    ↓
Durable recipient/message plan created
    ↓
Scheduler discovers due work
    ↓
Worker revalidates eligibility
    ↓
Rate limiter permits
    ↓
Provider send
    ↓
Result persisted
    ↓
Next sequence behavior determined
```

---

# 32. Why Planning and Sending Are Separate

Campaign activation should not immediately iterate through all recipients and send from the API process.

Planning creates durable intent.

Sending executes that intent later.

This separation enables:

- scheduled sending
- pause/resume
- recovery
- provider limits
- retries
- suppression checks
- reply stopping
- timezone rules
- scalable workers

---

# 33. Message Durability

A future email must exist as durable product state.

Conceptually:

```text
Message Intent

campaign
recipient
sequence step
mailbox
scheduled time
current execution state
```

Exact fields belong in `DATABASE.md`.

Celery tasks are transportation.

They are not the source of truth.

---

# 34. Pre-Send Eligibility Gate

Immediately before creating an external send side effect, the worker must validate current authoritative state.

Conceptually:

```text
Message due?
    ↓
Still unsent?
    ↓
Campaign executable?
    ↓
Recipient active?
    ↓
Recipient not suppressed?
    ↓
No applicable reply?
    ↓
Mailbox healthy?
    ↓
Schedule currently valid?
    ↓
Rate capacity available?
    ↓
Provider eligible?
    ↓
SEND
```

Failure at any gate should result in the appropriate:

```text
cancel
skip
defer
retry
terminal failure
```

behavior.

Exact state semantics belong in later documents.

---

# 35. External Side-Effect Boundary

Email sending is an external side effect.

It cannot be atomically rolled back with a PostgreSQL transaction.

Therefore sending logic must explicitly handle the possibility that:

```text
provider accepted email
        ↓
worker crashes before local commit
```

or:

```text
request timed out
        ↓
actual provider acceptance is uncertain
```

Provider-specific idempotency capabilities should be used where available.

Where providers do not offer native idempotency, the system must minimize duplicate-send risk through:

- durable execution records
- attempt identifiers
- transactionally controlled claims
- reconciliation
- conservative retry behavior when outcome is ambiguous

This must be designed in detail in `MESSAGE_STATE_MACHINE.md` and `PROVIDER_ARCHITECTURE.md`.

---

# 36. Provider Abstraction

Provider-specific implementation must sit behind a common provider boundary.

Conceptually:

```text
EmailProvider
    ├── GmailProvider
    ├── MicrosoftProvider
    └── SMTPProvider
```

Potential capabilities include:

```text
send
validate_connection
refresh_credentials
get_account_status
classify_error
sync_messages
```

Not every provider will support every capability.

The provider interface must model capability differences explicitly rather than pretending all providers are identical.

---

# 37. Provider Capability Model

Providers may expose capabilities such as:

```text
outbound_send
reply_sync
webhook_events
message_lookup
credential_refresh
delivery_events
```

Campaign/domain logic must ask for capabilities through the provider abstraction.

It must not contain logic such as:

```python
if provider == "gmail":
    ...
elif provider == "microsoft":
    ...
```

throughout the application.

---

# 38. Provider Implementation Order

Implementation order:

```text
Gmail
    ↓
Microsoft
    ↓
Custom SMTP
```

Gmail proves the abstraction first.

Microsoft validates that the abstraction generalizes.

SMTP provides compatibility for accounts outside OAuth-provider paths.

---

# 39. Gmail Architecture

Gmail should use:

```text
OAuth
+
Gmail API
```

The system must securely manage:

- access authorization
- refresh capability
- connection state
- provider identifiers
- send failures
- synchronization capability

Gmail implementation details belong in provider documentation.

---

# 40. Microsoft Architecture

Microsoft should use:

```text
OAuth
+
Microsoft Graph
```

Microsoft-specific behavior stays behind the provider abstraction.

Campaign and message services must not directly call Graph-specific logic.

---

# 41. Custom SMTP Architecture

Custom SMTP is a compatibility path.

It does not imply:

```text
direct MX delivery
self-managed mail transfer agents
IP rotation infrastructure
provider-limit circumvention
```

SMTP configuration is treated as sensitive credential material.

---

# 42. Custom SMTP Network Security

User-controlled SMTP host configuration introduces a network-security boundary.

The backend must prevent SMTP configuration from becoming a generic SSRF/internal network probing mechanism.

The implementation must consider:

- private/internal IP restrictions
- loopback restrictions
- cloud metadata addresses
- DNS resolution validation
- DNS rebinding risk
- connection timeout limits
- port restrictions/policy
- outbound network controls

Detailed controls belong in security/provider implementation documentation.

---

# 43. Mailbox Credential Security

OAuth refresh tokens, SMTP passwords, and similar credential material must:

- never be returned to the browser after secure storage
- never appear in normal logs
- never appear in analytics
- never be committed to source control
- use encrypted storage
- be accessible only to services that require them

Credential access should be narrow and auditable.

---

# 44. Mailbox Health Model

Mailbox connection and mailbox sending eligibility are not identical concepts.

Conceptually:

```text
Connected
    does credentials/provider relationship exist?

Healthy
    does the connection currently function?

Eligible to Send
    may campaign execution currently use it?
```

Later provider/mailbox architecture must define the final state model.

---

# 45. Rate Limiting Architecture

Sending rate limiting exists to enforce safe operational limits.

It must consider applicable limits such as:

```text
mailbox limits
provider limits
configured campaign/workspace limits
platform safety limits
```

Durable configuration belongs in PostgreSQL.

High-frequency operational counters may live in Redis.

Conceptually:

```text
Worker requests capacity
    ↓
Rate limiter evaluates
    ↓
Allowed?
    ├── Yes → send
    └── No → defer
```

Rate limiting must delay work safely rather than discard it.

---

# 46. Distributed Rate-Limit Safety

Because multiple send workers may execute concurrently, rate-limit decisions must be atomic.

The system must not rely on:

```text
read Redis count
    ↓
increment later
```

without atomic coordination.

Exact Redis structures/scripts/token-bucket algorithms belong in `RATE_LIMITING.md`.

---

# 47. Reply Synchronization Architecture

Replies enter the system asynchronously.

Conceptually:

```text
Mailbox provider
    ↓
Webhook / sync mechanism
    ↓
Mailbox sync worker
    ↓
Normalize provider message
    ↓
Deduplicate
    ↓
Match outbound context
    ↓
Conversation
    ↓
Recipient campaign state
    ↓
Stop future applicable outreach
```

Reply matching must rely on stable identifiers and defensible matching rules.

The system must not fabricate campaign associations when matching is ambiguous.

---

# 48. Event Architecture

External and internal events should be normalized into durable business events where required.

Examples:

```text
message sent
message failed
reply received
bounce received
unsubscribe received
complaint received
mailbox disconnected
campaign started
campaign paused
```

Provider payloads should not become the canonical business model.

Instead:

```text
Provider Event
    ↓
Validation
    ↓
Normalization
    ↓
Domain Event / State Change
```

Raw provider data may be retained selectively where operationally useful and safe.

---

# 49. Webhook Architecture

Provider webhooks must be treated as untrusted external input.

Webhook processing should include:

```text
request received
    ↓
authenticate/verify provider signature where supported
    ↓
validate payload
    ↓
deduplicate
    ↓
acknowledge quickly
    ↓
enqueue/process asynchronously where appropriate
```

Long business processing should not block provider webhook responses unnecessarily.

---

# 50. Webhook Idempotency

External providers may deliver the same webhook multiple times.

The system must deduplicate using stable provider event/message identifiers where available.

Duplicate webhook delivery must not create:

- duplicate replies
- duplicate suppressions
- duplicate bounce records
- duplicate analytics
- repeated campaign stopping actions

---

# 51. Suppression Architecture

Suppression is a central domain service.

Every send path must use the same suppression rules.

Conceptually:

```text
Recipient
    ↓
Suppression Policy
    ↓
Eligible / Ineligible
```

Suppression may originate from:

```text
manual suppression
unsubscribe
permanent bounce
complaint
other approved safety rules
```

Exact scope and reversibility belong in `SUPPRESSION.md`.

---

# 52. Suppression Timing

Suppression should be evaluated both when useful during planning and again near actual send execution.

Reason:

```text
Message planned Monday
Recipient unsubscribes Tuesday
Message due Wednesday
```

Wednesday's message must not send merely because Monday's plan considered the recipient eligible.

---

# 53. Unsubscribe Architecture

Unsubscribe handling must create a durable state change.

The architecture must not represent unsubscribe only as:

```text
remove recipient from one campaign
```

Instead, unsubscribe feeds the suppression system according to the final suppression scope.

---

# 54. Bounce Architecture

Bounce processing should distinguish, where provider information permits:

```text
temporary delivery failure
permanent recipient failure
```

Permanent failures can create durable suppression or ineligibility according to product rules.

Temporary failures should not automatically create permanent suppression.

---

# 55. Complaint Architecture

A confirmed complaint is a safety-critical event.

Where supported:

```text
complaint
    ↓
durable event
    ↓
suppression / recipient safety state
    ↓
future sending prevented
```

Complaint events are not merely analytics metrics.

---

# 56. Unified Inbox Architecture

The unified inbox is a normalized view over synchronized conversations.

Conceptually:

```text
Provider Mailbox
    ↓
Normalized Messages
    ↓
Conversation Model
    ↓
Inbox API
    ↓
Frontend Inbox
```

The application should not require the frontend to understand Gmail or Microsoft message schemas.

---

# 57. Analytics Architecture

Analytics should be derived from durable business events/state.

The primary analytics model should not depend on parsing application logs.

Conceptually:

```text
Business Events
    ↓
Operational records / aggregates
    ↓
Analytics queries
```

Initial analytics may query transactional data directly where scale permits.

As volume grows, aggregate tables/materialized views or dedicated analytical pipelines may be introduced.

Do not introduce a separate analytics data warehouse before the workload justifies it.

---

# 58. Open Tracking Principle

If open tracking is implemented, it should be modeled as an estimated signal.

It must not become a fundamental campaign-success truth.

Higher-confidence outcomes include:

```text
reply
bounce
unsubscribe
complaint
```

Product analytics should reflect this distinction.

---

# 59. CSV Import Architecture

Large imports should be asynchronous.

Preferred flow:

```text
Frontend requests import
    ↓
File stored in object storage
    ↓
Import job created in PostgreSQL
    ↓
Worker processes file
    ↓
Rows validated
    ↓
Leads persisted safely
    ↓
Job result updated
```

Large uploaded files should not be stored as database blobs.

---

# 60. Object Storage

Object storage may be used for temporary or durable artifacts such as:

```text
CSV imports
generated exports
other approved file artifacts
```

Production recommendation:

```text
AWS S3 or equivalent managed object storage
```

Object storage must not become a shadow database for structured business state.

Metadata and processing state belong in PostgreSQL.

---

# 61. Import File Security

Uploaded CSV files must be treated as untrusted input.

The platform should enforce:

- size limits
- supported content types/formats
- parsing limits
- safe encoding handling
- row/column limits as appropriate
- temporary-file cleanup
- access-controlled object storage
- expiry/deletion policy where appropriate

CSV content must never be interpreted as executable code.

---

# 62. Platform Transactional Email

Product-owned transactional communication is separate from customer outreach.

Examples:

```text
password/reset-related mail
workspace invitation
security notification
platform notification
billing notification
```

These must not be sent through customer campaign mailboxes.

Architecture:

```text
Platform
    ↓
Dedicated transactional email provider
```

Customer outreach:

```text
Campaign
    ↓
Customer-connected mailbox/provider
```

The two systems remain logically separate.

---

# 63. Notification Architecture

Important operational notifications should originate from durable application conditions.

Conceptually:

```text
Mailbox failure
Import completion
Campaign blocked
Critical execution failure
    ↓
Notification event
    ↓
In-app notification
    ↓
Optional external notification channel
```

Notification delivery failure must not alter the underlying business state.

---

# 64. Auditability

Sensitive operations should create durable audit information.

Examples:

```text
workspace role changed
mailbox disconnected
ownership transferred
suppression manually removed
campaign started
campaign paused
critical admin intervention
API/security credentials changed
```

An audit event should capture sufficient context to answer:

```text
who
did what
to which resource
when
within which workspace
```

without storing sensitive secret material.

---

# 65. Logging

Production logging should be structured.

Recommended format:

```text
JSON structured logs
```

Useful context may include:

```text
request_id
task_id
workspace_id
campaign_id
message_id
mailbox_id
provider
operation
result
error_class
```

Logs should not contain:

```text
OAuth tokens
SMTP passwords
full authorization headers
sensitive API keys
raw email bodies by default
unnecessary recipient personal data
```

---

# 66. Correlation IDs

Requests and background work should be traceable across boundaries.

Conceptually:

```text
HTTP request
    ↓
request_id
    ↓
database command
    ↓
task published
    ↓
task correlation id
    ↓
worker
    ↓
provider attempt
```

Correlation identifiers should make it possible to investigate a failed workflow without searching unrelated logs manually.

---

# 67. Metrics

Production metrics should include operational signals such as:

```text
API request latency
API error rate
worker task success/failure
queue depth
queue age
scheduler lag
due-message lag
send attempt volume
send success/failure
provider error rates
rate-limit deferrals
mailbox sync lag
webhook processing failures
import duration/failure
database pool utilization
Redis health
```

Business metrics and operational infrastructure metrics should remain distinguishable.

---

# 68. Tracing

The architecture should support distributed tracing across:

```text
API
database
task publishing
worker execution
external provider requests
```

OpenTelemetry-compatible instrumentation is preferred to avoid unnecessary vendor lock-in.

Tracing should be sampled and privacy-conscious.

---

# 69. Error Tracking

Unhandled application exceptions should be collected centrally.

The platform should have a production error-tracking mechanism capable of:

- grouping repeated failures
- linking failures to deployment versions
- preserving correlation context
- alerting on new/severe regressions

Sensitive customer data must be scrubbed.

---

# 70. Health Endpoints

The backend should expose at least:

```text
liveness
readiness
```

semantics.

A basic first endpoint may be:

```http
GET /api/v1/health
```

But production health checks should distinguish:

```text
process alive
```

from:

```text
process ready to handle traffic
```

A database outage should affect readiness without necessarily requiring the process to terminate.

---

# 71. Backpressure

The platform must protect itself when downstream systems become slower than incoming work.

Examples:

```text
provider latency increases
Redis queue grows
database becomes constrained
reply synchronization falls behind
```

The system should respond through:

- queueing
- rate limiting
- worker concurrency control
- controlled retries
- circuit breaking where justified
- alerts

It must not create unbounded retry storms.

---

# 72. Retry Architecture

Retries must classify errors before retrying.

Conceptually:

```text
Operation fails
    ↓
Retryable?
    ├── No → terminal handling
    └── Yes
          ↓
      Retry policy
          ↓
      backoff + jitter
          ↓
      retry
```

Retry budgets should be bounded.

Permanent failures must not loop indefinitely.

---

# 73. Circuit Breaker Principle

Repeated failures against the same external dependency can indicate a systemic outage.

Where justified, the system should temporarily reduce or stop calls to an unhealthy dependency.

Potential scope:

```text
provider
provider account
mailbox
external integration
```

The exact circuit-breaker implementation belongs in provider/worker architecture.

---

# 74. Time and Timezone Architecture

All authoritative timestamps should be stored in UTC.

User-facing campaign schedules may use an explicit configured timezone.

Conceptually:

```text
User schedule
    timezone = America/New_York
    sending window = 09:00–17:00
```

Scheduler computes eligible UTC execution based on that timezone.

Never rely on:

- server-local timezone
- worker-local timezone
- browser timezone

as implicit campaign truth.

---

# 75. Clock Safety

The production infrastructure must use synchronized system clocks.

Time-sensitive logic includes:

- scheduled sends
- rate limiting
- token expiration
- retries
- OAuth expiry
- audit records

Small clock inconsistencies must not become silent sources of campaign behavior errors.

---

# 76. API Rate Limiting vs Email Rate Limiting

These are distinct concerns.

## API protection

Protects platform endpoints from:

- abuse
- accidental client loops
- excessive request load

## Email sending rate limits

Control:

- mailbox/provider throughput
- campaign sending
- outreach safety

They must not share one simplistic rate-limit mechanism.

---

# 77. Security Boundary

All inbound input is untrusted.

This includes:

```text
browser requests
CSV uploads
OAuth callbacks
SMTP configuration
provider webhook payloads
mailbox-synchronized content
email headers
recipient replies
```

Every boundary requires:

```text
validation
authorization where relevant
normalization
size limits
safe parsing
```

---

# 78. Secret Management

Production secrets should live in a managed secret store/environment injection system.

Examples:

```text
database credentials
Redis credentials
OAuth client secrets
provider API keys
encryption keys
transactional email credentials
```

Secrets must not be:

```text
committed to Git
embedded in Docker images
returned through API responses
stored in frontend environment variables
printed in logs
```

---

# 79. Encryption

Network traffic should use TLS.

Sensitive provider credential material requires encryption at rest beyond ordinary application access controls where appropriate.

Encryption keys must be separated from the encrypted credential records.

Key rotation should be possible without redesigning the mailbox model.

---

# 80. OAuth Security

OAuth flows must validate:

- state/correlation
- callback origin
- authorization result
- token exchange
- intended workspace/user context

OAuth callback endpoints must not trust arbitrary workspace identifiers supplied by the browser.

Temporary OAuth state should be:

```text
short-lived
tamper-resistant
single-use where appropriate
```

---

# 81. Web Security

The frontend/backend architecture must account for applicable web threats including:

```text
XSS
CSRF
session fixation
open redirect
clickjacking
injection
broken access control
```

Specific controls depend on the final authentication/session implementation.

Security headers should be configured centrally.

---

# 82. HTML Email Security

Customer-authored email content is untrusted input.

Any preview rendered in the product must be sanitized or isolated appropriately.

The system must avoid allowing campaign HTML to become an XSS vector inside the application UI.

---

# 83. Reply Content Security

Inbound email/reply content is also untrusted.

Rendering reply HTML requires sanitization.

Remote images or active content must not automatically expose users to unsafe browser behavior.

---

# 84. Personal Data Handling

Leads and conversations contain customer-controlled personal data.

The architecture should minimize unnecessary duplication.

Personal data should not be copied casually into:

- logs
- Redis caches
- analytics labels
- monitoring tags
- error messages

Structured identifiers should be preferred for diagnostics.

---

# 85. Deletion and Retention

Hard deletion, soft deletion, archival, and retention rules must be explicit for each major domain.

System architecture requires that deletion behavior consider:

- audit needs
- campaign history
- suppression safety
- regulatory obligations
- provider credentials
- generated files

Detailed retention rules belong in database/security specifications.

---

# 86. Production Deployment Architecture

Development uses Docker/Docker Compose.

Production direction is AWS-managed infrastructure without Kubernetes initially.

Recommended production topology:

```text
                         Internet
                            │
                            ▼
                    DNS / TLS / Edge
                            │
                            ▼
                   Application Load Balancer
                       │             │
                       ▼             ▼
               Next.js Frontend   FastAPI API
                                      │
                    ┌─────────────────┼─────────────────┐
                    │                 │                 │
                    ▼                 ▼                 ▼
              Celery Workers      Scheduler          Redis
                    │
                    ▼
             Supabase PostgreSQL
                    │
                    ▼
             Object Storage / S3

External:
    Gmail API
    Microsoft Graph
    SMTP servers
    Transactional email provider
```

---

# 87. Recommended AWS Runtime

A production-ready initial AWS approach is:

```text
ECS / Fargate
```

for containerized runtime services.

Separate task/service definitions can run:

```text
frontend
backend
worker-send
worker-sync
worker-general
scheduler
```

Benefits:

- no Kubernetes operational burden
- independent scaling
- managed restarts
- deployment health checks
- clear resource allocation
- straightforward Docker workflow

---

# 88. Production Redis

Production Redis should use a managed service rather than an unmanaged Redis container on the application host.

Recommended AWS direction:

```text
ElastiCache for Redis-compatible workloads
```

or another production-grade managed Redis service.

Redis persistence should not be relied upon for core business correctness.

---

# 89. Production Database

The primary production PostgreSQL instance remains managed through the chosen Supabase architecture.

Application services connect through secure network/database credentials appropriate to their responsibilities.

Schema changes remain controlled exclusively through:

```text
supabase/migrations/
```

---

# 90. Database Connection Pooling

API and worker processes must use bounded connection pools.

Worker scaling must not create unbounded PostgreSQL connections.

Architecture must account for:

```text
API replicas
send workers
sync workers
general workers
scheduler
```

collectively consuming database connections.

Connection limits must become part of production capacity planning.

---

# 91. Deployment Isolation

Production, staging, and local environments must not share:

```text
database
Redis
OAuth callback configuration
secrets
object storage prefixes/buckets where unsafe
provider credentials
```

Staging must not accidentally send production customer campaigns.

---

# 92. Environment Model

Recommended environments:

```text
local
staging
production
```

Local development:

```text
Docker Compose
managed/external Supabase development project where appropriate
```

Staging:

```text
production-like managed infrastructure
separate Supabase project/database
separate credentials
```

Production:

```text
fully isolated production services
```

---

# 93. Deployment Strategy

Application deployments should be:

```text
repeatable
versioned
automated
reversible where possible
```

Backend and workers should normally deploy compatible application versions.

Schema changes must respect application compatibility during rollout.

---

# 94. Migration Deployment Compatibility

Database migrations should prefer changes that allow safe rolling deployment.

Preferred pattern:

```text
expand
    ↓
deploy compatible application
    ↓
backfill if required
    ↓
switch behavior
    ↓
later contract/remove old structure
```

Avoid migrations that instantly break the currently running application.

---

# 95. CI/CD Architecture

CI should validate every significant repository change.

Frontend:

```text
TypeScript
lint
tests
production build
```

Backend/workers:

```text
lint
format verification
type checking
tests
```

Repository/infrastructure:

```text
Docker image builds
migration/static checks
secret scanning
dependency/security checks
```

Production deployment should occur only after required validation succeeds.

---

# 96. Container Design

Containers should be immutable.

Do not:

```text
SSH into production container
edit source
install ad hoc dependencies
manually patch running code
```

Changes go through:

```text
Git
    ↓
CI
    ↓
image build
    ↓
deployment
```

---

# 97. Scheduler Deployment

The scheduler can initially run with:

```text
desired replicas = 1
```

for operational simplicity.

However, correctness must not depend on it being globally unique forever.

The database claiming model should eventually allow multiple scheduler instances without duplicate execution.

---

# 98. Worker Scaling

Worker types scale independently.

Example:

```text
send backlog increases
    ↓
scale worker-send
```

without scaling:

```text
worker-sync
frontend
backend
```

Similarly, slow mailbox synchronization can scale independently.

---

# 99. API Scaling

FastAPI instances should remain stateless with respect to durable user/business state.

Any replica should be able to serve a valid request.

Do not store critical session or workflow state only in API process memory.

---

# 100. Frontend Scaling

Next.js application instances should similarly avoid server-local business state.

Persistent application state remains in backend/database systems.

---

# 101. Failure Model: Redis Outage

Expected behavior:

```text
Redis unavailable
    ↓
new async work may temporarily stop queueing
workers may stop receiving work
    ↓
PostgreSQL durable state remains
    ↓
Redis recovers
    ↓
scheduler/workers rediscover/resume eligible work
```

The system may experience delay.

It must not experience business-state loss.

---

# 102. Failure Model: Worker Crash

```text
Worker executing task
    ↓
process crashes
    ↓
durable message state remains
    ↓
task/redelivery/reconciliation occurs
    ↓
idempotency prevents unsafe duplicate effect
```

---

# 103. Failure Model: Scheduler Crash

```text
Scheduler stops
    ↓
future work remains in PostgreSQL
    ↓
scheduler restarts
    ↓
overdue eligible work discovered
```

The scheduler does not need to reconstruct campaign plans from memory.

---

# 104. Failure Model: API Outage

During API outage:

```text
frontend mutations unavailable
```

but already queued/durable worker processing may continue if its dependencies remain healthy.

API recovery should not require repairing campaign state manually.

---

# 105. Failure Model: Provider Outage

```text
Provider failures increase
    ↓
errors classified
    ↓
bounded retry/backoff
    ↓
circuit-breaking/deferral where appropriate
    ↓
operational alert
```

The platform must not respond to a provider outage with thousands of immediate tight-loop retries.

---

# 106. Failure Model: Database Outage

PostgreSQL is a critical dependency.

During database unavailability:

```text
API should fail readiness
workers should avoid unsafe external effects that cannot be persisted
scheduler stops producing work
```

For email sending specifically:

> If the platform cannot reliably determine and persist authoritative message state, it should prefer delaying the send rather than creating untracked external effects.

---

# 107. Failure Model: Ambiguous Provider Send Result

One of the hardest cases:

```text
send request issued
    ↓
network timeout
    ↓
unknown whether provider accepted request
```

The architecture must not blindly retry as if failure were certain.

Provider architecture must define:

```text
reconciliation
provider lookup where possible
attempt identifiers
safe retry rules
```

Ambiguous outcomes must be explicit message states or execution conditions.

---

# 108. Disaster Recovery

The system's disaster-recovery strategy is centered on PostgreSQL.

Required capabilities before production include:

```text
database backups
restore procedure
backup verification
object-storage recovery policy
secret recovery/rotation procedure
infrastructure recreation from configuration
```

Redis recovery is operational rather than business-data recovery.

The platform should be reconstructable from:

```text
source repository
infrastructure configuration
database backups
managed secrets
object storage
```

---

# 109. Recovery Testing

Backups are not sufficient unless restoration is tested.

Before mature production operation, the team should periodically validate:

```text
database restore
critical secret rotation
service recreation
scheduler recovery
worker recovery
```

Exact RTO/RPO targets should be defined with actual business requirements rather than invented prematurely.

---

# 110. Scalability Model

Scale initially through:

```text
horizontal API replicas
horizontal worker replicas
queue-specific worker scaling
database indexes/query optimization
Redis capacity
managed infrastructure scaling
```

Do not introduce sharding or microservices before evidence requires them.

---

# 111. First Scaling Pressure: Sending Workers

Email-send throughput can scale by increasing send-worker concurrency only within:

```text
provider limits
mailbox limits
database capacity
Redis capacity
platform safety limits
```

More workers must never imply unlimited sending.

The rate limiter remains authoritative.

---

# 112. Second Scaling Pressure: Scheduler

Scheduler scalability depends on efficient due-work queries.

Database design must support indexes around the eventual due-message access pattern.

Conceptually:

```text
status
scheduled_at
workspace/campaign where relevant
```

Exact indexes belong in database design.

---

# 113. Third Scaling Pressure: Mailbox Sync

Mailbox synchronization can become expensive as connected accounts grow.

It should therefore have:

```text
separate queues
independent concurrency
sync cursors/checkpoints
idempotent processing
backoff
provider-aware scheduling
```

Detailed design belongs in `REPLY_SYNC.md`.

---

# 114. Analytics Scaling

Do not optimize analytics prematurely.

Progression:

```text
transactional queries
    ↓
indexed aggregate queries
    ↓
aggregate tables/materialized views
    ↓
dedicated analytical store only if justified
```

Campaign sending correctness must not depend on analytical processing completing synchronously.

---

# 115. Cache Strategy

Caching is optional optimization.

Do not cache business state merely to avoid correct database design.

Suitable cache candidates may include:

```text
short-lived configuration
non-critical computed summaries
provider metadata
```

Unsafe cache candidates include authoritative:

```text
suppression state
message sent state
campaign execution state
```

unless the database remains authoritative and the correctness model tolerates stale cache safely.

---

# 116. Strong vs Eventual Consistency

Some operations require strong/transactional consistency.

Examples:

```text
workspace authorization
suppression creation
campaign transition
message send claim
idempotency
```

Other views may tolerate eventual consistency:

```text
analytics dashboards
notification counts
some aggregated deliverability metrics
```

The architecture should intentionally choose consistency according to business impact.

---

# 117. Synchronous vs Asynchronous Rule

Use synchronous API execution for operations that are:

```text
fast
bounded
required before returning a truthful result
```

Use asynchronous processing for operations that are:

```text
slow
external
large
retryable
scheduled
batch-oriented
```

Examples:

Synchronous:

```text
create campaign draft
update lead
validate authorization
```

Asynchronous:

```text
CSV import
bulk message planning where large
email sending
mailbox sync
analytics processing
notification delivery
```

---

# 118. Transaction Boundary Rule

Database transactions should represent meaningful atomic application operations.

Do not hold database transactions open while waiting for long external provider requests unless a specific design proves it necessary.

Preferred pattern:

```text
claim/prepare state transactionally
    ↓
commit
    ↓
external operation
    ↓
record result transactionally
```

with explicit handling for uncertain outcomes.

---

# 119. No Distributed Transactions

The architecture does not depend on distributed two-phase commits between:

```text
PostgreSQL
Redis
Gmail
Microsoft
SMTP
```

Consistency is achieved through:

```text
durable states
idempotency
retries
reconciliation
```

---

# 120. Outbox Pattern Direction

For business operations where a database state change must reliably produce asynchronous work, an outbox-style pattern should be considered.

Conceptually:

```text
Database transaction
    ├── business state
    └── pending internal event/outbox record
                ↓
          publisher/dispatcher
                ↓
              queue
```

This prevents the classic failure:

```text
database commit succeeds
queue publish fails
```

The exact use of an outbox should be defined in `EVENT_SYSTEM.md` / `QUEUES.md`.

For production reliability, critical workflows such as message planning should not rely on an unprotected dual-write between PostgreSQL and Redis.

---

# 121. Inbox Pattern Direction

Inbound external events should similarly be deduplicated before business processing.

Conceptually:

```text
Provider event
    ↓
durable receipt/dedupe
    ↓
processing
    ↓
domain effects
```

Exact implementation belongs in event architecture.

---

# 122. Architecture Around Redis Failure

The outbox/scheduler design should make Redis replaceable.

A complete Redis flush should cause:

```text
temporary processing disruption
```

not:

```text
lost campaigns
lost messages
lost suppressions
lost replies
unknown business state
```

---

# 123. Deployment Version Compatibility

Workers and API may temporarily run different application versions during rolling deployment.

Schema/API/domain evolution should therefore avoid requiring every process to switch at exactly the same millisecond.

Database migrations and task payloads should support controlled compatibility windows.

---

# 124. Task Payload Design

Queue tasks should carry stable identifiers rather than entire authoritative business objects.

Preferred:

```json
{
  "message_id": "..."
}
```

rather than embedding a complete copy of:

```text
campaign
lead
mailbox
template
suppression
```

Workers load current state from PostgreSQL.

This prevents stale queue payloads from overriding current business truth.

---

# 125. Task Payload Security

Task payloads should not contain secrets.

Avoid queueing:

```text
OAuth tokens
SMTP passwords
full provider credentials
```

Workers resolve protected credentials at execution time through secure services/storage.

---

# 126. Queue Separation Principle

Queues should represent operational workload classes.

Potential examples:

```text
email.send
email.retry
mailbox.sync
reply.sync
imports
webhooks
notifications
maintenance
```

Exact names and routing belong in `QUEUES.md`.

Queue design should enable:

- workload isolation
- independent concurrency
- independent scaling
- protection of latency-sensitive tasks

---

# 127. Priority

Priority should be used sparingly.

Do not solve poor queue design by assigning arbitrary priorities to every task.

Critical interactive/safety work may require isolation rather than merely higher numeric priority.

---

# 128. Dead Work

With Redis/Celery, durable business failures must be represented in PostgreSQL rather than depending solely on a broker-specific dead-letter concept.

When work reaches terminal failure:

```text
message/job durable state
    ↓
failure metadata
    ↓
operational visibility
```

The system must be able to identify and investigate terminal work.

---

# 129. Administrative Intervention

Internal operators may need controlled actions to:

```text
disable sending
restrict workspace
inspect failure
requeue safe operation
resolve operational incident
```

These should eventually use explicit admin/domain commands.

Avoid normalizing:

```text
open production database
manually edit row
```

as the platform's operational interface.

---

# 130. Abuse Controls

A production outreach platform requires mechanisms for platform-level safety.

The architecture should support:

```text
workspace sending restriction
mailbox restriction
campaign restriction
rate reduction
account investigation
audit trail
```

Exact abuse detection policies remain outside this document.

---

# 131. Feature Flags

Feature flags may be useful for:

```text
controlled rollout
provider rollout
risky behavior changes
beta features
```

They must not become a substitute for:

```text
authorization
billing enforcement
security policy
```

The initial system does not require a complex feature-flag platform.

---

# 132. Configuration Ownership

Configuration should have a clear owner.

```text
Environment config
    deployment-level

Workspace config
    workspace-wide behavior

Mailbox config
    one mailbox

Campaign config
    one campaign

Provider config
    provider implementation/system
```

Do not duplicate the same configuration value across multiple levels without a defined precedence model.

---

# 133. Email Content Rendering

Email content should be rendered from authoritative template/sequence data in a controlled service.

Rendering must account for:

```text
lead variables
campaign content
fallback behavior
escaping/sanitization
unsubscribe content where applicable
```

Final content should be determined in a way that supports reproducibility and debugging.

The detailed snapshot/render strategy belongs in domain/message architecture.

---

# 134. Template Snapshot Principle

Running campaign behavior should not unpredictably change because a reusable template was edited later.

The architecture should strongly prefer capturing the relevant campaign content/version at an appropriate point.

The exact snapshot timing should be defined in the domain/message design.

---

# 135. Deterministic Execution

When possible, a message execution should be explainable from:

```text
campaign configuration/version
recipient state
mailbox state
schedule
suppression
message record
provider attempt
```

The platform should avoid hidden mutable behavior that makes historical sends impossible to reconstruct.

---

# 136. Database Constraints as Safety

Important invariants should be enforced at the database level where appropriate.

Examples may include:

```text
unique workspace membership
unique provider identifiers
unique idempotency records
valid foreign-key ownership
non-null workspace relationships
```

Application validation is necessary but not sufficient.

---

# 137. Referential Integrity

Foreign keys should be used for meaningful relationships unless a specific technical reason prevents them.

Do not sacrifice integrity prematurely for hypothetical performance.

Deletion behavior must be intentional:

```text
RESTRICT
CASCADE
SET NULL
```

must be chosen according to domain meaning.

---

# 138. Identifier Strategy

Business resources should use opaque globally unique identifiers suitable for distributed/runtime use.

The exact identifier format will be defined in database standards.

Client-facing identifiers must not leak authorization assumptions.

---

# 139. Pagination

Collection APIs must be pagination-ready from the beginning.

Large entities include:

```text
leads
campaign recipients
messages
conversations
events
imports
```

Do not design endpoints that permanently assume entire workspace datasets fit in one response.

---

# 140. Background Job Progress

Long-running jobs should expose persisted progress/status through the database.

Frontend polls or subscribes to server state.

The browser is never the authoritative owner of:

```text
import progress
campaign progress
sync progress
```

---

# 141. Real-Time UI Updates

Real-time UX is optional optimization.

Initial options may include:

```text
polling
server-sent events
WebSocket
approved realtime infrastructure
```

The first implementation should choose the simplest mechanism satisfying UX needs.

Realtime transport must not become required for business correctness.

---

# 142. API Timeout Principle

Interactive APIs should remain bounded.

Operations likely to exceed normal API response time should return a durable job/resource and continue asynchronously.

Avoid setting extremely long HTTP timeouts to mask incorrect synchronous architecture.

---

# 143. Provider Timeout Policy

External provider calls require explicit:

```text
connect timeout
read timeout
overall operation timeout
```

A provider call must not block worker capacity indefinitely.

---

# 144. External Dependency Isolation

Failures in:

```text
Gmail
Microsoft
SMTP host
transactional notification provider
analytics/error-monitoring vendor
```

must not unnecessarily crash unrelated product functionality.

For example:

A transactional notification-provider outage must not stop campaign execution.

---

# 145. Operational Runbooks

Before production launch, the team should maintain runbooks for:

```text
database outage
Redis outage
provider outage
mailbox token failure
queue backlog
worker crash loop
scheduler failure
failed deployment
migration failure
suspected duplicate sending
security incident
```

Architecture is incomplete operationally if nobody knows how to respond to predictable failures.

---

# 146. Non-Goals

The initial system will not use:

```text
Kubernetes
Kafka
service mesh
dozens of microservices
database-per-module
event sourcing as universal persistence
direct-to-MX email delivery
random VPS IP rotation
custom MTA infrastructure
multi-region active-active architecture
premature database sharding
```

These may only be reconsidered when measured requirements justify them.

---

# 147. Architectural Invariants

The following are permanent system-level rules unless intentionally changed through an architecture decision.

## Invariant 1 — PostgreSQL is durable truth

Redis is never the only copy of critical campaign state.

## Invariant 2 — Backend owns business rules

Frontend and workers do not independently redefine them.

## Invariant 3 — Workers reload authoritative state

A queue payload is not trusted as current business truth.

## Invariant 4 — Suppression is checked near send time

Planning eligibility is insufficient.

## Invariant 5 — External side effects are idempotency-aware

Retries must not knowingly create duplicates.

## Invariant 6 — Workspace isolation is enforced server-side

Client-supplied workspace IDs never grant access.

## Invariant 7 — Provider restrictions are respected

The architecture does not bypass them through IP tricks or similar mechanisms.

## Invariant 8 — Email sending is asynchronous

HTTP request handlers do not execute campaign-scale sending.

## Invariant 9 — Future messages are durable

A queue alone does not represent future campaign intent.

## Invariant 10 — Provider details stay behind adapters

Campaign logic remains provider-agnostic.

## Invariant 11 — Migrations live only in `supabase/migrations/`

No Alembic.

## Invariant 12 — Product transactional email is separate

Customer mailboxes do not send platform account notifications.

---

# 148. Critical Architecture Flow

The full production execution path is:

```text
┌────────────────────────────────────┐
│            Next.js UI              │
└─────────────────┬──────────────────┘
                  │ HTTPS
                  ▼
┌────────────────────────────────────┐
│              FastAPI               │
│                                    │
│ Authentication                     │
│ Workspace Context                  │
│ Authorization                      │
│ Application Services               │
│ Domain Rules                       │
└─────────────────┬──────────────────┘
                  │
                  ▼
┌────────────────────────────────────┐
│       Supabase PostgreSQL          │
│                                    │
│ Durable Business State             │
│ RLS / Constraints                  │
│ Message Intent                     │
│ Events                             │
└─────────────────┬──────────────────┘
                  │
                  │ due durable work
                  ▼
┌────────────────────────────────────┐
│             Scheduler              │
└─────────────────┬──────────────────┘
                  │
                  ▼
┌────────────────────────────────────┐
│               Redis                │
│        Queue / Coordination        │
└─────────────────┬──────────────────┘
                  │
                  ▼
┌────────────────────────────────────┐
│              Worker                │
│                                    │
│ Reload current state               │
│ Validate eligibility               │
│ Check suppression                  │
│ Check rate limits                  │
└─────────────────┬──────────────────┘
                  │
                  ▼
┌────────────────────────────────────┐
│          Provider Adapter          │
└───────────┬─────────┬──────────────┘
            │         │
        Gmail      Microsoft       SMTP
            │         │             │
            └─────────┴─────────────┘
                      │
                      ▼
               Provider Result
                      │
                      ▼
             PostgreSQL Event /
                State Update
```

---

# 149. Reply Architecture Flow

```text
Recipient Reply
      ↓
External Mailbox Provider
      ↓
Webhook / Sync
      ↓
Sync Worker
      ↓
Provider Adapter
      ↓
Normalize
      ↓
Deduplicate
      ↓
Match Conversation
      ↓
Persist Reply
      ↓
Update Recipient Campaign State
      ↓
Stop Applicable Future Messages
      ↓
Update Inbox / Analytics
```

---

# 150. Unsubscribe Architecture Flow

```text
Recipient Unsubscribe
      ↓
Unsubscribe Endpoint / Event
      ↓
Validate
      ↓
Resolve Recipient
      ↓
Persist Unsubscribe
      ↓
Create/Update Suppression
      ↓
Future message becomes due
      ↓
Worker re-checks suppression
      ↓
NO SEND
```

---

# 151. Recovery Architecture Flow

```text
Redis / Worker / Scheduler Failure
      ↓
Transient processing stops
      ↓
PostgreSQL remains authoritative
      ↓
Infrastructure recovers
      ↓
Scheduler rediscovers due work
      ↓
Workers reload current state
      ↓
Idempotency checks
      ↓
Safe continuation
```

---

# 152. Architecture Documents That Refine This File

The next documents should refine this system architecture in this order:

```text
SYSTEM_ARCHITECTURE.md
        ✓
        ↓
DOMAIN_MODEL.md
        ↓
CAMPAIGN_STATE_MACHINE.md
        ↓
MESSAGE_STATE_MACHINE.md
        ↓
PROVIDER_ARCHITECTURE.md
        ↓
SCHEDULER.md
        ↓
WORKERS.md
        ↓
QUEUES.md
        ↓
RATE_LIMITING.md
        ↓
EVENT_SYSTEM.md
        ↓
REPLY_SYNC.md
        ↓
SUPPRESSION.md
        ↓
DATABASE.md
```

Each document may add detail.

It must not silently violate the system-level invariants defined here.

---

# 153. Architecture Decision Records

Important decisions that materially change this architecture should receive an ADR.

Examples:

```text
Use Supabase migrations instead of Alembic
Use modular monolith
PostgreSQL as durable schedule
Provider abstraction
Use Redis/Celery
Choose deployment runtime
Introduce outbox pattern
Change authentication architecture
Extract a service
```

Recommended location:

```text
docs/adr/
```

An ADR records:

```text
context
decision
alternatives
consequences
status
```

---

# 154. Gate Before Database Design

Do not design the full database immediately from this document.

First define:

```text
DOMAIN_MODEL.md
CAMPAIGN_STATE_MACHINE.md
MESSAGE_STATE_MACHINE.md
PROVIDER_ARCHITECTURE.md
SCHEDULER.md
WORKERS.md
QUEUES.md
RATE_LIMITING.md
EVENT_SYSTEM.md
REPLY_SYNC.md
SUPPRESSION.md
```

These specifications determine what durable data and constraints the database must support.

---

# 155. Gate Before Initial Migration

`0001_initial.sql` must not attempt to implement the entire platform.

After the database architecture is defined, initial migration work should begin incrementally.

The first migration should focus on foundational identity/tenancy structures as approved by the final database design.

Every migration requires explicit review before execution.

---

# 156. Gate Before Application Feature Development

Feature implementation should begin only after the relevant domain has:

```text
product requirement
architecture specification
state rules where applicable
database model
migration
API contract
test expectations
```

The agent should not discover architecture while writing production code.

---

# 157. Definition of Done for SYSTEM_ARCHITECTURE.md

This system architecture is complete when the project accepts:

```text
✓ Modular monolith architecture
✓ Frontend/backend boundary
✓ Worker responsibility
✓ Domain logic ownership
✓ PostgreSQL durable-truth model
✓ Redis coordination model
✓ Supabase migration ownership
✓ Multi-tenant security strategy
✓ Asynchronous execution model
✓ Scheduler responsibility
✓ Message durability model
✓ Pre-send eligibility gate
✓ Idempotency requirement
✓ External side-effect handling
✓ Provider abstraction
✓ Gmail / Microsoft / SMTP boundaries
✓ Reply/event model direction
✓ Suppression architecture
✓ Rate-limit architecture
✓ Analytics direction
✓ Upload/import architecture
✓ Logging/metrics/tracing requirements
✓ Deployment direction
✓ Scaling direction
✓ Failure/recovery model
✓ Security boundaries
✓ Architectural invariants
✓ Explicit non-goals
✓ Documentation refinement order
```

---

# 158. Final Architecture Principle

The most important architectural rule is:

> Durable business state decides what the system should do; queues and workers only help the system do it.

Therefore:

```text
PostgreSQL
    knows the truth

Backend
    decides what is allowed

Scheduler
    discovers what is due

Redis
    coordinates execution

Workers
    perform work

Providers
    perform external email operations

Events
    record what happened

Frontend
    presents the result
```

This division of responsibility should remain clear throughout the codebase.

The system is production-ready architecturally when a process, queue, provider, or deployment can fail without causing the application to lose track of what should happen next.