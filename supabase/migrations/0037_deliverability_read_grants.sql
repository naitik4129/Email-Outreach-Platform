-- Let the API role read the tables the Deliverability section of the dashboard needs.
--
-- Migration:      0037_deliverability_read_grants.sql
-- Purpose:        GET /analytics/deliverability fails on the production database
--                 with "permission denied for table safety_holds" (SQLSTATE 42501):
--                 app_api has no SELECT grant and no RLS policy on safety_holds
--                 there. The repo's 0014 defines both, but they are missing on the
--                 managed database (its safety_holds policy list has none for
--                 app_api). Rather than re-running all of 0014 (which also grants
--                 webhook INSERT rights this feature does not need), this adds
--                 only the read access the endpoint uses.
-- Creates:        RLS policies safety_holds_api_analytics_select and
--                 message_attempts_api_analytics_select (SELECT, app_api). The
--                 second duplicates 0017's message_attempts_api_select definition
--                 so the failure breakdown works even if 0017's policy is also
--                 missing; if both exist they are OR'd, which is harmless.
-- Modifies:       GRANT SELECT on public.safety_holds and public.message_attempts
--                 to app_api. No table, column, constraint or index changes.
-- Deletes:        Nothing (only this migration's own policies are dropped and
--                 recreated on re-run).
-- Security:       Read-only, scoped to the caller's current workspace and gated on
--                 the product.read permission, the same predicate the other
--                 app_api read policies use (0004, 0017). No INSERT/UPDATE/DELETE
--                 rights are granted. Tenant isolation is by workspace_id under
--                 FORCE ROW LEVEL SECURITY; a foreign workspace's holds are not
--                 visible.
-- Existing data:  Unchanged.
-- Application:    Fixes the Deliverability section (currently a 500). Nothing else
--                 changes.
-- Compatibility:  If 0014 is applied later it adds its own permissive
--                 safety_holds_api_select policy; the two policies are OR'd, which
--                 is harmless.
-- Risk:           Low. Idempotent (safe to re-run).
-- Recovery:       DROP POLICY safety_holds_api_analytics_select ON public.safety_holds;
--                 REVOKE SELECT ON public.safety_holds FROM app_api;
--                 (this would break the Deliverability section again).
-- Needs:          0004 applied (safety_holds, message_attempts, app_api role and
--                 the app_has_permission helper).
--
-- PREPARED ONLY. Do not apply to any shared/staging/production environment
-- without the project owner's separate review and explicit authorization.

BEGIN;
SET LOCAL search_path = '';

DO $preflight$
BEGIN
    IF pg_catalog.to_regclass('public.safety_holds') IS NULL
       OR pg_catalog.to_regclass('public.message_attempts') IS NULL
       OR NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'app_api')
       OR pg_catalog.to_regprocedure('public.app_has_permission(text)') IS NULL
       OR pg_catalog.to_regprocedure('public.app_current_workspace_id()') IS NULL THEN
        RAISE EXCEPTION '0037 requires 0004 to already be applied';
    END IF;
END;
$preflight$;

GRANT SELECT ON public.safety_holds TO app_api;

DROP POLICY IF EXISTS safety_holds_api_analytics_select ON public.safety_holds;
CREATE POLICY safety_holds_api_analytics_select ON public.safety_holds
FOR SELECT TO app_api
USING (
    workspace_id = (SELECT public.app_current_workspace_id())
    AND public.app_has_permission('product.read')
);

-- Same predicate as 0017's message_attempts_api_select; repeated so the endpoint
-- does not depend on 0017 alone.
GRANT SELECT ON public.message_attempts TO app_api;

DROP POLICY IF EXISTS message_attempts_api_analytics_select ON public.message_attempts;
CREATE POLICY message_attempts_api_analytics_select ON public.message_attempts
FOR SELECT TO app_api
USING (
    workspace_id = (SELECT public.app_current_workspace_id())
    AND public.app_has_permission('product.read')
);

DO $postflight$
BEGIN
    IF NOT pg_catalog.has_table_privilege('app_api', 'public.safety_holds', 'SELECT')
       OR NOT pg_catalog.has_table_privilege('app_api', 'public.message_attempts', 'SELECT') THEN
        RAISE EXCEPTION '0037 postcondition failed: app_api cannot read safety_holds / message_attempts';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_policies
        WHERE schemaname = 'public' AND tablename = 'safety_holds'
          AND policyname = 'safety_holds_api_analytics_select' AND cmd = 'SELECT'
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_policies
        WHERE schemaname = 'public' AND tablename = 'message_attempts'
          AND policyname = 'message_attempts_api_analytics_select' AND cmd = 'SELECT'
    ) THEN
        RAISE EXCEPTION '0037 postcondition failed: read policies missing';
    END IF;
END;
$postflight$;

COMMIT;
