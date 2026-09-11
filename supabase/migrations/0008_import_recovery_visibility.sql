-- Import recovery visibility. Forward migration per CLAUDE.md/AGENTS.md.
--
-- Defect this fixes: every existing RLS policy on import_jobs (app_api,
-- app_worker_general) scopes SELECT to `workspace_id = (SELECT
-- app_current_workspace_id())`, i.e. exactly one workspace per transaction,
-- set from the transaction-local app.workspace_id GUC. That is correct for
-- request/task handling (each request or claimed job legitimately belongs to
-- one workspace), but Phase 3's durability story for import processing
-- (CLAUDE.md task, "Redis Failure"/"Worker Crash Recovery": Postgres must
-- remain the source of truth even if the initial Celery dispatch is lost or
-- a worker crashes mid-lease) requires a periodic sweep across ALL
-- workspaces for jobs that are PENDING past next_due_at or PROCESSING with
-- an expired lease. No existing role/policy can run that query -- it would
-- return zero rows regardless of role, since app.workspace_id can only ever
-- hold one workspace per transaction. This is a genuine schema gap, not an
-- application-layer bug: proven by inspecting 0002_contacts_content.sql,
-- which grants no `USING (true)` policy on import_jobs to any role.
--
-- Fix: grant the existing app_scheduler role (already NOLOGIN,
-- least-privilege, and already granted `USING (true)` cross-tenant SELECT on
-- platform_controls/platform_suppressions for the same class of periodic
-- discovery work) a read-only, SELECT-only policy on import_jobs. This role
-- only ever discovers due work and hands (workspace_id, import_id) pairs to
-- a Celery task; the actual claim/update still happens per-workspace, under
-- app_worker_general, with app.workspace_id properly set for that one job --
-- this migration grants no INSERT/UPDATE/DELETE on import_jobs to anyone new.
--
-- PREPARED for the project owner's already-migrated development database
-- only. Do not apply to any shared/staging/production environment without a
-- separate review and explicit authorization.

BEGIN;
SET LOCAL search_path = '';

DO $preflight$
BEGIN
    IF pg_catalog.to_regclass('public.import_jobs') IS NULL THEN
        RAISE EXCEPTION '0008 requires 0002_contacts_content.sql to already be applied';
    END IF;
    IF EXISTS (
        SELECT 1 FROM pg_catalog.pg_policies
        WHERE schemaname = 'public' AND tablename = 'import_jobs'
          AND policyname = 'import_jobs_scheduler_select'
    ) THEN
        RAISE EXCEPTION '0008 already applied: import_jobs_scheduler_select exists';
    END IF;
END;
$preflight$;

CREATE POLICY import_jobs_scheduler_select ON public.import_jobs
FOR SELECT TO app_scheduler USING (true);
GRANT SELECT ON public.import_jobs TO app_scheduler;

DO $postflight$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_catalog.pg_class AS relation
        JOIN pg_catalog.pg_namespace AS namespace ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = 'public' AND relation.relname = 'import_jobs'
          AND pg_catalog.has_table_privilege('app_scheduler', relation.oid, 'INSERT,UPDATE,DELETE')
    ) THEN
        RAISE EXCEPTION '0008 must grant app_scheduler read-only access to import_jobs';
    END IF;
END;
$postflight$;

COMMIT;
