# CLAUDE.md

## Purpose

This file defines how Claude Code should operate in this repository.

It intentionally does not repeat product requirements or system architecture.

Those are maintained in the repository documentation.

The repository-wide engineering rules are defined in:

```text
AGENTS.md
```

Treat `AGENTS.md` as authoritative.

---

# 1. Start Every Task by Building Context

Before changing code:

1. Read `AGENTS.md`.
2. Read `docs/product/PROJECT_CONTEXT.md` if broader product context is needed.
3. Read the documents directly relevant to the requested task.
4. Inspect the existing implementation.
5. Inspect relevant tests.
6. Identify the smallest set of files that should change.

Do not begin implementation from the request alone when specifications already exist.

---

# 2. Read Selectively, Not Blindly

Do not load every document in the repository for every task.

Determine which areas are relevant.

Examples:

A database task may require:

```text
docs/database/
docs/security/
supabase/migrations/
```

A worker task may require:

```text
docs/architecture/
workers/
relevant backend/domain code
```

A frontend task may require:

```text
docs/product/
docs/api/
frontend/
```

A provider integration may require:

```text
docs/providers/
docs/architecture/
relevant backend provider code
```

Use the repository as structured context.

Do not duplicate that context into new files unnecessarily.

---

# 3. Plan Before Editing

For a meaningful task, establish internally:

```text
Requested behavior
Existing owner/module
Relevant specification
Files likely affected
Database impact
Security impact
Async/concurrency impact
External-service impact
Tests required
```

Then implement.

Do not generate a large implementation first and attempt to fit it into the architecture afterward.

---

# 4. Follow Existing Decisions

Do not independently replace established project decisions.

If a requested task can be implemented within the current architecture, do so.

If it cannot, identify the reason and explain the architectural conflict.

Do not silently introduce a new pattern merely because it is more familiar.

---

# 5. Keep Changes Focused

Modify only the files necessary for the requested behavior and closely related correctness work.

Avoid unrelated:

* refactoring
* renaming
* formatting
* dependency upgrades
* architecture changes
* cleanup
* feature additions

If another issue is discovered, report it separately.

---

# 6. Database Work

Database migrations live only in:

```text
supabase/migrations/
```

Do not introduce another migration system.

When a schema change is required:

1. Inspect current migrations and schema assumptions.
2. Determine the minimal schema change.
3. Create a new migration.
4. Review security/RLS impact.
5. Review existing-data impact.
6. Review indexes and constraints.
7. Do not execute the migration unless explicitly instructed.

Creating SQL is not permission to apply SQL.

---

# 7. Before Executing a Migration

Provide a concise migration review:

```text
Migration
Purpose
Creates
Modifies
Deletes
Constraints
Indexes
RLS/security changes
Functions/triggers
Existing-data impact
Application impact
Risk
Recovery/rollback
```

Do not run the migration until approval is clear.

---

# 8. Protect Existing Migration History

Do not modify migrations that have already been applied to a shared environment.

Use a new migration for subsequent changes.

If it is unclear whether a migration has been applied, do not assume it is safe to rewrite.

Ask through the task report or inspect available project state before proceeding.

---

# 9. Treat Security as a Functional Requirement

When changing application behavior, explicitly consider:

* authentication
* authorization
* tenant isolation
* privileged operations
* input validation
* credentials
* sensitive output

Do not rely on client behavior for security.

Do not expose server secrets.

Do not weaken checks simply to make a flow easier to implement.

---

# 10. Understand Data Ownership

For data-related work, identify:

```text
Who owns this record?
Which tenant/workspace does it belong to?
Who may read it?
Who may modify it?
What happens if another tenant knows its ID?
```

Cross-tenant access must fail.

When appropriate, add tests proving that failure.

---

# 11. Reuse Authoritative Logic

Before writing a rule, search for an existing implementation.

Do not maintain duplicate versions of the same business decision in multiple locations.

Reuse the domain/service layer intended to own the behavior.

If an API route and background worker require the same rule, they should not each invent their own version.

---

# 12. Assume Distributed Work Can Repeat

For background and externally triggered behavior, assume:

```text
tasks may retry
events may duplicate
workers may crash
requests may repeat
webhooks may arrive twice
processes may race
networks may time out
```

Do not build correctness around "this should only happen once."

Follow the project's idempotency, transaction, and locking patterns.

---

# 13. Be Conservative With External Actions

Do not accidentally perform real external actions while implementing or testing.

Be especially careful with:

* sending emails
* modifying external accounts
* applying migrations
* billing changes
* deleting resources
* changing infrastructure
* changing credentials

Use controlled environments and test mechanisms.

Clearly distinguish between code that was created and actions that were actually executed.

---

# 14. Do Not Hide Failures

Never silence an error merely because it blocks completion.

Investigate the failure.

If it is caused by the implementation, fix it.

If it is environmental or unrelated, report it clearly.

Do not:

* remove tests because they fail
* disable type checking
* ignore lint rules without reason
* catch every exception and return success
* fake successful external responses

Correctness is more important than presenting a clean-looking result.

---

# 15. Validate the Change

After implementation, run the most relevant checks available.

Depending on the task, these may include:

```text
unit tests
integration tests
API tests
worker tests
database tests
frontend tests
type checking
linting
build
```

Do not claim checks passed unless they actually ran successfully.

If something could not be run, state that.

---

# 16. Review Your Own Diff

Before finishing:

* inspect changed files
* verify no unintended files changed
* remove temporary debugging
* remove dead code
* remove accidental secrets
* ensure naming matches the project
* ensure documentation still matches behavior
* verify migration execution did not occur unintentionally

Do not rely solely on tests to catch scope mistakes.

---

# 17. Update Documentation When Necessary

If implementation changes documented behavior, update the relevant primary document.

Do not copy the same explanation into several files.

Prefer one authoritative document and references to it.

If architecture would need to change, do not modify documentation and implementation silently. Surface the decision first.

---

# 18. Communicate Clearly at Completion

For meaningful tasks, summarize:

```text
Implemented
Files changed
Database changes
Configuration changes
Tests/checks run
Important decisions
Known limitations
Follow-up
```

Be explicit about database state.

For example:

```text
Created supabase/migrations/0004_example.sql.
Migration was not applied.
```

Do not make the user infer whether an external operation occurred.

---

# 19. When You Are Unsure

Prefer inspection over assumption.

Search:

* repository documentation
* existing implementations
* migrations
* tests
* configuration
* interfaces

before inventing behavior.

If a decision is genuinely absent and materially affects architecture, surface the choice rather than embedding an arbitrary decision into code.

---

# 20. Core Working Rule

Read first.

Understand ownership.

Implement narrowly.

Preserve architecture.

Protect data.

Test the important paths.

Never perform irreversible operations implicitly.

Report exactly what changed.
