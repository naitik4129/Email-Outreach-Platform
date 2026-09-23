-- Phase 12 inbound event processing pipeline: grant least-privilege
-- permissions to app_api for webhook receipt ingestion and public unsubscribe
-- processing, and grant app_worker_general version updates on messages and
-- campaign_enrollments for cancellation / stopping side effects.
-- Forward migration per AGENTS.md §§6-8.
--
-- Objects modified:
-- - GRANT INSERT, SELECT ON public.provider_receipts TO app_api;
-- - GRANT INSERT, SELECT ON public.safety_holds TO app_api;
-- - GRANT SELECT, UPDATE (revoked_at, version) ON public.unsubscribe_tokens TO app_api;
-- - GRANT UPDATE (version) ON public.messages TO app_worker_general;
-- - GRANT UPDATE (version) ON public.campaign_enrollments TO app_worker_general;
-- - GRANT SELECT ON public.unsubscribe_tokens TO app_worker_general;
-- - CREATE POLICY provider_receipts_api_insert ON public.provider_receipts FOR INSERT TO app_api;
-- - CREATE POLICY provider_receipts_api_select ON public.provider_receipts FOR SELECT TO app_api;
-- - CREATE POLICY safety_holds_api_insert ON public.safety_holds FOR INSERT TO app_api;
-- - CREATE POLICY safety_holds_api_select ON public.safety_holds FOR SELECT TO app_api;
-- - CREATE POLICY unsubscribe_tokens_api_select ON public.unsubscribe_tokens FOR SELECT TO app_api;
-- - CREATE POLICY unsubscribe_tokens_api_update ON public.unsubscribe_tokens FOR UPDATE TO app_api;
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
    IF pg_catalog.to_regclass('public.provider_receipts') IS NULL THEN
        RAISE EXCEPTION '0014 requires 0004_messages_events_inbox.sql to already be applied';
    END IF;
    IF pg_catalog.to_regclass('public.safety_holds') IS NULL THEN
        RAISE EXCEPTION '0014 requires 0004_messages_events_inbox.sql to already be applied';
    END IF;
    IF pg_catalog.to_regclass('public.unsubscribe_tokens') IS NULL THEN
        RAISE EXCEPTION '0014 requires 0002_contacts_content.sql to already be applied';
    END IF;
    IF EXISTS (
        SELECT 1 FROM pg_catalog.pg_policies
        WHERE schemaname = 'public' AND tablename = 'provider_receipts'
          AND policyname = 'provider_receipts_api_insert'
    ) THEN
        RAISE EXCEPTION '0014 already applied: provider_receipts_api_insert exists';
    END IF;
END;
$preflight$;

-- 1. Grant provider_receipts permissions to app_api for webhook ingestion
GRANT INSERT (
    lease_expires_at,
    payload_digest,
    id,
    workspace_id,
    mailbox_id,
    provider,
    scope_kind,
    event_identity,
    verified_at,
    received_at,
    source_schema,
    receipt_status,
    lease_owner,
    lease_generation,
    retry_count,
    next_due_at,
    safe_error,
    payload_ref
) ON public.provider_receipts TO app_api;

GRANT SELECT ON public.provider_receipts TO app_api;

CREATE POLICY provider_receipts_api_insert ON public.provider_receipts
FOR INSERT TO app_api
WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));

CREATE POLICY provider_receipts_api_select ON public.provider_receipts
FOR SELECT TO app_api
USING (workspace_id = (SELECT public.app_current_workspace_id()));

-- 2. Grant safety_holds permissions to app_api for webhook pending-safety gates
GRANT INSERT (
    id,
    workspace_id,
    source_receipt_id,
    source_work_identity,
    target_kind,
    target_mailbox_id,
    status,
    reason,
    resolved_at
) ON public.safety_holds TO app_api;

GRANT SELECT ON public.safety_holds TO app_api;

CREATE POLICY safety_holds_api_insert ON public.safety_holds
FOR INSERT TO app_api
WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));

CREATE POLICY safety_holds_api_select ON public.safety_holds
FOR SELECT TO app_api
USING (workspace_id = (SELECT public.app_current_workspace_id()));

-- 3. Grant unsubscribe_tokens permissions to app_api for public unsubscribe
GRANT SELECT ON public.unsubscribe_tokens TO app_api;
GRANT UPDATE (revoked_at, version) ON public.unsubscribe_tokens TO app_api;

-- Token digest lookup is public and global (high-entropy SHA-256 digest resolves workspace)
CREATE POLICY unsubscribe_tokens_api_select ON public.unsubscribe_tokens
FOR SELECT TO app_api
USING (true);

CREATE POLICY unsubscribe_tokens_api_update ON public.unsubscribe_tokens
FOR UPDATE TO app_api
USING (workspace_id = (SELECT public.app_current_workspace_id()))
WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));

-- 4. Grant app_worker_general version updates for optimistic concurrency
GRANT UPDATE (version) ON public.messages TO app_worker_general;
GRANT UPDATE (version) ON public.campaign_enrollments TO app_worker_general;
GRANT SELECT ON public.unsubscribe_tokens TO app_worker_general;

DO $postflight$
BEGIN
    IF NOT pg_catalog.has_table_privilege('app_api', 'public.provider_receipts', 'INSERT') THEN
        RAISE EXCEPTION '0014 postcondition failed: app_api lacks INSERT on provider_receipts';
    END IF;
    IF NOT pg_catalog.has_column_privilege('app_worker_general', 'public.messages', 'version', 'UPDATE') THEN
        RAISE EXCEPTION '0014 postcondition failed: app_worker_general lacks UPDATE on messages.version';
    END IF;
    IF NOT pg_catalog.has_column_privilege('app_worker_general', 'public.campaign_enrollments', 'version', 'UPDATE') THEN
        RAISE EXCEPTION '0014 postcondition failed: app_worker_general lacks UPDATE on campaign_enrollments.version';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_policies
        WHERE schemaname = 'public' AND tablename = 'provider_receipts'
          AND policyname = 'provider_receipts_api_insert'
    ) THEN
        RAISE EXCEPTION '0014 postcondition failed: provider_receipts_api_insert missing';
    END IF;
END;
$postflight$;

COMMIT;
