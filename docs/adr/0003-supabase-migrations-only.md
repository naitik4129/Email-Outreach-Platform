# Supabase migrations only

## Status

Accepted source decision — recorded from existing repository specifications on 2026-09-09. This records an established constraint, not a new implementation/deployment approval.

## Context

One ordered schema history must own tables, constraints, RLS, functions and indexes.

Source: MVP §§40.2, 45 and SYSTEM_ARCHITECTURE §§20–21, 147; current task explicitly reaffirms this choice. See [system architecture](../architecture/SYSTEM_ARCHITECTURE.md), [MVP](../product/MVP.md), and [project context](../product/PROJECT_CONTEXT.md).

## Decision

Use supabase/migrations/ exclusively. SQLAlchemy is runtime access. Applied shared-environment migrations are immutable; later schema changes use new migrations.

## Alternatives Considered

Alembic and ORM automatic schema creation would create competing histories. PROJECT_CONTEXT §§84, 139 still mention Alembic; these are explicitly superseded for this task by the newer migration rule. These alternatives explain the documented rationale; this record does not assert an undocumented historical evaluation process.

## Consequences

Migration preparation and execution remain separate reviewed actions. This ADR creates no migration and grants no permission to apply one.

