# Security Architecture


## Purpose and authority

This document consolidates the security design required by [SYSTEM_ARCHITECTURE §§23–25, 42–43, 77–85](../architecture/SYSTEM_ARCHITECTURE.md), [MVP release criteria](../product/MVP.md), and [DATABASE](../database/DATABASE.md). It defines controls and test obligations, not a claim that controls are implemented or an assertion of legal compliance.

The approved [USER_ROLES](../product/USER_ROLES.md) is the permission authority. [USER_FLOWS](../product/USER_FLOWS.md) owns journeys. Unknown actions deny. Database capabilities and the transaction/connection contract are defined in [DATABASE](../database/DATABASE.md); backend implementation and runtime verification remain required.

## Trust boundaries

| Boundary | Trusted component and required checks |
|---|---|
| Browser → API | API verifies identity, membership, action permission, resource ownership, current state, input shape/size and command idempotency. Client visibility/IDs are never authority. |
| Browser → Supabase Auth | Auth owns login/recovery/session identity. Mailbox OAuth is a separate flow. Core business tables have no direct browser write grants. |
| API/worker → PostgreSQL | Dedicated least-privilege roles, TLS, parameterized queries, transaction-local context, RLS and composite ownership FKs. |
| Producer → Redis/Celery → worker | Private authenticated broker, registered task/envelope allowlist, primitive safe serialization, current durable state revalidation. |
| Provider → webhook/sync | Verify source/account, bound payload, deduplicate, normalize; external fields cannot select tenant authority. |
| User host → SMTP/IMAP egress | Public-destination/port/TLS policy, DNS rebinding prevention, network enforcement and time/resource bounds. |
| Object storage → import worker | Immutable object version/digest, tenant access, content/size/parser limits and safe output. |
| Operator → platform control | Separate internal identity/capability, strong authentication, explicit reason and immutable audit; workspace Admin is not platform operator. |

## Authentication and session handling

Validate Supabase-issued identity tokens on the backend using trusted issuer/key material, intended audience, signature algorithm allowlist, expiry/not-before and appropriate session checks. Refresh signing keys from configured trusted endpoints only; never follow arbitrary token-provided URLs. Distinguish invalid identity from unavailable key service without falling back to unsigned decode. Cache keys with bounded refresh; reject unknown keys when verification cannot succeed.

Recommended browser session transport is secure HttpOnly cookies with SameSite policy and explicit CSRF protection for cookie-authenticated mutation routes. If a bearer-token frontend flow is approved instead, do not store long-lived secrets in application storage or assume CORS is authorization. Exact session transport is an Open Decision. Logout, expired/revoked session and recovery redirects must be tested. Recovery responses should not enumerate existing accounts. Approved redirect destinations only; no untrusted return URL forwarding.

Authentication success establishes user identity only. Workspace selection revalidates membership and clears stale frontend workspace data. Membership removal takes effect on the next authoritative read/write and invalidates any privileged continuation that requires the user. Background campaign execution is a workspace-owned authorization granted at activation, not an unbounded personal token; workspace restriction, mailbox revocation, safety facts and explicit current rules remain gates. Controlled test sends recheck current requester authorization.

## Authorization and tenant isolation

For every operation: current identity → active membership → specific action capability → composite workspace/resource lookup → lifecycle guard. Lists, imports, mailboxes, attempts, conversations, search, analytics, exports, files and job status all follow the same boundary. Return neutral not-found where necessary to avoid cross-tenant existence disclosure. Server-side pagination must never fetch all tenants and filter in frontend.

Role grants, owner transfer, last-owner protection, invitation assignment limits, suppression removal, mailbox disconnect and platform intervention must be explicitly mapped in restored product RBAC. Recommended bootstrap creates workspace + initial ownership in one restricted command; invitation acceptance binds intended identity and role, checks expiry and consumes token once. It cannot use an unrestricted generic insert endpoint or permit arbitrary Owner assignment. Exact owner policy remains approval-gated.

RLS reinforces application authorization. Use the [database RLS/pooling design](../database/DATABASE.md): non-owner/non-BYPASSRLS normal API role, default-deny missing context, transaction-local user/workspace settings and both visibility/write checks. Current membership helper avoids recursive RLS and returns only facts. Context setter is a trusted backend boundary, not protection against arbitrary code execution in that backend. Runtime roles cannot create/alter policy or schema. Worker/admin authority comes from authenticated service/login identity and narrow grants, never a user-editable context string.

Global due-work discovery, platform suppression boolean checks and rate aggregation use narrowly scoped internal functions, fixed search_path, explicit schema qualification, revoked PUBLIC execution and tightly controlled owners. They return only required IDs/counts/eligibility, not full cross-tenant data. Routine API requests never connect as Supabase service-role/superuser. Pool tests must demonstrate no context leakage after commit, rollback, exceptions or reused connections.

## Provider secrets and OAuth

Store OAuth/SMTP/IMAP secrets as authenticated-encryption envelopes with key IDs/nonces and associated workspace/mailbox/provider context. Encryption keys are outside the database in managed secrets/KMS-equivalent configuration; exact service remains an infrastructure decision. Separate app/environment secrets from per-mailbox encrypted credentials. Database backups alone must not expose plaintext mailbox tokens. Key rotation supports decrypt-old/encrypt-new transitions and audited completion without printing secrets.

Only connection/send/sync services receive decryption capability. API DTOs, worker envelopes, diagnostics, analytics, traces and logs never contain stored secrets. Secure temporary material has short lifetime and restricted access. Avoid secret-bearing URL/query logs and scrub provider SDK errors/request bodies. Local/staging keys and accounts are distinct from production.

OAuth initiation checks mailbox management permission and binds state to user/workspace/provider, expiry, return destination and PKCE where supported. Callback verifies this binding, current session/membership, issuer/provider response and single-use state. Exchange happens server-side; do not trust a browser-supplied workspace/account ID. Reconnect must verify the same provider account identity. Refresh is generation/lease-controlled, and late refresh cannot undo disconnect. Rotation persistence failure may require reconnect, never silent credential downgrade. See [PROVIDER_ARCHITECTURE](../architecture/PROVIDER_ARCHITECTURE.md) for operational protocol.

## Webhooks, unsubscribe and public endpoints

Verify provider signature or documented token/authentication on raw bounded request bytes where required, including replay/timestamp checks where supported. Compare secrets safely, rotate with bounded overlap, and reject unsupported source identity. Resolve subscription/account server-side. Signed transport alone does not make arbitrary payload fields valid. Persist verified receipt/work before acknowledgment; apply pending-safety holds as defined in [EVENT_SYSTEM](../architecture/EVENT_SYSTEM.md). Never trust arbitrary provider headers as a generic authentication scheme.

Unsubscribe uses a purpose-bound opaque token mapped to exactly one workspace/address. Do not accept address/workspace replacements from request parameters. Success follows durable suppression commit; duplicates succeed without new effects. Token values must not enter referrer/analytics logs; use a restricted referrer policy and avoid third-party assets on token pages. Link-scanning/one-click behavior and lifetime require product/provider review. Rate protection must not silently drop valid opt-outs. No legally unsupported claims are made here about regional requirements.

## SMTP/IMAP SSRF and external URL safety

Apply [provider network controls](../architecture/PROVIDER_ARCHITECTURE.md) consistently to validation, connection tests, send, retry and sync. Validate all IPv4/IPv6 answers; deny loopback/private/link-local/metadata/multicast/unspecified and configured internal destinations, including mapped addresses. Resolve each connection, pin the permitted address, preserve hostname certificate/SNI verification, and reject DNS rebinding. Allow only approved submission/receive ports and mandatory TLS; no plaintext downgrade, arbitrary proxy or caller-controlled trust store. Egress controls enforce the same boundary independently of validation.

Limit DNS/TCP/TLS/auth time, concurrent tests, response size and error detail so testing cannot become an internal scanner. Remote provider cursors and OAuth URLs must stay on configured provider origins. Any future link preview, tracking redirect, attachment fetch or enrichment integration requires its own URL/egress policy; do not implicitly fetch user-provided email URLs. The first such integration, website research for personalization, is governed by [ADR-0013](../adr/0013-research-sources-and-outbound-fetch.md) (https/443 only, all-answer IP validation with pinning, bounded redirects/size/time, no proxy, per-tenant cache). Sending lead data to an LLM provider is governed by [ADR-0012](../adr/0012-llm-port-data-handling-and-validation.md): field allow-list (never email/phone/LinkedIn), model output and website text treated as untrusted, key confined to one worker group.

## HTML, MIME, CSV and web input

Treat template variables, authored HTML, inbound bodies and headers as untrusted. Escape variables according to output context; disallow arbitrary template evaluation, filesystem/network access and executable expressions. Prevent header injection in subject, addresses and sender fields. Bound MIME depth, part count, decoded size and attachment expansion. Inbound HTML never executes in application origin. Outbound email bodies authored in the sequence editor are stored through an allow-list sanitizer (tags, `href` http/https/mailto, `src` https/`cid:`, a fixed set of inline style properties; a link may not begin with a template variable). Step attachments follow [ADR-0010](../adr/0010-step-attachments-and-inline-images.md): a private bucket reached only with the server key, extension allow-list plus content inspection, per-file/per-step/count limits, SHA-256 verification at send, tenant-scoped immutable rows, and no fetch of any user-supplied URL.

Use a maintained allowlist sanitizer and isolated preview where appropriate; remove script, event attributes, unsafe schemes, forms and active embedded content. Block remote images by default in inbox/preview to avoid tracking/data leakage; any image proxy needs independent SSRF control. CSP, anti-framing headers, strict content types, safe links and safe redirects are centrally configured. Parameterized SQL and validated sort/filter allowlists prevent injection; never build SQL identifiers from unchecked requests.

CSV uploads require authenticated ownership, bounded byte/row/column/field sizes, accepted encoding, actual parsing validation and immutable storage version/digest after confirmation. Do not infer safety solely from extension or MIME header. Stream chunks with memory/time limits. Formula-like cells are data, never executed; exports intended for spreadsheets neutralize formula injection. Object keys are generated server-side, not trusted filenames; no path traversal. Error files must redact unnecessary PII and remain tenant-protected. No executable macro/import evaluator is introduced.

## Object storage, PII and telemetry

Private storage with workspace/job-scoped access; short-lived signed uploads/downloads must be bound to approved object, operation and size. Reauthorize download issuance and verify object metadata/content before processing. A bucket prefix is organization, not authorization. Expired source files produce actionable job failure, not success. Storage provider remains unresolved between existing recommendations; both must meet these controls.

Minimize duplicated lead/address/body data. Content snapshots exist for reproducible sending, but events/logs should carry IDs and reasons. Raw provider evidence is restricted, encrypted and bounded in retention. Metrics avoid recipient addresses and unbounded tenant-specific labels. Trace/error scrubbing covers payloads, cookies, authorization headers, OAuth codes, URLs and exception locals. Security logging records denied access, privilege changes, suppression releases, connection changes and operator actions with safe correlation.

## Abuse, destructive actions and environment isolation

Platform sending restriction, mailbox restriction, rate reduction and investigation are durable explicit admin commands with actor/reason/audit. They serialize against send authorization. They do not depend on manual SQL edits or frontend feature flags. Exact abuse thresholds, escalation and customer-visible messaging require approval; no speculative risk score is treated as truth.

Deletion, ownership transfer, credential revocation and suppression release need explicit user/operator intent, current permission, consequence explanation, version check and durable audit. Preparing a migration is separate from applying it. Shared/production migration execution and destructive operations follow AGENTS.md review/approval; this document grants no such execution.

Use isolated local/staging/production database, Redis, storage, OAuth callbacks, keys and provider accounts. Staging send policy restricts destinations to controlled recipients and uses provider fakes by default. Production credentials must not be available to ordinary test jobs. Backup restore starts with sending disabled until the interval after the restore point is reconciled; old database state cannot prove external emails were unsent. Restore/secret-rotation drills and incident runbooks are prerequisites, with RTO/RPO approved separately.

## Failure behavior and testing requirements

Fail closed on missing authorization/RLS context, database inability to evaluate safety, missing rate generation, revoked credentials and unknown provider outcomes. Provider/Redis outages may delay work but cannot erase safety facts. Duplicate callbacks/events/tasks have durable dedupe identities. Concurrent role revocation, suppression creation and campaign pause are tested at transaction boundaries. Unavailable telemetry must not bypass policy or fabricate success.

Required tests: unauthenticated/expired/wrong-audience identity; membership revocation; all role/action grants once approved; cross-tenant read/update/insert/FK/file/job/event access; pool context after errors; protected function misuse; token replay/CSRF/open redirects; concurrent refresh/disconnect; credential redaction; SSRF rebinding/IPv6/metadata/TLS downgrade; MIME/HTML/XSS/header injection; malformed/oversize CSV and formula-safe export; webhook forgery/replay; unsubscribe race; limiter loss; operator privilege separation; and restore with uncertain sends. Use synthetic fixtures and isolated accounts.

## Open Decisions and definition of done

Approve RBAC/owner behavior, session transport, OAuth scopes/provider verification, key-store/runtime/storage choices, SMTP allowlist, unsubscribe protocol/token policy, retention/erasure/legal review, abuse thresholds and recovery objectives. Recommended focused follow-up documents are an RBAC repair, provider OAuth permission/verification review, data-retention/erasure policy and incident/recovery runbooks; do not create speculative security subsystems.

Security implementation is complete only when approved policies are enforced in backend and database, the tests above pass, secrets and tenant data remain scoped, recovery has been exercised, and no open policy is silently converted into permissive behavior.
