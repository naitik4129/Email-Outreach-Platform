-- Scheduler due discovery and outbox delivery visibility.
-- Forward migration per CLAUDE.md/AGENTS.md.
--
-- Defect this fixes: every existing RLS policy on messages, campaigns,
-- mailboxes, controlled_send_authorizations, outbox_work, and outbox_deliveries
-- scopes SELECT to `workspace_id = (SELECT app_current_workspace_id())`, i.e.
-- exactly one workspace per transaction, set from the transaction-local
-- app.workspace_id GUC. That is correct for tenant-scoped request/task
-- execution, but Phase 9 due-work discovery (SCHEDULER.md, "Due discovery
-- and transactional claim", "Scheduler uses a narrowly authorized system
-- discovery role returning workspace/resource IDs") requires a periodic
-- global sweep across ALL workspaces for messages that are SCHEDULED or
-- RETRY_SCHEDULED with due_at <= now, as well as QUEUED messages with
-- claim_expires_at <= now, and outbox_deliveries with next_attempt_at <= now.
--
-- No existing role/policy can run those queries -- without app.workspace_id
-- set, app_current_workspace_id() is NULL, so workspace_id = NULL evaluates
-- to false and returns zero rows across the entire database.
--
-- Furthermore, partial indexes messages_due_idx (due_at, id),
-- messages_claim_expiry_idx (claim_expires_at, id), and
-- outbox_deliveries_unpublished_idx (next_attempt_at, id) are specifically
-- indexed on due/expiry timestamps globally, not prefixed by workspace_id.
--
-- Fix: grant the existing app_scheduler and app_outbox_relay roles (both
-- NOLOGIN, least-privilege, and already granted `USING (true)` cross-tenant
-- SELECT on import_jobs/platform_controls/platform_suppressions for this exact
-- class of periodic discovery work) read-only, SELECT-only discovery policies
-- on the required scheduling tables.
--
-- Claim mutations and outbox inserts remain strictly workspace-scoped: each
-- claiming transaction sets app.workspace_id and operates under existing
-- workspace-isolated policies. This migration also grants app_scheduler UPDATE
-- on outbox_deliveries (state, lease_owner, lease_generation, lease_expires_at,
-- next_attempt_at, attempts, safe_error, published_at) scoped by workspace_id
-- so that expired claim recovery can mark old uncompleted deliveries SUPERSEDED
-- and lease-based outbox delivery publication can be performed.
--
-- PREPARED for the project owner's already-migrated development database
-- only. Do not apply to any shared/staging/production environment without a
-- separate review and explicit authorization.

BEGIN;
SET LOCAL search_path = '';

DO $preflight$
BEGIN
    IF pg_catalog.to_regclass('public.messages') IS NULL THEN
        RAISE EXCEPTION '0011 requires 0004_messages_events_inbox.sql to already be applied';
    END IF;
    IF pg_catalog.to_regclass('public.outbox_deliveries') IS NULL THEN
        RAISE EXCEPTION '0011 requires outbox_deliveries table to exist';
    END IF;
    IF EXISTS (
        SELECT 1 FROM pg_catalog.pg_policies
        WHERE schemaname = 'public' AND tablename = 'messages'
          AND policyname = 'messages_scheduler_discovery_select'
    ) THEN
        RAISE EXCEPTION '0011 already applied: messages_scheduler_discovery_select exists';
    END IF;
END;
$preflight$;

-- 1. Messages discovery for scheduler
CREATE POLICY messages_scheduler_discovery_select ON public.messages
FOR SELECT TO app_scheduler USING (true);

-- 2. Campaigns discovery check for scheduler
CREATE POLICY campaigns_scheduler_discovery_select ON public.campaigns
FOR SELECT TO app_scheduler USING (true);

-- 3. Mailboxes discovery check for scheduler (blocked_until)
CREATE POLICY mailboxes_scheduler_discovery_select ON public.mailboxes
FOR SELECT TO app_scheduler USING (true);

-- 4. Controlled send authorizations discovery check for scheduler
CREATE POLICY controlled_send_authorizations_scheduler_discovery_select ON public.controlled_send_authorizations
FOR SELECT TO app_scheduler USING (true);

-- 5. Outbox work discovery for scheduler and relay
CREATE POLICY outbox_work_scheduler_discovery_select ON public.outbox_work
FOR SELECT TO app_scheduler USING (true);

CREATE POLICY outbox_work_relay_discovery_select ON public.outbox_work
FOR SELECT TO app_outbox_relay USING (true);

-- 6. Outbox deliveries discovery and update for scheduler and relay
GRANT SELECT ON public.outbox_deliveries TO app_scheduler;
GRANT UPDATE (state, lease_owner, lease_generation, lease_expires_at, next_attempt_at, attempts, safe_error, published_at)
    ON public.outbox_deliveries TO app_scheduler;

CREATE POLICY outbox_deliveries_scheduler_discovery_select ON public.outbox_deliveries
FOR SELECT TO app_scheduler USING (true);

CREATE POLICY outbox_deliveries_scheduler_update ON public.outbox_deliveries
FOR UPDATE TO app_scheduler
USING (workspace_id = (SELECT public.app_current_workspace_id()))
WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));

CREATE POLICY outbox_deliveries_relay_discovery_select ON public.outbox_deliveries
FOR SELECT TO app_outbox_relay USING (true);

DO $postflight$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_policies
        WHERE schemaname = 'public' AND tablename = 'messages'
          AND policyname = 'messages_scheduler_discovery_select'
    ) THEN
        RAISE EXCEPTION '0011 postcondition failed: messages_scheduler_discovery_select missing';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_policies
        WHERE schemaname = 'public' AND tablename = 'outbox_deliveries'
          AND policyname = 'outbox_deliveries_scheduler_discovery_select'
    ) THEN
        RAISE EXCEPTION '0011 postcondition failed: outbox_deliveries_scheduler_discovery_select missing';
    END IF;
END;
$postflight$;

COMMIT;
