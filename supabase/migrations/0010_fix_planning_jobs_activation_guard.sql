-- Fix app_guard_planning_activation() so ENROLL/RENDER campaign_planning_jobs
-- rows can ever be inserted. Forward migration per CLAUDE.md/AGENTS.md.
--
-- Defect this fixes: public.app_guard_planning_activation() (0003_mailboxes_
-- campaigns.sql) fires BEFORE INSERT on public.campaign_planning_jobs and,
-- for phase <> 'CAPTURE' (i.e. ENROLL/RENDER), requires
--     c.activated_audience_id = NEW.audience_id
-- against the campaign's current row. But
-- campaign_planning_jobs_phase_shape_check requires the OPPOSITE for those
-- same phases: audience_id IS NULL whenever phase IN ('ENROLL','RENDER').
-- Once a campaign is activated, campaigns_activation_shape_check forces
-- activated_audience_id to be NOT NULL. A NOT NULL column can never equal a
-- NULL value in SQL (the comparison evaluates to UNKNOWN, so the EXISTS
-- never matches), so the trigger's NOT EXISTS branch is unconditionally
-- true and it raises "Planning must use the current immutable activation"
-- on every single ENROLL/RENDER insert, with no way to construct a row that
-- satisfies both constraints at once. Phase 7 never hit this because it only
-- ever inserts phase='CAPTURE' rows, which take the trigger's exempted
-- branch. Confirmed by reproduction: POST .../campaigns/{id}/activate's
-- first campaign_planning_jobs insert (phase='ENROLL') raises
-- psycopg.errors.CheckViolation with that exact message and ERRCODE 23514
-- from this trigger.
--
-- Fix: for phase <> 'CAPTURE', check only activation_id and
-- activated_sequence_id -- audience_id is definitionally NULL for these
-- rows (already enforced by campaign_planning_jobs_phase_shape_check) and
-- comparing it served no purpose except making the check unsatisfiable.
-- activation_id + activated_sequence_id is exactly the pair that is
-- actually populated on ENROLL/RENDER rows and correctly identifies "this
-- job belongs to the campaign's current, immutable activation" -- the same
-- property the CAPTURE branch establishes via audience_id. The CAPTURE
-- branch (phase = 'CAPTURE') is completely unchanged.
--
-- PREPARED for the project owner's already-migrated development database
-- only. Do not apply to any shared/staging/production environment without a
-- separate review and explicit authorization.

BEGIN;
SET LOCAL search_path = '';

DO $preflight$
BEGIN
    IF pg_catalog.to_regclass('public.campaign_planning_jobs') IS NULL THEN
        RAISE EXCEPTION '0010 requires 0003_mailboxes_campaigns.sql to already be applied';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_proc AS proc
        JOIN pg_catalog.pg_namespace AS ns ON ns.oid = proc.pronamespace
        WHERE ns.nspname = 'public' AND proc.proname = 'app_guard_planning_activation'
    ) THEN
        RAISE EXCEPTION '0010 requires public.app_guard_planning_activation() to already exist';
    END IF;
    IF EXISTS (
        SELECT 1 FROM pg_catalog.pg_proc AS proc
        JOIN pg_catalog.pg_namespace AS ns ON ns.oid = proc.pronamespace
        WHERE ns.nspname = 'public' AND proc.proname = 'app_guard_planning_activation'
          AND pg_catalog.pg_get_functiondef(proc.oid) NOT LIKE '%activated_audience_id=NEW.audience_id%'
    ) THEN
        RAISE EXCEPTION '0010 already applied: app_guard_planning_activation no longer compares audience_id';
    END IF;
END;
$preflight$;

CREATE OR REPLACE FUNCTION public.app_guard_planning_activation() RETURNS trigger
LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path = ''
AS $function$
BEGIN
    IF NEW.phase <> 'CAPTURE' AND NOT EXISTS (SELECT 1 FROM public.campaigns c
      WHERE c.workspace_id=NEW.workspace_id AND c.id=NEW.campaign_id AND c.activation_id=NEW.activation_id
        AND c.activated_sequence_id=NEW.sequence_id) THEN
        RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Planning must use the current immutable activation';
    END IF;
    RETURN NEW;
END;
$function$;

DO $postflight$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_catalog.pg_proc AS proc
        JOIN pg_catalog.pg_namespace AS ns ON ns.oid = proc.pronamespace
        WHERE ns.nspname = 'public' AND proc.proname = 'app_guard_planning_activation'
          AND pg_catalog.pg_get_functiondef(proc.oid) LIKE '%activated_audience_id=NEW.audience_id%'
    ) THEN
        RAISE EXCEPTION '0010 failed to update app_guard_planning_activation';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_trigger AS trig
        JOIN pg_catalog.pg_class AS relation ON relation.oid = trig.tgrelid
        JOIN pg_catalog.pg_namespace AS ns ON ns.oid = relation.relnamespace
        WHERE ns.nspname = 'public' AND relation.relname = 'campaign_planning_jobs'
          AND trig.tgname = 'campaign_planning_jobs_activation_guard'
    ) THEN
        RAISE EXCEPTION '0010 postcondition failed: campaign_planning_jobs_activation_guard trigger missing';
    END IF;
END;
$postflight$;

COMMIT;
