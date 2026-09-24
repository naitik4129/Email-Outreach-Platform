-- Phase 14: Unified Inbox
-- Adds durable read_at tracking on public.conversations, supporting indexes for
-- keyset pagination and inbox filtering, trigger guard protecting archived_at,
-- and grants for app_api to update read_at under product.read.
-- Forward migration per AGENTS.md §§6-8.
--
-- Objects modified:
-- - ALTER TABLE public.conversations ADD COLUMN read_at timestamptz;
-- - CREATE FUNCTION public.app_guard_conversation_update();
-- - CREATE TRIGGER conversations_archive_guard ON public.conversations;
-- - CREATE INDEX conversations_inbox_active_idx ON public.conversations;
-- - CREATE INDEX conversations_inbox_archived_idx ON public.conversations;
-- - CREATE INDEX conversations_inbox_mailbox_idx ON public.conversations;
-- - CREATE INDEX conversations_inbox_campaign_idx ON public.conversations;
-- - GRANT UPDATE (read_at) ON public.conversations TO app_api;
-- - CREATE POLICY conversations_api_update_read ON public.conversations FOR UPDATE TO app_api;
--
-- PREPARED for review per AGENTS.md §§6-8.
-- Do not apply to any shared/staging/production environment without explicit authorization.

BEGIN;
SET LOCAL search_path = '';

DO $preflight$
BEGIN
    IF pg_catalog.to_regclass('public.conversations') IS NULL THEN
        RAISE EXCEPTION '0016 requires 0004_messages_events_inbox.sql to already be applied';
    END IF;
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'conversations'
          AND column_name = 'read_at'
    ) THEN
        RAISE EXCEPTION '0016 already applied: conversations.read_at exists';
    END IF;
END;
$preflight$;

-- 1. Add read_at timestamp to conversations table
ALTER TABLE public.conversations
    ADD COLUMN read_at timestamptz;

-- 2. Guard trigger: strictly enforce that archived_at can only be updated by roles with inbox.manage
CREATE OR REPLACE FUNCTION public.app_guard_conversation_update() RETURNS trigger
LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path = ''
AS $function$
BEGIN
    IF NEW.archived_at IS DISTINCT FROM OLD.archived_at THEN
        IF NOT public.app_has_permission('inbox.manage') THEN
            RAISE EXCEPTION USING
                ERRCODE = '42501',
                MESSAGE = 'Permission denied: inbox.manage capability required to archive or unarchive conversations';
        END IF;
    END IF;
    RETURN NEW;
END;
$function$;

CREATE TRIGGER conversations_archive_guard
    BEFORE UPDATE ON public.conversations
    FOR EACH ROW EXECUTE FUNCTION public.app_guard_conversation_update();

-- 3. Composite indexes for high-performance keyset pagination and filtering
CREATE INDEX conversations_inbox_active_idx
    ON public.conversations (workspace_id, latest_activity_at DESC, id DESC)
    WHERE archived_at IS NULL;

CREATE INDEX conversations_inbox_archived_idx
    ON public.conversations (workspace_id, latest_activity_at DESC, id DESC)
    WHERE archived_at IS NOT NULL;

CREATE INDEX conversations_inbox_mailbox_idx
    ON public.conversations (workspace_id, mailbox_id, latest_activity_at DESC, id DESC)
    WHERE archived_at IS NULL;

CREATE INDEX conversations_inbox_campaign_idx
    ON public.conversations (workspace_id, campaign_summary_id, latest_activity_at DESC, id DESC)
    WHERE archived_at IS NULL AND campaign_summary_id IS NOT NULL;

-- 4. Grant UPDATE (read_at) on conversations to app_api
GRANT UPDATE (read_at) ON public.conversations TO app_api;

-- 5. RLS Policy allowing app_api to update read_at with product.read
CREATE POLICY conversations_api_update_read ON public.conversations
FOR UPDATE TO app_api
USING (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('product.read'))
WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('product.read'));

COMMIT;
