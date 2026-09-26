-- Message-level tracking events: email opens and bounces.
-- Forward migration per AGENTS.md.
--
-- Why: recipient_outcomes records what happened to an ENROLLMENT (REPLIED,
-- HARD_BOUNCE, ...). Open and bounce analytics need what happened to a single
-- outbound MESSAGE: "Opened" and "Bounced" count emails, Bounce Rate is
-- bounced emails / sent emails, and a bounce must attach to the step that was
-- sent. No table records that, so open tracking could not exist and bounces
-- could not be tied to a message, a step or a campaign.
--
-- Objects created:
-- - TABLE public.message_events, one row per (message, kind), kind IN
--     ('OPENED', 'BOUNCED'). Repeated opens or duplicate/late bounce
--     notifications update the existing row (occurrence_count,
--     last_occurred_at) instead of adding rows, so distinct-message metrics
--     cannot be inflated by retries, prefetching or webhook redelivery.
--     Lead, campaign, sequence, step and mailbox are reached through the
--     message (FK), which is the single source of truth for them.
-- - RLS policies + grants:
--     app_api            SELECT (product.read; opens also by workspace alone, see
--                        below); INSERT + UPDATE(counters) scoped
--                        to the transaction's workspace, used by the public
--                        open-tracking endpoint after it verifies a signed token.
--     app_worker_general SELECT, INSERT, UPDATE(bounce fields, counters) for the
--                        bounce processor.
-- - GRANT UPDATE (rfc_message_id) ON public.messages TO app_worker_send. The
--     send path must record the Message-ID each email carries, because replies
--     and bounces quote it; without it they cannot be matched to the email.
--     The existing immutability trigger already allows NULL -> value only.
--
-- Existing-data impact: none (new table; one column grant). Existing sent
-- messages have no Message-ID recorded, so replies/bounces for emails sent
-- before this change cannot be matched by Message-ID.
-- Application impact: the new code inserts into message_events and updates
-- messages.rfc_message_id; it must not run against a database without this
-- migration.
-- Rollback: DROP TABLE public.message_events; REVOKE UPDATE (rfc_message_id)
-- ON public.messages FROM app_worker_send; (data loss limited to tracking rows).
--
-- PREPARED for review per AGENTS.md §§6-8.
-- Do not apply to any shared/staging/production environment without explicit authorization.

BEGIN;
SET LOCAL search_path = '';

DO $preflight$
BEGIN
    IF pg_catalog.to_regclass('public.messages') IS NULL THEN
        RAISE EXCEPTION '0030 requires 0004_messages_events_inbox.sql to already be applied';
    END IF;
    IF pg_catalog.to_regclass('public.message_events') IS NOT NULL THEN
        RAISE EXCEPTION '0030 already applied: public.message_events exists';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'app_api')
       OR NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'app_worker_general')
       OR NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'app_worker_send') THEN
        RAISE EXCEPTION '0030 requires roles app_api, app_worker_general and app_worker_send from 0001_initial.sql';
    END IF;
    IF pg_catalog.has_column_privilege('app_worker_send', 'public.messages', 'rfc_message_id', 'UPDATE') THEN
        RAISE EXCEPTION '0030 preflight failed: app_worker_send already may update messages.rfc_message_id';
    END IF;
END;
$preflight$;

CREATE TABLE public.message_events (
    id uuid DEFAULT pg_catalog.gen_random_uuid() CONSTRAINT message_events_pkey PRIMARY KEY,
    workspace_id uuid NOT NULL,
    message_id uuid NOT NULL,
    kind text NOT NULL,
    bounce_type text,
    bounce_code text,
    source text NOT NULL,
    detail text,
    first_occurred_at timestamptz NOT NULL,
    last_occurred_at timestamptz NOT NULL,
    occurrence_count integer NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    CONSTRAINT message_events_workspace_fkey FOREIGN KEY (workspace_id)
        REFERENCES public.workspaces (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT message_events_message_fkey FOREIGN KEY (workspace_id, message_id)
        REFERENCES public.messages (workspace_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT message_events_identity_key UNIQUE (workspace_id, message_id, kind),
    CONSTRAINT message_events_kind_check CHECK (kind IN ('OPENED', 'BOUNCED')),
    CONSTRAINT message_events_bounce_shape_check CHECK (
        (kind = 'BOUNCED' AND bounce_type IN ('HARD', 'SOFT', 'UNKNOWN'))
        OR (kind = 'OPENED' AND bounce_type IS NULL AND bounce_code IS NULL)
    ),
    CONSTRAINT message_events_count_check CHECK (occurrence_count >= 1),
    CONSTRAINT message_events_source_check CHECK (pg_catalog.char_length(source) BETWEEN 1 AND 50),
    CONSTRAINT message_events_bounce_code_check CHECK (bounce_code IS NULL OR pg_catalog.char_length(bounce_code) <= 20),
    CONSTRAINT message_events_detail_check CHECK (detail IS NULL OR pg_catalog.char_length(detail) <= 300)
);

CREATE INDEX message_events_kind_time_idx
    ON public.message_events (workspace_id, kind, last_occurred_at);

ALTER TABLE public.message_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.message_events FORCE ROW LEVEL SECURITY;
REVOKE ALL PRIVILEGES ON TABLE public.message_events FROM PUBLIC, anon, authenticated, service_role;

-- Analytics / lead timeline readers.
CREATE POLICY message_events_api_select ON public.message_events
FOR SELECT TO app_api
USING (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('product.read'));
GRANT SELECT ON public.message_events TO app_api;

-- Public open-tracking endpoint: no user is signed in, the signed token is the
-- credential and the endpoint sets the workspace for the transaction, exactly
-- like the webhook receipt endpoints (0014). Scoped to that workspace only.
-- INSERT ... ON CONFLICT DO UPDATE must also be able to SEE the existing row, and
-- there is no user here for the product.read check above, so opens (and only
-- opens) are visible to app_api by workspace alone.
CREATE POLICY message_events_api_select_opens ON public.message_events
FOR SELECT TO app_api
USING (workspace_id = (SELECT public.app_current_workspace_id()) AND kind = 'OPENED');

CREATE POLICY message_events_api_insert ON public.message_events
FOR INSERT TO app_api
WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()) AND kind = 'OPENED');
GRANT INSERT (id, workspace_id, message_id, kind, source, first_occurred_at, last_occurred_at, occurrence_count)
    ON public.message_events TO app_api;

CREATE POLICY message_events_api_update ON public.message_events
FOR UPDATE TO app_api
USING (workspace_id = (SELECT public.app_current_workspace_id()) AND kind = 'OPENED')
WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()) AND kind = 'OPENED');
GRANT UPDATE (occurrence_count, last_occurred_at) ON public.message_events TO app_api;

-- Bounce processor (webhook events and delivery-status notifications).
CREATE POLICY message_events_worker_general_select ON public.message_events
FOR SELECT TO app_worker_general USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.message_events TO app_worker_general;

CREATE POLICY message_events_worker_general_insert ON public.message_events
FOR INSERT TO app_worker_general
WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT INSERT (id, workspace_id, message_id, kind, bounce_type, bounce_code, source, detail, first_occurred_at, last_occurred_at, occurrence_count)
    ON public.message_events TO app_worker_general;

CREATE POLICY message_events_worker_general_update ON public.message_events
FOR UPDATE TO app_worker_general
USING (workspace_id = (SELECT public.app_current_workspace_id()))
WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT UPDATE (bounce_type, bounce_code, detail, last_occurred_at, occurrence_count)
    ON public.message_events TO app_worker_general;

-- The send path records the Message-ID every email carries (NULL -> value only,
-- enforced by messages_snapshot_guard).
GRANT UPDATE (rfc_message_id) ON public.messages TO app_worker_send;

DO $postflight$
BEGIN
    IF NOT pg_catalog.has_table_privilege('app_api', 'public.message_events', 'SELECT')
       OR NOT pg_catalog.has_column_privilege('app_api', 'public.message_events', 'message_id', 'INSERT')
       OR NOT pg_catalog.has_column_privilege('app_api', 'public.message_events', 'occurrence_count', 'UPDATE') THEN
        RAISE EXCEPTION '0030 postcondition failed: app_api cannot read/record message_events';
    END IF;
    IF NOT pg_catalog.has_column_privilege('app_worker_general', 'public.message_events', 'bounce_type', 'INSERT')
       OR NOT pg_catalog.has_column_privilege('app_worker_general', 'public.message_events', 'bounce_type', 'UPDATE') THEN
        RAISE EXCEPTION '0030 postcondition failed: app_worker_general cannot record bounces';
    END IF;
    IF NOT pg_catalog.has_column_privilege('app_worker_send', 'public.messages', 'rfc_message_id', 'UPDATE') THEN
        RAISE EXCEPTION '0030 postcondition failed: app_worker_send cannot record rfc_message_id';
    END IF;
    -- The public endpoint may never change what a row says happened, only count.
    IF pg_catalog.has_column_privilege('app_api', 'public.message_events', 'kind', 'UPDATE')
       OR pg_catalog.has_column_privilege('app_api', 'public.message_events', 'bounce_type', 'UPDATE')
       OR pg_catalog.has_table_privilege('app_api', 'public.message_events', 'DELETE') THEN
        RAISE EXCEPTION '0030 postcondition failed: app_api gained more than open counting';
    END IF;
    IF (SELECT pg_catalog.count(*) FROM pg_catalog.pg_policies
        WHERE schemaname = 'public' AND tablename = 'message_events') <> 7 THEN
        RAISE EXCEPTION '0030 postcondition failed: expected 7 message_events policies';
    END IF;
END;
$postflight$;

COMMIT;
