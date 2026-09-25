-- Send worker: close three confirmed privilege gaps for app_worker_send.
-- Forward migration per AGENTS.md.
--
-- Defect this fixes: SendingService.execute (backend/app/modules/sending/
-- repository.py, load_message_for_send) runs as app_worker_send and, per
-- docs/architecture/CAMPAIGN_ENGINE.md "Transaction and lock boundaries" and
-- MESSAGE_STATE_MACHINE.md step 3, locks the campaign, the enrollment and the
-- mailbox rows (SELECT ... FOR UPDATE) before authorizing an attempt. That lock
-- is the send-authorization linearization point: a pause or unsubscribe that
-- committed first must win. PostgreSQL requires UPDATE privilege (and, with RLS,
-- an UPDATE policy USING expression) on every table named in a row-locking
-- clause, even when nothing is modified. app_worker_send holds only SELECT on
-- public.campaigns and public.campaign_enrollments (0003), so every campaign
-- send fails with `permission denied for table campaigns` before any provider
-- call is made. The unit-test suite runs on SQLite, which has no row locks, so
-- it could not detect this. Separately, the pre-send reply gate
-- (sending/gates.py check_reply_outcome) reads public.recipient_outcomes, and
-- app_worker_send has no privilege on that table at all.
--
-- Objects created:
-- - POLICY campaigns_worker_send_lock ON public.campaigns
--     FOR UPDATE TO app_worker_send
--     USING (workspace_id = (SELECT public.app_current_workspace_id()))
--     WITH CHECK (false);
-- - GRANT UPDATE (error_reason) ON public.campaigns TO app_worker_send;
-- - POLICY campaign_enrollments_worker_send_lock ON public.campaign_enrollments
--     FOR UPDATE TO app_worker_send
--     USING (workspace_id = (SELECT public.app_current_workspace_id()))
--     WITH CHECK (false);
-- - GRANT UPDATE (hold_reason) ON public.campaign_enrollments TO app_worker_send;
-- - POLICY recipient_outcomes_worker_send_select ON public.recipient_outcomes
--     FOR SELECT TO app_worker_send
--     USING (workspace_id = (SELECT public.app_current_workspace_id()));
-- - GRANT SELECT ON public.recipient_outcomes TO app_worker_send;
--
-- Why this is still least privilege: the column-level UPDATE grants exist only
-- to satisfy PostgreSQL's row-lock requirement. The matching UPDATE policies
-- have WITH CHECK (false), so any actual UPDATE by app_worker_send on these two
-- tables is rejected by row-level security for every row; the role can lock
-- rows in its own workspace and cannot change them. Campaign and enrollment
-- state remain writable only by the roles that already own those transitions
-- (app_api, app_worker_general, app_scheduler, app_integrity_guard).
-- recipient_outcomes is read-only for the send worker and tenant-scoped like
-- every other worker read policy.
--
-- This migration is additive-only. It does not alter any table structure,
-- constraint, index, trigger or existing data, and it does not change any other
-- role's privileges. mailboxes and messages already grant what the send path
-- needs and are unchanged.
--
-- Recovery / rollback (run manually if ever required):
--   DROP POLICY campaigns_worker_send_lock ON public.campaigns;
--   REVOKE UPDATE (error_reason) ON public.campaigns FROM app_worker_send;
--   DROP POLICY campaign_enrollments_worker_send_lock ON public.campaign_enrollments;
--   REVOKE UPDATE (hold_reason) ON public.campaign_enrollments FROM app_worker_send;
--   DROP POLICY recipient_outcomes_worker_send_select ON public.recipient_outcomes;
--   REVOKE SELECT ON public.recipient_outcomes FROM app_worker_send;
-- Rolling back restores the failure above (campaign sends stop before any
-- provider call); it does not affect stored data.
--
-- PREPARED for review per AGENTS.md §§6-8.
-- Do not apply to any shared/staging/production environment without explicit authorization.

BEGIN;
SET LOCAL search_path = '';

DO $preflight$
BEGIN
    IF pg_catalog.to_regclass('public.campaigns') IS NULL
       OR pg_catalog.to_regclass('public.campaign_enrollments') IS NULL THEN
        RAISE EXCEPTION '0020 requires 0003_mailboxes_campaigns.sql to already be applied';
    END IF;
    IF pg_catalog.to_regclass('public.recipient_outcomes') IS NULL THEN
        RAISE EXCEPTION '0020 requires 0004_messages_events_inbox.sql to already be applied';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'app_worker_send') THEN
        RAISE EXCEPTION '0020 requires role app_worker_send from 0001_initial.sql';
    END IF;
    IF EXISTS (
        SELECT 1 FROM pg_catalog.pg_policies
        WHERE schemaname = 'public'
          AND policyname IN (
              'campaigns_worker_send_lock',
              'campaign_enrollments_worker_send_lock',
              'recipient_outcomes_worker_send_select'
          )
    ) THEN
        RAISE EXCEPTION '0020 already applied: one of its policies exists';
    END IF;
    IF pg_catalog.has_any_column_privilege('app_worker_send', 'public.campaigns', 'UPDATE')
       OR pg_catalog.has_any_column_privilege('app_worker_send', 'public.campaign_enrollments', 'UPDATE')
       OR pg_catalog.has_any_column_privilege('app_worker_send', 'public.recipient_outcomes', 'SELECT') THEN
        RAISE EXCEPTION '0020 preflight failed: app_worker_send already has one of the privileges this migration grants';
    END IF;
END;
$preflight$;

-- 1. Lock-only access to campaigns and campaign_enrollments (see header).
CREATE POLICY campaigns_worker_send_lock ON public.campaigns
FOR UPDATE TO app_worker_send
USING (workspace_id = (SELECT public.app_current_workspace_id()))
WITH CHECK (false);
GRANT UPDATE (error_reason) ON public.campaigns TO app_worker_send;

CREATE POLICY campaign_enrollments_worker_send_lock ON public.campaign_enrollments
FOR UPDATE TO app_worker_send
USING (workspace_id = (SELECT public.app_current_workspace_id()))
WITH CHECK (false);
GRANT UPDATE (hold_reason) ON public.campaign_enrollments TO app_worker_send;

-- 2. Read-only, tenant-scoped access for the pre-send reply gate.
CREATE POLICY recipient_outcomes_worker_send_select ON public.recipient_outcomes
FOR SELECT TO app_worker_send USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.recipient_outcomes TO app_worker_send;

DO $postflight$
BEGIN
    IF NOT pg_catalog.has_column_privilege('app_worker_send', 'public.campaigns', 'error_reason', 'UPDATE')
       OR NOT pg_catalog.has_column_privilege('app_worker_send', 'public.campaign_enrollments', 'hold_reason', 'UPDATE') THEN
        RAISE EXCEPTION '0020 postcondition failed: app_worker_send still cannot lock campaigns/campaign_enrollments';
    END IF;
    IF NOT pg_catalog.has_table_privilege('app_worker_send', 'public.recipient_outcomes', 'SELECT') THEN
        RAISE EXCEPTION '0020 postcondition failed: app_worker_send still cannot read recipient_outcomes';
    END IF;
    -- Lock-only: no other column may become updatable, and no INSERT/DELETE.
    IF pg_catalog.has_column_privilege('app_worker_send', 'public.campaigns', 'status', 'UPDATE')
       OR pg_catalog.has_column_privilege('app_worker_send', 'public.campaign_enrollments', 'state', 'UPDATE')
       OR pg_catalog.has_table_privilege('app_worker_send', 'public.campaigns', 'INSERT')
       OR pg_catalog.has_table_privilege('app_worker_send', 'public.campaigns', 'DELETE')
       OR pg_catalog.has_table_privilege('app_worker_send', 'public.campaign_enrollments', 'INSERT')
       OR pg_catalog.has_table_privilege('app_worker_send', 'public.campaign_enrollments', 'DELETE')
       OR pg_catalog.has_any_column_privilege('app_worker_send', 'public.recipient_outcomes', 'INSERT, UPDATE') THEN
        RAISE EXCEPTION '0020 postcondition failed: app_worker_send gained more than lock/read access';
    END IF;
    IF (SELECT pg_catalog.count(*) FROM pg_catalog.pg_policies
        WHERE schemaname = 'public'
          AND policyname IN (
              'campaigns_worker_send_lock',
              'campaign_enrollments_worker_send_lock',
              'recipient_outcomes_worker_send_select'
          )) <> 3 THEN
        RAISE EXCEPTION '0020 postcondition failed: expected policies are missing';
    END IF;
END;
$postflight$;

COMMIT;
