-- Fix mailboxes_guard_mailbox_generation to allow a brand-new mailbox's
-- first-ever connection. Forward migration per CLAUDE.md/AGENTS.md.
--
-- Defect this fixes: public.app_guard_mailbox_generation() (0003_mailboxes_
-- campaigns.sql) fires BEFORE UPDATE on public.mailboxes and rejects any
-- change to connected_generation unless current_connection_generation is
-- also strictly increasing past its old value. That rule exists to fence
-- stale workers on RECONNECT/REFRESH/DISCONNECT (a real, still-needed
-- protection), but it does not distinguish "reconnecting past a prior
-- connection" from "connecting for the very first time": when a mailbox is
-- inserted CONNECTING with connected_generation NULL and
-- current_connection_generation already 1 (required, since
-- mailboxes_connection_generation_check forbids 0 and
-- mailbox_connections_mailbox_fkey forces the mailbox row to exist before
-- its first mailbox_connections row can be inserted), the only legal next
-- current_connection_generation value the trigger accepts is 2 -- but no
-- second connection generation exists yet to promote to. The result: no
-- newly connected mailbox (Gmail, Microsoft, or SMTP alike) can ever be
-- promoted from CONNECTING to CONNECTED. Confirmed by reproduction: the
-- Gmail OAuth complete endpoint's "create new mailbox" path raises
-- psycopg.errors.CheckViolation "Connection identity is immutable and
-- generation changes must fence stale workers" (ERRCODE 23514) from this
-- trigger on the very first UPDATE that sets connected_generation = 1.
--
-- Fix: add "OLD.connected_generation IS NOT NULL" to that OR-branch's
-- conditions, so the fencing requirement only applies once a connection has
-- actually existed before. Every other branch of the guard (workspace/
-- provider immutability, provider_account_id immutability, generation
-- non-regression, and the CONNECTED-to-non-CONNECTED transition fencing
-- requirement) is unchanged.
--
-- PREPARED for the project owner's already-migrated development database
-- only. Do not apply to any shared/staging/production environment without a
-- separate review and explicit authorization.

BEGIN;
SET LOCAL search_path = '';

DO $preflight$
BEGIN
    IF pg_catalog.to_regclass('public.mailboxes') IS NULL THEN
        RAISE EXCEPTION '0009 requires 0003_mailboxes_campaigns.sql to already be applied';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_proc AS proc
        JOIN pg_catalog.pg_namespace AS ns ON ns.oid = proc.pronamespace
        WHERE ns.nspname = 'public' AND proc.proname = 'app_guard_mailbox_generation'
    ) THEN
        RAISE EXCEPTION '0009 requires public.app_guard_mailbox_generation() to already exist';
    END IF;
    IF EXISTS (
        SELECT 1 FROM pg_catalog.pg_proc AS proc
        JOIN pg_catalog.pg_namespace AS ns ON ns.oid = proc.pronamespace
        WHERE ns.nspname = 'public' AND proc.proname = 'app_guard_mailbox_generation'
          AND pg_catalog.pg_get_functiondef(proc.oid) LIKE '%OLD.connected_generation IS NOT NULL AND NEW.connected_generation IS DISTINCT%'
    ) THEN
        RAISE EXCEPTION '0009 already applied: app_guard_mailbox_generation already exempts the first connection';
    END IF;
END;
$preflight$;

CREATE OR REPLACE FUNCTION public.app_guard_mailbox_generation() RETURNS trigger
LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path = ''
AS $function$
BEGIN
    IF (NEW.workspace_id,NEW.provider) IS DISTINCT FROM (OLD.workspace_id,OLD.provider)
       OR (OLD.provider_account_id IS NOT NULL AND NEW.provider_account_id IS DISTINCT FROM OLD.provider_account_id)
       OR NEW.current_connection_generation < OLD.current_connection_generation
       OR (OLD.connection_state='CONNECTED' AND NEW.connection_state <> 'CONNECTED' AND NEW.current_connection_generation <= OLD.current_connection_generation)
       OR (OLD.connected_generation IS NOT NULL AND NEW.connected_generation IS DISTINCT FROM OLD.connected_generation AND NEW.connected_generation IS NOT NULL
           AND NEW.current_connection_generation <= OLD.current_connection_generation) THEN
        RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Connection identity is immutable and generation changes must fence stale workers';
    END IF;
    RETURN NEW;
END;
$function$;

DO $postflight$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_proc AS proc
        JOIN pg_catalog.pg_namespace AS ns ON ns.oid = proc.pronamespace
        WHERE ns.nspname = 'public' AND proc.proname = 'app_guard_mailbox_generation'
          AND pg_catalog.pg_get_functiondef(proc.oid) LIKE '%OLD.connected_generation IS NOT NULL AND NEW.connected_generation IS DISTINCT%'
    ) THEN
        RAISE EXCEPTION '0009 failed to update app_guard_mailbox_generation';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_trigger AS trig
        JOIN pg_catalog.pg_class AS relation ON relation.oid = trig.tgrelid
        JOIN pg_catalog.pg_namespace AS ns ON ns.oid = relation.relnamespace
        WHERE ns.nspname = 'public' AND relation.relname = 'mailboxes'
          AND trig.tgname = 'mailboxes_guard_mailbox_generation'
    ) THEN
        RAISE EXCEPTION '0009 postcondition failed: mailboxes_guard_mailbox_generation trigger missing';
    END IF;
END;
$postflight$;

COMMIT;
