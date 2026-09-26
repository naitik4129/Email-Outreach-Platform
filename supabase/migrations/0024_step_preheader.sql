-- Add an optional pre-header (inbox preview text) to sequence steps and
-- template versions. Forward migration per CLAUDE.md/AGENTS.md.
--
-- Migration review
--   Purpose:        let an EMAIL step / template version carry pre-header text.
--                   The renderer injects it as a hidden preview-text element at
--                   the top of the rendered HTML body, so the existing message
--                   content digest and send-gate recompute cover it unchanged.
--   Creates:        sequence_steps.email_preheader (text NULL),
--                   template_versions.preheader (text NULL), one CHECK on each.
--   Modifies:       column-level INSERT/UPDATE grants for app_api (0003/0002
--                   list writable columns explicitly, so the new columns need
--                   their own grants).
--   Deletes:        nothing.
--   Constraints:    1..255 chars, no control characters; on sequence_steps only
--                   allowed on EMAIL steps.
--   Indexes:        none.
--   RLS/security:   unchanged. Existing row policies are row-level and already
--                   apply to the new columns; SELECT is table-level for every
--                   role. sequence_steps_guard_frozen fires on any UPDATE, so
--                   frozen sequences remain immutable for the new column too.
--   Existing data:  none touched. ADD COLUMN ... NULL is metadata-only; NULL
--                   means "no pre-header", so every existing step/template and
--                   every frozen digest is unaffected.
--   App impact:     backend reads/writes the new columns, so this migration must
--                   be applied BEFORE deploying the matching backend/workers.
--   Risk:           low.
--   Rollback:       REVOKE the four column grants, DROP CONSTRAINT
--                   sequence_steps_preheader_check and
--                   template_versions_preheader_check, then DROP COLUMN both
--                   columns (only safe while no row has a pre-header the owner
--                   wants to keep).
--
-- PREPARED ONLY. Do not apply to any shared/staging/production environment
-- without the project owner's separate review and explicit authorization.

BEGIN;
SET LOCAL search_path = '';

DO $preflight$
BEGIN
    IF pg_catalog.to_regclass('public.sequence_steps') IS NULL
       OR pg_catalog.to_regclass('public.template_versions') IS NULL THEN
        RAISE EXCEPTION '0024 requires 0002_contacts_content.sql and 0003_mailboxes_campaigns.sql to already be applied';
    END IF;
    IF EXISTS (
        SELECT 1 FROM pg_catalog.pg_attribute
        WHERE attrelid = 'public.sequence_steps'::pg_catalog.regclass
          AND attname = 'email_preheader' AND NOT attisdropped
    ) OR EXISTS (
        SELECT 1 FROM pg_catalog.pg_attribute
        WHERE attrelid = 'public.template_versions'::pg_catalog.regclass
          AND attname = 'preheader' AND NOT attisdropped
    ) THEN
        RAISE EXCEPTION '0024 already applied: pre-header column already exists';
    END IF;
END;
$preflight$;

ALTER TABLE public.sequence_steps
    ADD COLUMN email_preheader text,
    ADD CONSTRAINT sequence_steps_preheader_check CHECK (
        email_preheader IS NULL
        OR (
            kind = 'EMAIL'
            AND pg_catalog.char_length(email_preheader) BETWEEN 1 AND 255
            AND email_preheader !~ '[[:cntrl:]]'
        )
    );

ALTER TABLE public.template_versions
    ADD COLUMN preheader text,
    ADD CONSTRAINT template_versions_preheader_check CHECK (
        preheader IS NULL
        OR (
            pg_catalog.char_length(preheader) BETWEEN 1 AND 255
            AND preheader !~ '[[:cntrl:]]'
        )
    );

GRANT INSERT (email_preheader) ON public.sequence_steps TO app_api;
GRANT UPDATE (email_preheader) ON public.sequence_steps TO app_api;
GRANT INSERT (preheader) ON public.template_versions TO app_api;

DO $postcondition$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_constraint
        WHERE conname = 'sequence_steps_preheader_check'
          AND conrelid = 'public.sequence_steps'::pg_catalog.regclass
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_constraint
        WHERE conname = 'template_versions_preheader_check'
          AND conrelid = 'public.template_versions'::pg_catalog.regclass
    ) THEN
        RAISE EXCEPTION '0024 postcondition failed: pre-header CHECK constraints missing';
    END IF;
END;
$postcondition$;

COMMIT;
