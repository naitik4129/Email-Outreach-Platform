-- Phase 11 failure, retry, and recovery hardening: grant least-privilege
-- permissions to app_scheduler for background stale execution recovery and
-- message outcome reconciliation.
-- Forward migration per AGENTS.md.
--
-- Objects modified:
-- - GRANT UPDATE (evidence_state, completed_at, provider_request_id, provider_message_ref, provider_thread_ref, error_category, error_code, reconciliation_metadata)
--   ON public.message_attempts TO app_scheduler;
-- - GRANT UPDATE (version) ON public.messages TO app_scheduler;
-- - CREATE POLICY message_attempts_scheduler_discovery_select ON public.message_attempts FOR SELECT TO app_scheduler USING (true);
-- - CREATE POLICY message_attempts_scheduler_update ON public.message_attempts FOR UPDATE TO app_scheduler
--     USING (workspace_id = (SELECT public.app_current_workspace_id()))
--     WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
--
-- This migration is additive-only: grants and RLS policies. It does not alter any
-- table structure, constraints, indexes, triggers, or existing data.
--
-- PREPARED for review per AGENTS.md §§6-8.
-- Do not apply to any shared/staging/production environment without explicit authorization.

BEGIN;
SET LOCAL search_path = '';

DO $preflight$
BEGIN
    IF pg_catalog.to_regclass('public.message_attempts') IS NULL THEN
        RAISE EXCEPTION '0013 requires 0004_messages_events_inbox.sql to already be applied';
    END IF;
    IF EXISTS (
        SELECT 1 FROM pg_catalog.pg_policies
        WHERE schemaname = 'public' AND tablename = 'message_attempts'
          AND policyname = 'message_attempts_scheduler_discovery_select'
    ) THEN
        RAISE EXCEPTION '0013 already applied: message_attempts_scheduler_discovery_select exists';
    END IF;
END;
$preflight$;

-- 1. Grant column updates to app_scheduler on message_attempts
GRANT UPDATE (
    evidence_state,
    completed_at,
    provider_request_id,
    provider_message_ref,
    provider_thread_ref,
    error_category,
    error_code,
    reconciliation_metadata
) ON public.message_attempts TO app_scheduler;

-- 2. Grant version update to app_scheduler on messages
GRANT UPDATE (version) ON public.messages TO app_scheduler;

-- 3. Global discovery policy for scheduler recovery sweep
CREATE POLICY message_attempts_scheduler_discovery_select ON public.message_attempts
FOR SELECT TO app_scheduler USING (true);

-- 4. Tenant-scoped update policy for scheduler recovery
CREATE POLICY message_attempts_scheduler_update ON public.message_attempts
FOR UPDATE TO app_scheduler
USING (workspace_id = (SELECT public.app_current_workspace_id()))
WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));

DO $postflight$
BEGIN
    IF NOT pg_catalog.has_column_privilege('app_scheduler', 'public.message_attempts', 'evidence_state', 'UPDATE') THEN
        RAISE EXCEPTION '0013 postcondition failed: app_scheduler still lacks UPDATE on message_attempts.evidence_state';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_policies
        WHERE schemaname = 'public' AND tablename = 'message_attempts'
          AND policyname = 'message_attempts_scheduler_discovery_select'
    ) THEN
        RAISE EXCEPTION '0013 postcondition failed: message_attempts_scheduler_discovery_select missing';
    END IF;
END;
$postflight$;

COMMIT;
