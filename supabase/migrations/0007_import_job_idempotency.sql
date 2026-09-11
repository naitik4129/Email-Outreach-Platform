-- Import job idempotency. Forward migration per CLAUDE.md/AGENTS.md.
--
-- Phase 3 (CSV Import) needs a server-enforced guarantee that confirming the
-- same uploaded object twice (double-click Confirm, browser retry, duplicate
-- API call) cannot create two import_jobs rows for it. app_api has no UPDATE
-- policy on import_jobs (only app_worker_general does, per
-- 0002_contacts_content.sql), so a job row must be complete and correct in
-- one INSERT -- there is no follow-up call that could reconcile a duplicate
-- afterwards. A plain application-level "check then insert" would race under
-- concurrent requests, so the safety net has to be a database constraint the
-- INSERT itself can conflict against (INSERT ... ON CONFLICT DO NOTHING).
--
-- storage_object_key is already server-generated per upload (never derived
-- from a client-supplied filename) and embeds the workspace, so one key is
-- naturally scoped to at most one job.
--
-- PREPARED for the project owner's already-migrated development database
-- only. Do not apply to any shared/staging/production environment without a
-- separate review and explicit authorization.

BEGIN;
SET LOCAL search_path = '';

DO $preflight$
BEGIN
    IF pg_catalog.to_regclass('public.import_jobs') IS NULL THEN
        RAISE EXCEPTION '0007 requires 0002_contacts_content.sql to already be applied';
    END IF;
    IF EXISTS (
        SELECT 1 FROM pg_catalog.pg_constraint
        WHERE conname = 'import_jobs_storage_object_key_unique'
    ) THEN
        RAISE EXCEPTION '0007 already applied: import_jobs_storage_object_key_unique exists';
    END IF;
END;
$preflight$;

ALTER TABLE public.import_jobs
    ADD CONSTRAINT import_jobs_storage_object_key_unique UNIQUE (workspace_id, storage_object_key);

COMMENT ON CONSTRAINT import_jobs_storage_object_key_unique ON public.import_jobs IS
    'One import job per uploaded object. Lets the confirm-import command use INSERT ... ON CONFLICT (workspace_id, storage_object_key) DO NOTHING to make double-click/retry confirmation idempotent without a separate command-receipt table.';

COMMIT;
