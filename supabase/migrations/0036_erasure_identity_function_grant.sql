-- Let the erasure role call the workspace-role helper (fix for 0033).
--
-- Migration:      0036_erasure_identity_function_grant.sql
-- Purpose:        0033 ran `GRANT EXECUTE ON FUNCTION app_current_workspace_role()
--                 ... TO app_erasure`. That function is owned by
--                 app_foundation_reader (0001), not by the migration user. When
--                 the grantor is neither the owner nor a member of the owning role,
--                 PostgreSQL does NOT fail: it emits a WARNING ("no privileges were
--                 granted") and skips the grant. As a superuser (the local test
--                 database) the grant works, so tests passed; on a managed database
--                 where the migration user is not a superuser it was silently
--                 skipped. Result: app_erasure cannot run app_has_permission(),
--                 so every erasure / purge / delete command (0033, 0034, 0035)
--                 fails with "permission denied for function
--                 app_current_workspace_role" (SQLSTATE 42501).
-- Creates:        Nothing.
-- Modifies:       One EXECUTE grant: app_current_workspace_role() to app_erasure.
--                 The migration user becomes a member of app_foundation_reader only
--                 for the duration of this transaction (the same temporary
--                 elevation 0001 used) and is removed again before COMMIT.
-- Deletes:        Nothing.
-- Constraints/Indexes/RLS/Triggers: none. No data is read or changed.
-- Security:       app_erasure is NOLOGIN, NOBYPASSRLS and has no runtime member
--                 (asserted below). The function only reports the caller's role in
--                 the current workspace from session settings. It is the same
--                 grant 0033 already intended.
-- Existing data:  Unchanged.
-- Application:    Fixes the delete / erase actions, which currently fail with a
--                 database permission error. Nothing else changes.
-- Risk:           Low. Idempotent (re-running is harmless). The postflight makes
--                 the migration fail loudly if the grant did not take effect,
--                 instead of skipping it silently as before.
-- Recovery:       REVOKE EXECUTE ON FUNCTION public.app_current_workspace_role()
--                 FROM app_erasure (this would break the erasure commands again).
-- Needs:          0033 applied.
--
-- PREPARED ONLY. Do not apply to any shared/staging/production environment
-- without the project owner's separate review and explicit authorization.

BEGIN;
SET LOCAL search_path = '';

DO $preflight$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'app_erasure')
       OR NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'app_foundation_reader')
       OR pg_catalog.to_regprocedure('public.app_current_workspace_role()') IS NULL THEN
        RAISE EXCEPTION '0036 requires 0001 and 0033 to already be applied';
    END IF;
END;
$preflight$;

-- Temporary migration-only elevation (as in 0001); revoked before COMMIT.
GRANT app_foundation_reader TO CURRENT_USER WITH INHERIT TRUE, SET TRUE;

-- The other identity helpers are owned by the migration user, so 0033's grants for
-- them took effect; repeated here so every function the commands rely on is
-- granted in one place (idempotent).
GRANT EXECUTE ON FUNCTION
    public.app_current_user_id(), public.app_current_workspace_id(),
    public.app_current_workspace_role(), public.app_has_permission(text)
    TO app_erasure;

REVOKE app_foundation_reader FROM CURRENT_USER;

DO $postflight$
BEGIN
    IF NOT pg_catalog.has_function_privilege('app_erasure', 'public.app_current_user_id()', 'EXECUTE')
       OR NOT pg_catalog.has_function_privilege('app_erasure', 'public.app_current_workspace_id()', 'EXECUTE')
       OR NOT pg_catalog.has_function_privilege('app_erasure', 'public.app_current_workspace_role()', 'EXECUTE')
       OR NOT pg_catalog.has_function_privilege('app_erasure', 'public.app_has_permission(text)', 'EXECUTE') THEN
        RAISE EXCEPTION '0036 postcondition failed: app_erasure cannot execute the identity helpers (a grant was skipped)';
    END IF;
    IF pg_catalog.pg_has_role('app_api', 'app_erasure', 'MEMBER')
       OR pg_catalog.pg_has_role('app_worker_send', 'app_erasure', 'MEMBER')
       OR pg_catalog.pg_has_role('app_worker_general', 'app_erasure', 'MEMBER')
       OR pg_catalog.pg_has_role('app_api', 'app_foundation_reader', 'MEMBER') THEN
        RAISE EXCEPTION '0036 forbids runtime membership in app_erasure or app_foundation_reader';
    END IF;
END;
$postflight$;

COMMIT;
