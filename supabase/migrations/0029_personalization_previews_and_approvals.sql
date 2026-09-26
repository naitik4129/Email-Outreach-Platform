-- Sample previews and launch approvals for hyper-personalized campaigns.
-- Forward migration per CLAUDE.md/AGENTS.md. See docs/adr/0011.
--
-- Migration review
--   Purpose:        let a user generate a few sample emails before launch, and
--                   record a manager's approval of them, bound to the exact
--                   objective + reference templates + prompt version + model that
--                   produced them.
--   Creates:        public.personalization_previews (short-lived samples) and
--                   public.campaign_personalization_approvals (append-only).
--   Modifies:       nothing existing.
--   Deletes:        nothing.
--   Constraints:    preview: unique (workspace, batch, audience member, step);
--                   state allow-list; OK previews carry sanitized subject/body
--                   within the same size bounds as messages; facts <= 4 KiB and
--                   research summary <= 2 KiB; expiry after creation. approval:
--                   64-hex config digest, unique per (workspace, campaign, digest).
--   Indexes:        previews by (workspace, campaign, batch) and by expiry.
--   RLS/security:   enabled and forced. app_api: SELECT (product.read); INSERT of
--                   PENDING previews (campaigns.draft, creator = caller, campaign
--                   must be DRAFT); INSERT of an approval (campaigns.execute,
--                   approver = caller, campaign must be DRAFT). No API UPDATE or
--                   DELETE on either table, so an approval is immutable.
--                   app_worker_general: previews SELECT, UPDATE of result
--                   columns, DELETE of expired rows only. Cross-tenant access
--                   fails on the workspace_id policy.
--   Existing data:  none touched (new tables).
--   App impact:     only the flag-gated personalization API/worker use these.
--   Risk:           low; additive. Preview rows contain lead-derived text, so they
--                   expire after 7 days (purged opportunistically by the worker).
--   Rollback:       DROP TABLE public.campaign_personalization_approvals;
--                   DROP TABLE public.personalization_previews;
--
-- PREPARED ONLY. Do not apply to any shared/staging/production environment
-- without the project owner's separate review and explicit authorization.

BEGIN;
SET LOCAL search_path = '';

DO $preflight$
BEGIN
    IF pg_catalog.to_regclass('public.campaign_audience_members') IS NULL
       OR pg_catalog.to_regclass('public.sequence_steps') IS NULL
       OR pg_catalog.to_regclass('public.leads') IS NULL THEN
        RAISE EXCEPTION '0029 requires 0002 and 0003 to already be applied';
    END IF;
    IF pg_catalog.to_regclass('public.message_generations') IS NULL THEN
        RAISE EXCEPTION '0029 requires 0028_message_generations.sql to already be applied';
    END IF;
    IF pg_catalog.to_regclass('public.personalization_previews') IS NOT NULL
       OR pg_catalog.to_regclass('public.campaign_personalization_approvals') IS NOT NULL THEN
        RAISE EXCEPTION '0029 already applied: a preview/approval table exists';
    END IF;
END;
$preflight$;

CREATE TABLE public.personalization_previews (
    id uuid DEFAULT pg_catalog.gen_random_uuid() CONSTRAINT personalization_previews_pkey PRIMARY KEY,
    workspace_id uuid NOT NULL,
    campaign_id uuid NOT NULL,
    batch_id uuid NOT NULL,
    audience_member_id uuid NOT NULL,
    lead_id uuid NOT NULL,
    step_id uuid NOT NULL,
    config_digest text NOT NULL,
    state text NOT NULL DEFAULT 'PENDING',
    subject text,
    body_html text,
    facts jsonb,
    research_summary jsonb,
    fallback_used boolean NOT NULL DEFAULT false,
    failure_codes text[],
    input_tokens integer,
    output_tokens integer,
    created_by uuid NOT NULL,
    created_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    expires_at timestamptz NOT NULL,
    completed_at timestamptz,
    CONSTRAINT personalization_previews_workspace_fkey FOREIGN KEY (workspace_id)
        REFERENCES public.workspaces (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT personalization_previews_campaign_fkey FOREIGN KEY (workspace_id, campaign_id)
        REFERENCES public.campaigns (workspace_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT personalization_previews_member_fkey FOREIGN KEY (workspace_id, audience_member_id)
        REFERENCES public.campaign_audience_members (workspace_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT personalization_previews_lead_fkey FOREIGN KEY (workspace_id, lead_id)
        REFERENCES public.leads (workspace_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT personalization_previews_step_fkey FOREIGN KEY (workspace_id, step_id)
        REFERENCES public.sequence_steps (workspace_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT personalization_previews_creator_fkey FOREIGN KEY (created_by)
        REFERENCES public.profiles (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT personalization_previews_workspace_id_key UNIQUE (workspace_id, id),
    CONSTRAINT personalization_previews_identity_key UNIQUE (workspace_id, batch_id, audience_member_id, step_id),
    CONSTRAINT personalization_previews_digest_check CHECK (config_digest ~ '^[0-9a-f]{64}$'),
    CONSTRAINT personalization_previews_state_check CHECK (state IN ('PENDING', 'OK', 'FAILED')),
    CONSTRAINT personalization_previews_result_check CHECK (
        (state = 'OK') = (subject IS NOT NULL AND body_html IS NOT NULL)
        AND (subject IS NULL OR pg_catalog.char_length(subject) BETWEEN 1 AND 500)
        AND (body_html IS NULL OR pg_catalog.char_length(body_html) <= 200000)
        AND (state = 'PENDING') = (completed_at IS NULL)
        AND (state <> 'FAILED' OR failure_codes IS NOT NULL)
    ),
    CONSTRAINT personalization_previews_facts_check CHECK (
        facts IS NULL
        OR (pg_catalog.jsonb_typeof(facts) = 'array'
            AND pg_catalog.octet_length(pg_catalog.convert_to(facts::text, 'UTF8')) <= 4096)
    ),
    CONSTRAINT personalization_previews_research_check CHECK (
        research_summary IS NULL
        OR (pg_catalog.jsonb_typeof(research_summary) = 'object'
            AND pg_catalog.octet_length(pg_catalog.convert_to(research_summary::text, 'UTF8')) <= 2048)
    ),
    CONSTRAINT personalization_previews_codes_check CHECK (
        failure_codes IS NULL OR pg_catalog.cardinality(failure_codes) <= 10
    ),
    CONSTRAINT personalization_previews_usage_check CHECK (
        (input_tokens IS NULL OR input_tokens >= 0) AND (output_tokens IS NULL OR output_tokens >= 0)
    ),
    CONSTRAINT personalization_previews_expiry_check CHECK (expires_at > created_at)
);

CREATE INDEX personalization_previews_batch_idx
    ON public.personalization_previews (workspace_id, campaign_id, batch_id);
CREATE INDEX personalization_previews_expiry_idx
    ON public.personalization_previews (workspace_id, expires_at);

CREATE TABLE public.campaign_personalization_approvals (
    id uuid DEFAULT pg_catalog.gen_random_uuid() CONSTRAINT campaign_personalization_approvals_pkey PRIMARY KEY,
    workspace_id uuid NOT NULL,
    campaign_id uuid NOT NULL,
    config_digest text NOT NULL,
    batch_id uuid NOT NULL,
    approved_by uuid NOT NULL,
    approved_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    CONSTRAINT campaign_personalization_approvals_workspace_fkey FOREIGN KEY (workspace_id)
        REFERENCES public.workspaces (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT campaign_personalization_approvals_campaign_fkey FOREIGN KEY (workspace_id, campaign_id)
        REFERENCES public.campaigns (workspace_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT campaign_personalization_approvals_approver_fkey FOREIGN KEY (approved_by)
        REFERENCES public.profiles (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT campaign_personalization_approvals_workspace_id_key UNIQUE (workspace_id, id),
    CONSTRAINT campaign_personalization_approvals_digest_key UNIQUE (workspace_id, campaign_id, config_digest),
    CONSTRAINT campaign_personalization_approvals_digest_check CHECK (config_digest ~ '^[0-9a-f]{64}$')
);

ALTER TABLE public.personalization_previews ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.personalization_previews FORCE ROW LEVEL SECURITY;
ALTER TABLE public.campaign_personalization_approvals ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.campaign_personalization_approvals FORCE ROW LEVEL SECURITY;

REVOKE ALL PRIVILEGES ON TABLE public.personalization_previews FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL PRIVILEGES ON TABLE public.campaign_personalization_approvals FROM PUBLIC, anon, authenticated, service_role;

CREATE POLICY personalization_previews_api_select ON public.personalization_previews
FOR SELECT TO app_api USING (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('product.read'));
GRANT SELECT ON public.personalization_previews TO app_api;

CREATE POLICY personalization_previews_api_insert ON public.personalization_previews
FOR INSERT TO app_api WITH CHECK (
    workspace_id = (SELECT public.app_current_workspace_id())
    AND public.app_has_permission('campaigns.draft')
    AND created_by = public.app_current_user_id()
    AND state = 'PENDING'
    AND EXISTS (
        SELECT 1 FROM public.campaigns c
        WHERE c.workspace_id = personalization_previews.workspace_id
          AND c.id = personalization_previews.campaign_id
          AND c.status = 'DRAFT'
    )
);
GRANT INSERT (id, workspace_id, campaign_id, batch_id, audience_member_id, lead_id, step_id, config_digest, created_by, expires_at) ON public.personalization_previews TO app_api;

CREATE POLICY personalization_previews_worker_general_select ON public.personalization_previews
FOR SELECT TO app_worker_general USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.personalization_previews TO app_worker_general;

CREATE POLICY personalization_previews_worker_general_update ON public.personalization_previews
FOR UPDATE TO app_worker_general USING (workspace_id = (SELECT public.app_current_workspace_id())) WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT UPDATE (state, subject, body_html, facts, research_summary, fallback_used, failure_codes, input_tokens, output_tokens, completed_at) ON public.personalization_previews TO app_worker_general;

CREATE POLICY personalization_previews_worker_general_delete ON public.personalization_previews
FOR DELETE TO app_worker_general USING (
    workspace_id = (SELECT public.app_current_workspace_id())
    AND expires_at < pg_catalog.transaction_timestamp()
);
GRANT DELETE ON public.personalization_previews TO app_worker_general;

CREATE POLICY campaign_personalization_approvals_api_select ON public.campaign_personalization_approvals
FOR SELECT TO app_api USING (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('product.read'));
GRANT SELECT ON public.campaign_personalization_approvals TO app_api;

CREATE POLICY campaign_personalization_approvals_api_insert ON public.campaign_personalization_approvals
FOR INSERT TO app_api WITH CHECK (
    workspace_id = (SELECT public.app_current_workspace_id())
    AND public.app_has_permission('campaigns.execute')
    AND approved_by = public.app_current_user_id()
    AND EXISTS (
        SELECT 1 FROM public.campaigns c
        WHERE c.workspace_id = campaign_personalization_approvals.workspace_id
          AND c.id = campaign_personalization_approvals.campaign_id
          AND c.status = 'DRAFT'
    )
);
GRANT INSERT (id, workspace_id, campaign_id, config_digest, batch_id, approved_by) ON public.campaign_personalization_approvals TO app_api;

DO $postcondition$
BEGIN
    IF pg_catalog.to_regclass('public.personalization_previews') IS NULL
       OR pg_catalog.to_regclass('public.campaign_personalization_approvals') IS NULL THEN
        RAISE EXCEPTION '0029 postcondition failed: a table is missing';
    END IF;
    IF pg_catalog.has_table_privilege('app_api', 'public.campaign_personalization_approvals', 'UPDATE')
       OR pg_catalog.has_table_privilege('app_api', 'public.campaign_personalization_approvals', 'DELETE')
       OR pg_catalog.has_table_privilege('app_worker_general', 'public.campaign_personalization_approvals', 'INSERT') THEN
        RAISE EXCEPTION '0029 postcondition failed: approvals must be append-only and API-written';
    END IF;
END;
$postcondition$;

COMMIT;
