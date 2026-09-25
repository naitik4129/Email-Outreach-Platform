-- Raise the frozen_variables size cap from 16384 to 32768 bytes. Forward
-- migration per CLAUDE.md/AGENTS.md.
--
-- frozen_variables is the merge-field snapshot built by
-- build_lead_render_context(): the standard lead fields (0021 added twelve
-- profile fields on top of the original seven) plus the lead's whole
-- custom_fields object, which is itself allowed up to 16384 bytes
-- (leads_custom_fields_check). jsonb::text also adds a space after every ':'
-- and ',', so a lead with large custom_fields plus populated profile fields
-- can legitimately exceed 16384 bytes. The insert would then fail the CHECK
-- and audience capture for that campaign would fail on a valid lead.
--
-- 32768 covers custom_fields at its maximum plus every standard field at its
-- maximum length with room for jsonb formatting. The limit is only relaxed:
-- every existing row already satisfies the new CHECK, so no data changes.
--
-- Both tables are replaced in one transaction, so there is no window without a
-- size check. app_guard_enrollment_snapshot() compares frozen_variables for
-- equality between the two tables, so their limits must stay in step.
--
-- PREPARED ONLY. Do not apply to any shared/staging/production environment
-- without the project owner's separate review and explicit authorization.

BEGIN;
SET LOCAL search_path = '';

DO $preflight$
BEGIN
    IF pg_catalog.to_regclass('public.campaign_audience_members') IS NULL
       OR pg_catalog.to_regclass('public.campaign_enrollments') IS NULL THEN
        RAISE EXCEPTION '0022 requires 0003_mailboxes_campaigns.sql to already be applied';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_constraint
        WHERE conname = 'campaign_audience_members_frozen_variables_check'
          AND conrelid = 'public.campaign_audience_members'::pg_catalog.regclass
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_constraint
        WHERE conname = 'campaign_enrollments_frozen_variables_check'
          AND conrelid = 'public.campaign_enrollments'::pg_catalog.regclass
    ) THEN
        RAISE EXCEPTION '0022 expects both frozen_variables CHECK constraints from 0003 to exist';
    END IF;
    IF EXISTS (
        SELECT 1 FROM pg_catalog.pg_constraint
        WHERE conname = 'campaign_enrollments_frozen_variables_check'
          AND conrelid = 'public.campaign_enrollments'::pg_catalog.regclass
          AND pg_catalog.pg_get_constraintdef(oid) LIKE '%32768%'
    ) THEN
        RAISE EXCEPTION '0022 already applied: frozen_variables cap is already 32768';
    END IF;
END;
$preflight$;

ALTER TABLE public.campaign_audience_members
    DROP CONSTRAINT campaign_audience_members_frozen_variables_check,
    ADD CONSTRAINT campaign_audience_members_frozen_variables_check CHECK (
        pg_catalog.jsonb_typeof(frozen_variables) = 'object'
        AND pg_catalog.octet_length(pg_catalog.convert_to(frozen_variables::text, 'UTF8')) <= 32768
    );

ALTER TABLE public.campaign_enrollments
    DROP CONSTRAINT campaign_enrollments_frozen_variables_check,
    ADD CONSTRAINT campaign_enrollments_frozen_variables_check CHECK (
        pg_catalog.jsonb_typeof(frozen_variables) = 'object'
        AND pg_catalog.octet_length(pg_catalog.convert_to(frozen_variables::text, 'UTF8')) <= 32768
    );

COMMIT;
