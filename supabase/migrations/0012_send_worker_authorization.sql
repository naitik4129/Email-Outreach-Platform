-- Phase 10 sending worker: close a confirmed grant gap for app_worker_send.
-- Forward migration per CLAUDE.md/AGENTS.md.
--
-- Defect this fixes: messages_worker_send_update (0004_messages_events_inbox.sql,
-- line 840) grants app_worker_send UPDATE on
-- (status, terminal_reason, dispatch_origin, dispatch_generation, claim_expires_at,
--  retry_count, next_retry_at, due_at, accepted_at, provider_message_id, hold_reason)
-- but omits `version`. Every other mutator of a versioned row in this codebase
-- (SchedulerRepository.claim_due_message, .recover_expired_claim) bumps
-- `version` on every transition for optimistic-concurrency detection and
-- audit history. The Phase 10 sending worker's authorization transaction
-- (backend/app/modules/sending/repository.py: transition_message_to_sending,
-- finalize_attempt_result, skip_message) needs the same `WHERE version =
-- :expected_version` / `SET version = version + 1` pattern to detect a
-- concurrent mutation racing its own FOR UPDATE-held transaction, and cannot
-- do so without this grant.
--
-- This migration is additive-only: one column added to an existing GRANT
-- UPDATE list. It does not alter any table, constraint, index, trigger, or
-- RLS policy definition, and does not touch existing data.
--
-- PREPARED for the project owner's already-migrated development database
-- only. Requires 0005_operations_platform.sql to already be applied (the
-- Phase 10 sending worker also depends on rate_control/rate_scopes/
-- tenant_rate_policies/capacity_debits/capacity_debit_scopes existing, even
-- though this migration itself makes no changes to those tables). Do not
-- apply to any shared/staging/production environment without a separate
-- review and explicit authorization.

BEGIN;
SET LOCAL search_path = '';

DO $preflight$
BEGIN
    IF pg_catalog.to_regclass('public.messages') IS NULL THEN
        RAISE EXCEPTION '0012 requires 0004_messages_events_inbox.sql to already be applied';
    END IF;
    IF pg_catalog.to_regclass('public.rate_control') IS NULL THEN
        RAISE EXCEPTION '0012 requires 0005_operations_platform.sql to already be applied';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM pg_catalog.pg_attribute a
        JOIN pg_catalog.pg_class c ON c.oid = a.attrelid
        JOIN pg_catalog.pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public' AND c.relname = 'messages' AND a.attname = 'version'
          AND pg_catalog.has_column_privilege('app_worker_send', 'public.messages', 'version', 'UPDATE')
    ) THEN
        RAISE EXCEPTION '0012 already applied: app_worker_send already has UPDATE on messages.version';
    END IF;
END;
$preflight$;

GRANT UPDATE (version) ON public.messages TO app_worker_send;

DO $postflight$
BEGIN
    IF NOT pg_catalog.has_column_privilege('app_worker_send', 'public.messages', 'version', 'UPDATE') THEN
        RAISE EXCEPTION '0012 postcondition failed: app_worker_send still lacks UPDATE on messages.version';
    END IF;
END;
$postflight$;

COMMIT;
