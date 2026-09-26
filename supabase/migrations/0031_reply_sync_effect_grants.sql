-- Reply sync: let the sync role read the tables it appends reply effects to.
-- Forward migration per AGENTS.md.
--
-- Defect this fixes: when a reply is matched, ReplyRepository.
-- record_reply_outcome_and_stop_campaign (backend/app/modules/replies/
-- repository.py) runs as app_worker_sync and, in the same transaction that
-- stores the inbound message and stops the enrollment, appends an
-- `enrollment.replied` row to public.domain_events and a public.outbox_work row
-- with `INSERT ... ON CONFLICT (workspace_id, ..., semantic_key) DO NOTHING`.
-- app_worker_sync holds INSERT on both tables (0004) but no SELECT, and
-- PostgreSQL requires SELECT on the columns of an ON CONFLICT target (it must
-- read the existing row to detect the conflict). The statement therefore fails
-- with `permission denied for table domain_events`, the whole page transaction
-- rolls back, and no reply is ever stored or stops a sequence. SQLite-based unit
-- tests cannot see grants, which is how this shipped.
--
-- Objects created:
-- - POLICY domain_events_worker_sync_select ON public.domain_events (FOR SELECT)
-- - GRANT SELECT ON public.domain_events TO app_worker_sync
-- - POLICY outbox_work_worker_sync_select ON public.outbox_work (FOR SELECT)
-- - GRANT SELECT ON public.outbox_work TO app_worker_sync
-- Both policies are scoped to the transaction's workspace (same predicate as the
-- existing INSERT policies). No UPDATE or DELETE is granted: these tables stay
-- append-only for this role.
--
-- Existing-data impact: none. Application impact: enables the reply pipeline to
-- commit. Risk: low (read-only, tenant-scoped).
-- Rollback: DROP the two policies and REVOKE the two grants.
--
-- PREPARED for review per AGENTS.md §§6-8.
-- Do not apply to any shared/staging/production environment without explicit authorization.

BEGIN;
SET LOCAL search_path = '';

DO $preflight$
BEGIN
    IF pg_catalog.to_regclass('public.domain_events') IS NULL
       OR pg_catalog.to_regclass('public.outbox_work') IS NULL THEN
        RAISE EXCEPTION '0031 requires 0004_messages_events_inbox.sql to already be applied';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'app_worker_sync') THEN
        RAISE EXCEPTION '0031 requires role app_worker_sync from 0001_initial.sql';
    END IF;
    IF EXISTS (
        SELECT 1 FROM pg_catalog.pg_policies
        WHERE schemaname = 'public'
          AND policyname IN ('domain_events_worker_sync_select', 'outbox_work_worker_sync_select')
    ) THEN
        RAISE EXCEPTION '0031 already applied: one of its policies exists';
    END IF;
    IF pg_catalog.has_any_column_privilege('app_worker_sync', 'public.domain_events', 'SELECT')
       OR pg_catalog.has_any_column_privilege('app_worker_sync', 'public.outbox_work', 'SELECT') THEN
        RAISE EXCEPTION '0031 preflight failed: app_worker_sync already can SELECT domain_events/outbox_work';
    END IF;
END;
$preflight$;

CREATE POLICY domain_events_worker_sync_select ON public.domain_events
FOR SELECT TO app_worker_sync USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.domain_events TO app_worker_sync;

CREATE POLICY outbox_work_worker_sync_select ON public.outbox_work
FOR SELECT TO app_worker_sync USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.outbox_work TO app_worker_sync;

DO $postflight$
BEGIN
    IF NOT pg_catalog.has_table_privilege('app_worker_sync', 'public.domain_events', 'SELECT')
       OR NOT pg_catalog.has_table_privilege('app_worker_sync', 'public.outbox_work', 'SELECT') THEN
        RAISE EXCEPTION '0031 postcondition failed: app_worker_sync still cannot SELECT domain_events/outbox_work';
    END IF;
    IF pg_catalog.has_any_column_privilege('app_worker_sync', 'public.domain_events', 'UPDATE')
       OR pg_catalog.has_any_column_privilege('app_worker_sync', 'public.outbox_work', 'UPDATE')
       OR pg_catalog.has_table_privilege('app_worker_sync', 'public.domain_events', 'DELETE')
       OR pg_catalog.has_table_privilege('app_worker_sync', 'public.outbox_work', 'DELETE') THEN
        RAISE EXCEPTION '0031 postcondition failed: app_worker_sync gained more than append + read access';
    END IF;
END;
$postflight$;

COMMIT;
