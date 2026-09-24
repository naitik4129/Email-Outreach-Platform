-- Phase 15: Analytics and Deliverability Infrastructure
-- Adds SELECT grant and RLS policy for app_api on public.message_attempts under product.read,
-- and supporting indexes for performant workspace, campaign, sequence, and deliverability queries.
-- Forward migration per AGENTS.md §§6-8.
--
-- Objects modified:
-- - GRANT SELECT ON public.message_attempts TO app_api;
-- - CREATE POLICY message_attempts_api_select ON public.message_attempts;
-- - CREATE INDEX messages_analytics_accepted_idx ON public.messages;
-- - CREATE INDEX messages_analytics_campaign_status_idx ON public.messages;
-- - CREATE INDEX messages_analytics_step_status_idx ON public.messages;
-- - CREATE INDEX recipient_outcomes_analytics_kind_idx ON public.recipient_outcomes;
-- - CREATE INDEX inbound_outreach_links_analytics_outbound_idx ON public.inbound_outreach_links;
-- - CREATE INDEX message_attempts_analytics_mailbox_idx ON public.message_attempts;
-- - CREATE INDEX message_attempts_analytics_error_cat_idx ON public.message_attempts;
--
-- PREPARED for review per AGENTS.md §§6-8.
-- Do not apply to any shared/staging/production environment without explicit authorization.

BEGIN;
SET LOCAL search_path = '';

DO $preflight$
BEGIN
    IF pg_catalog.to_regclass('public.messages') IS NULL THEN
        RAISE EXCEPTION '0017 requires 0004_messages_events_inbox.sql to already be applied';
    END IF;
    IF pg_catalog.to_regclass('public.message_attempts') IS NULL THEN
        RAISE EXCEPTION '0017 requires 0004_messages_events_inbox.sql to already be applied';
    END IF;
    IF pg_catalog.to_regclass('public.recipient_outcomes') IS NULL THEN
        RAISE EXCEPTION '0017 requires 0004_messages_events_inbox.sql to already be applied';
    END IF;
    IF EXISTS (
        SELECT 1 FROM pg_catalog.pg_policies
        WHERE schemaname = 'public' AND tablename = 'message_attempts'
          AND policyname = 'message_attempts_api_select'
    ) THEN
        RAISE EXCEPTION '0017 already applied: message_attempts_api_select exists';
    END IF;
END;
$preflight$;

-- 1. Grant SELECT on message_attempts to app_api
GRANT SELECT ON public.message_attempts TO app_api;

-- 2. RLS Policy allowing app_api to read message_attempts for current workspace with product.read
CREATE POLICY message_attempts_api_select ON public.message_attempts
FOR SELECT TO app_api
USING (
    workspace_id = (SELECT public.app_current_workspace_id())
    AND public.app_has_permission('product.read')
);

-- 3. Composite indexes for high-performance analytics queries
CREATE INDEX messages_analytics_accepted_idx
    ON public.messages (workspace_id, accepted_at)
    WHERE status = 'SENT';

CREATE INDEX messages_analytics_campaign_status_idx
    ON public.messages (workspace_id, campaign_id, status);

CREATE INDEX messages_analytics_step_status_idx
    ON public.messages (workspace_id, step_id, status);

CREATE INDEX recipient_outcomes_analytics_kind_idx
    ON public.recipient_outcomes (workspace_id, kind, occurred_at);

CREATE INDEX inbound_outreach_links_analytics_outbound_idx
    ON public.inbound_outreach_links (workspace_id, outbound_message_id)
    WHERE status = 'CONFIRMED';

CREATE INDEX message_attempts_analytics_mailbox_idx
    ON public.message_attempts (workspace_id, mailbox_id, evidence_state, started_at);

CREATE INDEX message_attempts_analytics_error_cat_idx
    ON public.message_attempts (workspace_id, error_category)
    WHERE error_category IS NOT NULL;

COMMIT;
