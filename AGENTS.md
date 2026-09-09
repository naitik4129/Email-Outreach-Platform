# AGENTS.md

## Purpose

This file defines how coding agents must work inside this repository.

It does not describe the product itself and should not duplicate product or architecture documentation.

The repository documentation is the source of truth for product requirements, architecture, database design, security decisions, infrastructure decisions, and implementation behavior.

Agents are expected to read and follow the relevant documentation before making changes.

---

# 1. Read Before You Change

Before starting any implementation task, first understand the repository.

Always read:

1. `AGENTS.md`
2. `docs/product/PROJECT_CONTEXT.md`
3. Documentation directly related to the task
4. Existing implementation in the affected area
5. Existing tests for the affected behavior

Do not begin implementation based only on the user request if the repository already contains specifications governing that area.

The goal is to extend the existing system, not independently redesign it.

---

# 2. Documentation Is Part of the Architecture

The repository documentation represents deliberate engineering decisions.

Depending on the task, relevant documentation may exist under:

```text
docs/product/
docs/architecture/
docs/database/
docs/security/
docs/api/
docs/providers/
docs/operations/
docs/adr/
```

Read only the documents relevant to the task, but read them before making architectural assumptions.

Do not duplicate documentation across files unnecessarily.

Each concept should have one primary source of truth.

If two documents conflict, identify the conflict rather than silently choosing one.

---

# 3. Understand Before Implementing

Before modifying code, determine:

* What behavior is being requested?
* Which existing module owns that behavior?
* What documentation defines it?
* Is there already an implementation pattern to follow?
* Does the change affect database schema?
* Does it affect security or authorization?
* Does it affect multi-tenancy?
* Does it affect background execution?
* Does it affect external services?
* Does it introduce irreversible or destructive behavior?
* What tests should prove the change is correct?

Do not produce large amounts of code before answering these questions internally.

---

# 4. Respect Existing Boundaries

Do not move responsibilities between major areas of the repository without an approved architectural reason.

Use the existing repository structure and ownership conventions.

Before creating:

* a new top-level directory
* a new service
* a new abstraction
* a new infrastructure component
* a new shared package
* a new database mechanism
* a new background-processing mechanism

first verify that an existing location or abstraction is not already intended for that responsibility.

Prefer extending established patterns over creating parallel ones.

---

# 5. Scope Discipline

Implement the requested change and the minimum supporting work required to make it correct.

Do not use a task as an opportunity to:

* rewrite unrelated modules
* rename unrelated files
* change established architecture
* upgrade unrelated dependencies
* introduce speculative abstractions
* implement future features
* clean up unrelated technical debt
* change formatting across the repository
* reorganize working code without necessity

If unrelated problems are discovered, report them separately.

Do not silently expand scope.

---

# 6. Database Changes

All database schema evolution must follow the repository's established migration process.

The migration source of truth is:

```text
supabase/migrations/
```

Do not introduce another migration system.

Do not create parallel migration ownership elsewhere in the repository.

A database change and execution of that database change are separate actions.

An agent may prepare a migration when required.

An agent must not automatically apply that migration to a shared or production environment unless explicitly instructed.

---

# 7. Migration Review Requirement

Before any migration is applied, clearly explain what it does.

The review should include, where applicable:

```text
Migration
Purpose
Objects created
Objects modified
Objects removed
Columns changed
Constraints changed
Indexes changed
RLS/security impact
Functions or triggers changed
Existing-data impact
Application impact
Compatibility considerations
Risk
Recovery or rollback approach
```

The explanation should be specific to the migration rather than generic.

Do not hide destructive operations inside large migrations.

---

# 8. Applied Migrations Are Historical Records

Once a migration has been applied to a shared environment, treat it as immutable unless the project owner explicitly decides otherwise.

Do not silently:

* rewrite it
* reorder it
* rename it
* delete it
* squash it into another migration

If the schema needs another change, create another migration.

Migration history must remain understandable and reproducible.

---

# 9. Never Perform Destructive Operations Casually

Do not execute destructive or difficult-to-reverse operations without explicit approval.

Examples include:

* resetting a database
* dropping tables
* truncating data
* removing important columns
* mass deleting records
* rewriting Git history
* force pushing
* deleting infrastructure
* changing production DNS
* revoking credentials
* disabling security controls
* applying destructive production migrations

When uncertain about impact, inspect first and explain the risk.

---

# 10. Security Is Not Optional

Every task must preserve the project's security model.

When working on data access or mutations, consider:

* authentication
* authorization
* tenant isolation
* resource ownership
* role permissions
* secret exposure
* privileged operations
* input validation

Never weaken security merely to make implementation simpler.

Never rely on UI restrictions as authorization.

Never expose sensitive server-side credentials to client-side code.

---

# 11. Protect Secrets and Sensitive Data

Never commit, print, return, or log secret values.

This includes any credential, token, key, password, or private signing material.

When debugging secret-related behavior, log only safe metadata.

Do not put real secrets into:

* source files
* examples
* tests
* documentation
* screenshots
* logs
* error messages

Use the repository's established configuration and secret-management conventions.

---

# 12. Preserve Tenant Isolation

Any change involving tenant-owned resources must explicitly consider isolation.

Never assume that an identifier alone authorizes access to a resource.

Do not create code paths that fetch global tenant data and rely on frontend filtering.

Tests involving tenant-owned resources should include unauthorized cross-tenant access cases where appropriate.

Tenant isolation failures are security bugs.

---

# 13. Keep Business Logic in the Correct Layer

Before adding logic, identify which layer should own it.

Avoid copying the same business rule into multiple execution paths.

If the same rule is needed by an API request and a background task, reuse an authoritative domain/service implementation rather than maintaining two independent copies.

Do not move business-critical decisions into presentation code merely because it is convenient.

---

# 14. Background Work Must Be Safe to Repeat

Any asynchronous job must be designed with retries and duplicate execution in mind.

Never assume:

> this task will only execute once.

Consider:

* retries
* worker crashes
* queue redelivery
* duplicate scheduling
* network ambiguity
* concurrent workers
* partial completion

Use the repository's established state, locking, transaction, and idempotency patterns.

---

# 15. External Side Effects Require Care

Operations involving external systems can have real-world consequences.

Examples include:

* sending emails
* modifying external accounts
* applying migrations
* changing billing
* modifying DNS
* deleting remote resources
* altering credentials

Do not trigger such side effects during ordinary testing unless explicitly intended.

Prefer test doubles, local environments, staging environments, or controlled test accounts.

If a task will perform a meaningful external side effect, make that clear before executing it.

---

# 16. Error Handling Must Be Intentional

Do not swallow exceptions simply to make an operation appear successful.

Avoid broad exception handling unless it has a clear purpose.

Errors should be:

* actionable
* appropriately classified
* logged with safe context
* translated at architectural boundaries when necessary

Do not leak internal stack traces, credentials, or implementation details to end users.

---

# 17. Concurrency Must Be Considered

Do not assume one process is operating on a record at a time.

For operations involving shared state, consider:

* concurrent requests
* concurrent workers
* retries
* stale reads
* transaction boundaries
* race conditions
* duplicate execution

Use database constraints and transactional guarantees where they provide stronger correctness than application assumptions.

---

# 18. Testing Is Part of Implementation

A task is not finished simply because the happy path works.

Add or update tests when behavior changes.

Choose the appropriate testing layer:

* unit
* integration
* API
* database
* worker
* frontend
* end-to-end

High-risk behavior should include unhappy-path testing.

Do not change tests merely to make incorrect behavior pass.

Tests should express intended behavior.

---

# 19. Do Not Test Against Production

Development and automated tests must never rely on production as a sandbox.

Do not point test configuration at production services.

Do not send uncontrolled real-world messages during tests.

Do not modify real customer data while validating implementation.

Use the correct local, testing, or staging environment.

---

# 20. Dependency Discipline

Before adding a dependency, determine whether it is actually necessary.

Avoid adding libraries for functionality already provided by:

* the language
* the framework
* an existing dependency
* a small amount of straightforward code

For meaningful dependencies, consider:

* maintenance status
* security
* license
* project activity
* architectural fit
* overlap with existing packages

Do not replace core technology choices during an unrelated task.

---

# 21. Avoid Premature Abstraction

Do not create abstractions for hypothetical future needs unless the current architecture explicitly requires them.

Prefer:

* clear code
* explicit responsibilities
* small cohesive modules
* straightforward interfaces

over elaborate generic systems that have no current consumer.

However, do not bypass an abstraction that already exists.

---

# 22. Avoid Premature Infrastructure Complexity

Do not introduce infrastructure simply because it may theoretically be useful at scale.

Major infrastructure changes require deliberate architectural approval.

If the existing architecture can support the requirement cleanly, extend it.

If it cannot, explain why before introducing another platform or service.

---

# 23. Maintain Compatibility

Before changing an existing interface, determine who consumes it.

This includes:

* APIs
* database schema
* events
* worker task payloads
* configuration
* provider interfaces
* frontend contracts
* webhooks

Do not casually make breaking changes.

When a breaking change is required, identify it explicitly and plan the migration.

---

# 24. Logging and Observability

Operationally important behavior should be diagnosable.

Use the repository's structured logging conventions.

Include useful identifiers where appropriate.

Do not log unnecessary sensitive content.

Logs should make it possible to understand:

* what operation occurred
* which major resource it affected
* whether it succeeded
* why it failed

without exposing secrets.

---

# 25. Documentation Changes

Update documentation when implementation materially changes documented behavior.

Do not edit documentation simply to make an unauthorized code change appear compliant.

The correct order is:

```text
approved decision
→ documentation
→ implementation
```

not:

```text
unapproved implementation
→ rewrite documentation afterward
```

---

# 26. Architecture Changes

Do not silently change architecture.

If the requested work appears to require an architecture change, explain:

```text
Current approach
Proposed change
Why the current approach is insufficient
Benefits
Trade-offs
Risks
Migration impact
Alternatives
```

Architecture changes should be deliberate.

When appropriate, record the approved decision in `docs/adr/`.

---

# 27. Existing Code Is Evidence, Not Absolute Truth

Inspect existing implementation before changing it.

However, existing code may be:

* incomplete
* transitional
* outdated
* inconsistent with current documentation

Do not blindly copy a pattern merely because it exists once.

Compare implementation with current specifications.

If they conflict, identify the discrepancy.

---

# 28. Keep Files Cohesive

Avoid giant catch-all files.

When adding code, place it with the domain or responsibility that owns it.

Do not create generic dumping grounds such as:

```text
helpers.py
utils.py
misc.py
common.py
```

unless the contents genuinely belong there.

Prefer specific naming and clear ownership.

---

# 29. Comments Should Explain Why

Do not add comments that simply restate obvious code.

Use comments for:

* non-obvious decisions
* architectural constraints
* unusual provider behavior
* concurrency reasoning
* security reasoning
* compatibility requirements

Code should explain what it is doing.

Comments should explain why the implementation is shaped that way.

---

# 30. Do Not Fabricate Completion

Never claim that something happened unless it actually happened.

Do not state that:

* tests passed
* a migration ran
* deployment succeeded
* an email was delivered
* an external resource changed

unless that action was actually performed and verified.

Be precise about the difference between:

* implemented
* prepared
* tested
* executed
* deployed
* verified

---

# 31. Completion Standard

Before considering a meaningful task complete, verify:

```text
Requirement satisfied
Relevant documentation followed
Correct repository boundary used
Security preserved
Tenant isolation preserved where applicable
Database process followed where applicable
No unauthorized external side effect occurred
Concurrency/idempotency considered where applicable
Error handling added
Relevant tests added or updated
Relevant tests executed
Types/lint/build checks executed where appropriate
No secrets introduced
No unrelated work included
Documentation updated if required
```

---

# 32. Completion Report

For meaningful implementation tasks, finish with a concise report containing:

```text
Implemented
Files changed
Database changes
Configuration/environment changes
Tests/checks performed
Important decisions
Known limitations or follow-up
```

If a migration was created but not executed, explicitly state:

```text
Migration created — not applied.
```

---

# 33. Working Principle

Do not optimize for producing the most code.

Optimize for producing the smallest correct change that fits the existing system.

Understand first.

Respect boundaries.

Preserve security.

Design for failure.

Test important behavior.

Leave the repository easier to understand than you found it.
