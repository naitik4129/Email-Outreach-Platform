-- Revoke default PUBLIC execute on app_guard_conversation_update(). Forward
-- migration per CLAUDE.md/AGENTS.md.
--
-- 0016_unified_inbox.sql created this trigger function but, unlike every other
-- guard function in this schema, did not REVOKE the default PUBLIC EXECUTE
-- privilege. It is a trigger function (RETURNS trigger) so it cannot be called
-- directly as a SQL function, which keeps the exposure low, but the schema's
-- convention is that no guard function is executable by PUBLIC/anon/
-- authenticated/service_role, and scripts/check_migrations.py enforces it.
--
-- Trigger functions are permission-checked when the trigger is created, not
-- when it fires, so the existing conversations_archive_guard trigger keeps
-- working for app_api unchanged. 0016 is an applied migration and is not edited.
--
-- PREPARED ONLY. Do not apply to any shared/staging/production environment
-- without the project owner's separate review and explicit authorization.

BEGIN;
SET LOCAL search_path = '';

DO $preflight$
BEGIN
    IF pg_catalog.to_regprocedure('public.app_guard_conversation_update()') IS NULL THEN
        RAISE EXCEPTION '0023 requires 0016_unified_inbox.sql to already be applied';
    END IF;
END;
$preflight$;

REVOKE ALL ON FUNCTION public.app_guard_conversation_update() FROM PUBLIC, anon, authenticated, service_role;

COMMIT;
