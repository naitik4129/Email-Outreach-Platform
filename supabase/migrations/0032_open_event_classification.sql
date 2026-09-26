-- Classify open-tracking pixel hits: only human-like hits count as an open.
-- Forward migration per AGENTS.md.
--
-- Why: the public pixel endpoint recorded EVERY request as an open. Delivery-time
-- scanners, link/image prefetchers and privacy proxies fetch the same URL without
-- a person reading the email, so unread emails showed as "Opened" (a whole bulk
-- send at once). The application now classifies each request and needs somewhere
-- to keep the result: opens are counted from qualified hits only, automated hits
-- stay as evidence.
--
-- Objects modified (all on public.message_events, OPENED rows only in practice):
-- - qualified_at      timestamptz  first human-like hit; NULL = never counted as opened
-- - qualified_count   integer      human-like hits (>= 0)
-- - automated_count   integer      hits classified automated (>= 0)
-- - last_user_agent   text         most recent user agent, <= 200 chars (diagnostics)
-- - GRANT INSERT / UPDATE on exactly these columns to app_api (the public pixel
--   endpoint). No policy changes: the 0030 OPENED-only INSERT/UPDATE policies
--   already scope which rows those grants can touch. app_api still cannot change
--   kind, bounce fields or delete.
--
-- Existing-data impact: additive. Existing OPENED rows get qualified_at NULL and
-- counts 0, so they STOP counting as opened in analytics. They were recorded
-- without any classification and cannot be told apart from scanner hits, so they
-- are deliberately not backfilled. BOUNCED rows are untouched.
-- Application impact: the new code writes these columns; apply this BEFORE
-- deploying it. Analytics read qualified_at / qualified_count.
-- Rollback: ALTER TABLE public.message_events DROP COLUMN qualified_at,
-- DROP COLUMN qualified_count, DROP COLUMN automated_count,
-- DROP COLUMN last_user_agent; (loses only classification evidence; the
-- previous code would then count every OPENED row again).
--
-- PREPARED for review per AGENTS.md §§6-8.
-- Do not apply to any shared/staging/production environment without explicit authorization.

BEGIN;
SET LOCAL search_path = '';

DO $preflight$
BEGIN
    IF pg_catalog.to_regclass('public.message_events') IS NULL THEN
        RAISE EXCEPTION '0032 requires 0030_message_tracking.sql to already be applied';
    END IF;
    IF EXISTS (
        SELECT 1 FROM pg_catalog.pg_attribute
        WHERE attrelid = 'public.message_events'::pg_catalog.regclass
          AND attname IN ('qualified_at', 'qualified_count', 'automated_count', 'last_user_agent')
          AND NOT attisdropped
    ) THEN
        RAISE EXCEPTION '0032 already applied: message_events classification columns exist';
    END IF;
END;
$preflight$;

ALTER TABLE public.message_events
    ADD COLUMN qualified_at timestamptz,
    ADD COLUMN qualified_count integer NOT NULL DEFAULT 0,
    ADD COLUMN automated_count integer NOT NULL DEFAULT 0,
    ADD COLUMN last_user_agent text,
    ADD CONSTRAINT message_events_qualified_count_check CHECK (qualified_count >= 0),
    ADD CONSTRAINT message_events_automated_count_check CHECK (automated_count >= 0),
    ADD CONSTRAINT message_events_user_agent_check
        CHECK (last_user_agent IS NULL OR pg_catalog.char_length(last_user_agent) <= 200);

-- The 0030 INSERT grant is column-scoped, so the new columns need their own.
GRANT INSERT (qualified_at, qualified_count, automated_count, last_user_agent)
    ON public.message_events TO app_api;
GRANT UPDATE (qualified_at, qualified_count, automated_count, last_user_agent)
    ON public.message_events TO app_api;

DO $postflight$
BEGIN
    IF NOT pg_catalog.has_column_privilege('app_api', 'public.message_events', 'qualified_count', 'INSERT')
       OR NOT pg_catalog.has_column_privilege('app_api', 'public.message_events', 'qualified_count', 'UPDATE')
       OR NOT pg_catalog.has_column_privilege('app_api', 'public.message_events', 'automated_count', 'UPDATE')
       OR NOT pg_catalog.has_column_privilege('app_api', 'public.message_events', 'qualified_at', 'UPDATE')
       OR NOT pg_catalog.has_column_privilege('app_api', 'public.message_events', 'last_user_agent', 'UPDATE') THEN
        RAISE EXCEPTION '0032 postcondition failed: app_api cannot record open classification';
    END IF;
    IF pg_catalog.has_column_privilege('app_api', 'public.message_events', 'kind', 'UPDATE')
       OR pg_catalog.has_column_privilege('app_api', 'public.message_events', 'bounce_type', 'UPDATE')
       OR pg_catalog.has_table_privilege('app_api', 'public.message_events', 'DELETE') THEN
        RAISE EXCEPTION '0032 postcondition failed: app_api gained more than open counting';
    END IF;
END;
$postflight$;

COMMIT;
