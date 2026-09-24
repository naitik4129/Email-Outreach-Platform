-- Phase 13 reply synchronization and campaign safety pipeline: grant least-privilege
-- permissions to app_worker_sync for inbound correlation, recipient outcomes,
-- enrollment stopping, message cancellation, and lease management.
-- Forward migration per AGENTS.md §§6-8.
--
-- Objects modified:
-- - GRANT SELECT, UPDATE (state, stop_reason, next_step_id, version) ON public.campaign_enrollments TO app_worker_sync;
-- - GRANT SELECT, INSERT ON public.recipient_outcomes TO app_worker_sync;
-- - GRANT SELECT, INSERT, UPDATE (status, matched_at, version) ON public.inbound_outreach_links TO app_worker_sync;
-- - GRANT UPDATE (association_status, version) ON public.inbound_messages TO app_worker_sync;
-- - GRANT UPDATE (status, resolved_at, version) ON public.safety_holds TO app_worker_sync;
-- - GRANT UPDATE (version, connection_generation) ON public.mailbox_sync_states TO app_worker_sync;
-- - CREATE POLICY campaign_enrollments_sync_select ON public.campaign_enrollments FOR SELECT TO app_worker_sync;
-- - CREATE POLICY campaign_enrollments_sync_update ON public.campaign_enrollments FOR UPDATE TO app_worker_sync;
-- - CREATE POLICY recipient_outcomes_sync_select ON public.recipient_outcomes FOR SELECT TO app_worker_sync;
-- - CREATE POLICY recipient_outcomes_sync_insert ON public.recipient_outcomes FOR INSERT TO app_worker_sync;
-- - CREATE POLICY inbound_outreach_links_sync_select ON public.inbound_outreach_links FOR SELECT TO app_worker_sync;
-- - CREATE POLICY inbound_outreach_links_sync_insert ON public.inbound_outreach_links FOR INSERT TO app_worker_sync;
-- - CREATE POLICY inbound_outreach_links_sync_update ON public.inbound_outreach_links FOR UPDATE TO app_worker_sync;
-- - CREATE POLICY inbound_messages_sync_update ON public.inbound_messages FOR UPDATE TO app_worker_sync;
-- - CREATE POLICY safety_holds_sync_update ON public.safety_holds FOR UPDATE TO app_worker_sync;
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
    IF pg_catalog.to_regclass('public.inbound_outreach_links') IS NULL THEN
        RAISE EXCEPTION '0015 requires 0004_messages_events_inbox.sql to already be applied';
    END IF;
    IF pg_catalog.to_regclass('public.recipient_outcomes') IS NULL THEN
        RAISE EXCEPTION '0015 requires 0004_messages_events_inbox.sql to already be applied';
    END IF;
    IF pg_catalog.to_regclass('public.campaign_enrollments') IS NULL THEN
        RAISE EXCEPTION '0015 requires 0003_mailboxes_campaigns.sql to already be applied';
    END IF;
    IF EXISTS (
        SELECT 1 FROM pg_catalog.pg_policies
        WHERE schemaname = 'public' AND tablename = 'inbound_outreach_links'
          AND policyname = 'inbound_outreach_links_sync_select'
    ) THEN
        RAISE EXCEPTION '0015 already applied: inbound_outreach_links_sync_select exists';
    END IF;
END;
$preflight$;

-- 1. Campaign enrollments: allow sync worker to check enrollment state and stop on reply
GRANT SELECT ON public.campaign_enrollments TO app_worker_sync;
GRANT UPDATE (state, stop_reason, next_step_id, version) ON public.campaign_enrollments TO app_worker_sync;

CREATE POLICY campaign_enrollments_sync_select ON public.campaign_enrollments
FOR SELECT TO app_worker_sync
USING (workspace_id = (SELECT public.app_current_workspace_id()));

CREATE POLICY campaign_enrollments_sync_update ON public.campaign_enrollments
FOR UPDATE TO app_worker_sync
USING (workspace_id = (SELECT public.app_current_workspace_id()))
WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));

-- 2. Recipient outcomes: allow sync worker to record confirmed REPLIED outcomes
GRANT SELECT ON public.recipient_outcomes TO app_worker_sync;
GRANT INSERT (id, workspace_id, enrollment_id, kind, source_key, occurred_at, observed_at, inbound_message_id)
ON public.recipient_outcomes TO app_worker_sync;

CREATE POLICY recipient_outcomes_sync_select ON public.recipient_outcomes
FOR SELECT TO app_worker_sync
USING (workspace_id = (SELECT public.app_current_workspace_id()));

CREATE POLICY recipient_outcomes_sync_insert ON public.recipient_outcomes
FOR INSERT TO app_worker_sync
WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));

-- 3. Inbound outreach links: allow sync worker to create confirmed and candidate links
GRANT SELECT ON public.inbound_outreach_links TO app_worker_sync;
GRANT INSERT (
    id,
    workspace_id,
    mailbox_id,
    inbound_message_id,
    outbound_message_id,
    campaign_id,
    enrollment_id,
    evidence_type,
    confidence,
    status,
    matched_at
) ON public.inbound_outreach_links TO app_worker_sync;
GRANT UPDATE (status, matched_at, version) ON public.inbound_outreach_links TO app_worker_sync;

CREATE POLICY inbound_outreach_links_sync_select ON public.inbound_outreach_links
FOR SELECT TO app_worker_sync
USING (workspace_id = (SELECT public.app_current_workspace_id()));

CREATE POLICY inbound_outreach_links_sync_insert ON public.inbound_outreach_links
FOR INSERT TO app_worker_sync
WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));

CREATE POLICY inbound_outreach_links_sync_update ON public.inbound_outreach_links
FOR UPDATE TO app_worker_sync
USING (workspace_id = (SELECT public.app_current_workspace_id()))
WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));

-- 4. Inbound messages: allow sync worker to update association_status on match
GRANT UPDATE (association_status, version) ON public.inbound_messages TO app_worker_sync;

CREATE POLICY inbound_messages_sync_update ON public.inbound_messages
FOR UPDATE TO app_worker_sync
USING (workspace_id = (SELECT public.app_current_workspace_id()))
WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));

-- 5. Safety holds: allow sync worker to resolve active holds
GRANT UPDATE (status, resolved_at, version) ON public.safety_holds TO app_worker_sync;

CREATE POLICY safety_holds_sync_update ON public.safety_holds
FOR UPDATE TO app_worker_sync
USING (workspace_id = (SELECT public.app_current_workspace_id()))
WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));

-- 6. Mailbox sync states: grant version update and generation tracking
GRANT UPDATE (version, connection_generation) ON public.mailbox_sync_states TO app_worker_sync;

COMMIT;
