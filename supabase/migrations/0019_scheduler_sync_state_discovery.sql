-- Reply-sync due discovery and stale-lease recovery for the scheduler.
-- Forward migration per AGENTS.md.
--
-- Defect this fixes: the scheduler's periodic sweeps that claim due mailbox
-- syncs and recover expired sync leases must look at mailbox_sync_states across
-- ALL workspaces. Every existing policy on that table is scoped to
-- `workspace_id = (SELECT app_current_workspace_id())` (one workspace per
-- transaction), and app_scheduler has no access to the table at all. Run under
-- app_worker_sync with no workspace set, both sweeps silently saw zero rows, so
-- scheduled reply sync could never be dispatched or repaired. Migrations 0011
-- and 0013 solved the same problem for messages, mailboxes and message_attempts
-- with narrow read-only discovery policies for app_scheduler; this applies the
-- same pattern to mailbox_sync_states.
--
-- Objects modified:
-- - GRANT SELECT (id, workspace_id, mailbox_id, connection_generation, sync_scope,
--     lease_owner, lease_generation, lease_expires_at, next_due_at, status,
--     failure_count, version) ON public.mailbox_sync_states TO app_scheduler;
--   cursor_data, last_complete_at, subscription_expires_at, created_at and
--   updated_at are deliberately NOT granted: discovery only needs identity and
--   lease/due state.
-- - CREATE POLICY mailbox_sync_states_scheduler_discovery_select
--     ON public.mailbox_sync_states FOR SELECT TO app_scheduler USING (true);
--
-- app_scheduler receives no INSERT/UPDATE/DELETE on mailbox_sync_states. The
-- application claims and recovers each row afterwards as app_worker_sync, inside
-- that row's workspace, under the existing tenant-scoped policies and grants.
--
-- This migration is additive-only: one column-limited grant and one RLS policy.
-- It does not alter any table structure, constraint, index, trigger, or existing
-- data, and it does not change any other role's privileges.
--
-- Recovery / rollback (run manually if ever required):
--   DROP POLICY mailbox_sync_states_scheduler_discovery_select ON public.mailbox_sync_states;
--   REVOKE SELECT ON public.mailbox_sync_states FROM app_scheduler;
--
-- PREPARED for review per AGENTS.md §§6-8.
-- Do not apply to any shared/staging/production environment without explicit authorization.

BEGIN;
SET LOCAL search_path = '';

DO $preflight$
BEGIN
    IF pg_catalog.to_regclass('public.mailbox_sync_states') IS NULL THEN
        RAISE EXCEPTION '0019 requires 0003_mailboxes_campaigns.sql to already be applied';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'app_scheduler') THEN
        RAISE EXCEPTION '0019 requires role app_scheduler from 0001_initial.sql';
    END IF;
    IF EXISTS (
        SELECT 1 FROM pg_catalog.pg_policies
        WHERE schemaname = 'public' AND tablename = 'mailbox_sync_states'
          AND policyname = 'mailbox_sync_states_scheduler_discovery_select'
    ) THEN
        RAISE EXCEPTION '0019 already applied: mailbox_sync_states_scheduler_discovery_select exists';
    END IF;
    IF pg_catalog.has_table_privilege('app_scheduler', 'public.mailbox_sync_states', 'SELECT')
       OR pg_catalog.has_column_privilege('app_scheduler', 'public.mailbox_sync_states', 'id', 'SELECT') THEN
        RAISE EXCEPTION '0019 preflight failed: app_scheduler already has SELECT on mailbox_sync_states';
    END IF;
END;
$preflight$;

-- 1. Column-limited SELECT: identity and lease/due state only, no cursor data.
GRANT SELECT (
    id,
    workspace_id,
    mailbox_id,
    connection_generation,
    sync_scope,
    lease_owner,
    lease_generation,
    lease_expires_at,
    next_due_at,
    status,
    failure_count,
    version
) ON public.mailbox_sync_states TO app_scheduler;

-- 2. Global, read-only discovery policy for the scheduler sweeps.
CREATE POLICY mailbox_sync_states_scheduler_discovery_select ON public.mailbox_sync_states
FOR SELECT TO app_scheduler USING (true);

DO $postflight$
BEGIN
    IF NOT pg_catalog.has_column_privilege('app_scheduler', 'public.mailbox_sync_states', 'next_due_at', 'SELECT') THEN
        RAISE EXCEPTION '0019 postcondition failed: app_scheduler still lacks SELECT on mailbox_sync_states.next_due_at';
    END IF;
    IF pg_catalog.has_column_privilege('app_scheduler', 'public.mailbox_sync_states', 'cursor_data', 'SELECT') THEN
        RAISE EXCEPTION '0019 postcondition failed: app_scheduler must not be able to read mailbox_sync_states.cursor_data';
    END IF;
    IF pg_catalog.has_any_column_privilege('app_scheduler', 'public.mailbox_sync_states', 'INSERT, UPDATE') THEN
        RAISE EXCEPTION '0019 postcondition failed: app_scheduler must not be able to write mailbox_sync_states';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_policies
        WHERE schemaname = 'public' AND tablename = 'mailbox_sync_states'
          AND policyname = 'mailbox_sync_states_scheduler_discovery_select'
    ) THEN
        RAISE EXCEPTION '0019 postcondition failed: mailbox_sync_states_scheduler_discovery_select missing';
    END IF;
END;
$postflight$;

COMMIT;
