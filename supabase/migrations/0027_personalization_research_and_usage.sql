-- Research cache and daily usage counters for hyper-personalization. Forward
-- migration per CLAUDE.md/AGENTS.md. See docs/adr/0012 and docs/adr/0013.
--
-- Migration review
--   Purpose:        (1) cache the text extracted from a lead's company website
--                   so the same site is not fetched repeatedly and a failed fetch
--                   is not retried in a loop; (2) count per-workspace daily LLM
--                   generations, previews and website fetches so environment-
--                   configured caps can be enforced atomically.
--   Creates:        public.personalization_research_cache and
--                   public.personalization_usage_daily.
--   Modifies:       nothing existing.
--   Deletes:        nothing.
--   Constraints:    cache: unique (workspace, source, url_hash); status/source
--                   allow-lists; extracted text <= 16000 chars and present only
--                   for status OK; expiry after fetch. usage: PK (workspace, day,
--                   kind), non-negative counters.
--   Indexes:        cache by (workspace_id, expires_at) for opportunistic purge.
--   RLS/security:   enabled and forced on both. The research cache is tenant
--                   scoped and is NEVER shared across workspaces; it is readable
--                   and writable only by app_worker_general (INSERT/UPDATE of
--                   named columns, DELETE of expired rows) -- app_api has no
--                   access, so fetched third-party text is not exposed through
--                   the API. Usage counters: app_worker_general SELECT/INSERT/
--                   UPDATE; app_api SELECT (product.read) so the UI can show
--                   remaining budget.
--   Existing data:  none touched (new tables).
--   App impact:     only the flag-gated personalization worker uses these tables.
--   Risk:           low; additive.
--   Rollback:       DROP TABLE public.personalization_usage_daily;
--                   DROP TABLE public.personalization_research_cache;
--
-- PREPARED ONLY. Do not apply to any shared/staging/production environment
-- without the project owner's separate review and explicit authorization.

BEGIN;
SET LOCAL search_path = '';

DO $preflight$
BEGIN
    IF pg_catalog.to_regclass('public.workspaces') IS NULL
       OR pg_catalog.to_regclass('public.campaign_sequences') IS NULL THEN
        RAISE EXCEPTION '0027 requires the earlier migrations to already be applied';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_attribute
        WHERE attrelid = 'public.campaigns'::pg_catalog.regclass
          AND attname = 'campaign_type' AND NOT attisdropped
    ) THEN
        RAISE EXCEPTION '0027 requires 0026_campaign_type_and_personalization_config.sql to already be applied';
    END IF;
    IF pg_catalog.to_regclass('public.personalization_research_cache') IS NOT NULL
       OR pg_catalog.to_regclass('public.personalization_usage_daily') IS NOT NULL THEN
        RAISE EXCEPTION '0027 already applied: a personalization table exists';
    END IF;
END;
$preflight$;

CREATE TABLE public.personalization_research_cache (
    id uuid DEFAULT pg_catalog.gen_random_uuid() CONSTRAINT personalization_research_cache_pkey PRIMARY KEY,
    workspace_id uuid NOT NULL,
    source text NOT NULL,
    url_hash text NOT NULL,
    normalized_url text NOT NULL,
    status text NOT NULL,
    extracted_text text,
    content_sha256 text,
    http_status integer,
    fetched_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    expires_at timestamptz NOT NULL,
    CONSTRAINT personalization_research_cache_workspace_fkey FOREIGN KEY (workspace_id)
        REFERENCES public.workspaces (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT personalization_research_cache_workspace_id_key UNIQUE (workspace_id, id),
    CONSTRAINT personalization_research_cache_identity_key UNIQUE (workspace_id, source, url_hash),
    CONSTRAINT personalization_research_cache_source_check CHECK (source IN ('WEBSITE')),
    CONSTRAINT personalization_research_cache_status_check CHECK (status IN ('OK', 'EMPTY', 'BLOCKED', 'ERROR')),
    CONSTRAINT personalization_research_cache_url_hash_check CHECK (url_hash ~ '^[0-9a-f]{64}$'),
    CONSTRAINT personalization_research_cache_url_check CHECK (
        pg_catalog.char_length(normalized_url) BETWEEN 1 AND 2048
        AND normalized_url !~ '[[:cntrl:]]'
    ),
    CONSTRAINT personalization_research_cache_text_check CHECK (
        (status = 'OK') = (extracted_text IS NOT NULL)
        AND (extracted_text IS NULL OR pg_catalog.char_length(extracted_text) BETWEEN 1 AND 16000)
    ),
    CONSTRAINT personalization_research_cache_sha_check CHECK (content_sha256 IS NULL OR content_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT personalization_research_cache_http_status_check CHECK (http_status IS NULL OR http_status BETWEEN 100 AND 599),
    CONSTRAINT personalization_research_cache_expiry_check CHECK (expires_at > fetched_at)
);

CREATE INDEX personalization_research_cache_expiry_idx
    ON public.personalization_research_cache (workspace_id, expires_at);

CREATE TABLE public.personalization_usage_daily (
    workspace_id uuid NOT NULL,
    usage_day date NOT NULL,
    kind text NOT NULL,
    units integer NOT NULL DEFAULT 0,
    input_tokens bigint NOT NULL DEFAULT 0,
    output_tokens bigint NOT NULL DEFAULT 0,
    cost_micros bigint NOT NULL DEFAULT 0,
    updated_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    CONSTRAINT personalization_usage_daily_pkey PRIMARY KEY (workspace_id, usage_day, kind),
    CONSTRAINT personalization_usage_daily_workspace_fkey FOREIGN KEY (workspace_id)
        REFERENCES public.workspaces (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT personalization_usage_daily_kind_check CHECK (kind IN ('GENERATION', 'PREVIEW', 'FETCH')),
    CONSTRAINT personalization_usage_daily_counters_check CHECK (
        units >= 0 AND input_tokens >= 0 AND output_tokens >= 0 AND cost_micros >= 0
    )
);

ALTER TABLE public.personalization_research_cache ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.personalization_research_cache FORCE ROW LEVEL SECURITY;
ALTER TABLE public.personalization_usage_daily ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.personalization_usage_daily FORCE ROW LEVEL SECURITY;

REVOKE ALL PRIVILEGES ON TABLE public.personalization_research_cache FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL PRIVILEGES ON TABLE public.personalization_usage_daily FROM PUBLIC, anon, authenticated, service_role;

-- Research cache: worker-only.
CREATE POLICY personalization_research_cache_worker_general_select ON public.personalization_research_cache
FOR SELECT TO app_worker_general USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.personalization_research_cache TO app_worker_general;

CREATE POLICY personalization_research_cache_worker_general_insert ON public.personalization_research_cache
FOR INSERT TO app_worker_general WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT INSERT (id, workspace_id, source, url_hash, normalized_url, status, extracted_text, content_sha256, http_status, fetched_at, expires_at) ON public.personalization_research_cache TO app_worker_general;

CREATE POLICY personalization_research_cache_worker_general_update ON public.personalization_research_cache
FOR UPDATE TO app_worker_general USING (workspace_id = (SELECT public.app_current_workspace_id())) WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT UPDATE (status, extracted_text, content_sha256, http_status, fetched_at, expires_at) ON public.personalization_research_cache TO app_worker_general;

-- Only expired rows may be removed (opportunistic purge inside the worker).
CREATE POLICY personalization_research_cache_worker_general_delete ON public.personalization_research_cache
FOR DELETE TO app_worker_general USING (
    workspace_id = (SELECT public.app_current_workspace_id())
    AND expires_at < pg_catalog.transaction_timestamp()
);
GRANT DELETE ON public.personalization_research_cache TO app_worker_general;

-- Usage counters.
CREATE POLICY personalization_usage_daily_api_select ON public.personalization_usage_daily
FOR SELECT TO app_api USING (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('product.read'));
GRANT SELECT ON public.personalization_usage_daily TO app_api;

CREATE POLICY personalization_usage_daily_worker_general_select ON public.personalization_usage_daily
FOR SELECT TO app_worker_general USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.personalization_usage_daily TO app_worker_general;

CREATE POLICY personalization_usage_daily_worker_general_insert ON public.personalization_usage_daily
FOR INSERT TO app_worker_general WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT INSERT (workspace_id, usage_day, kind, units, input_tokens, output_tokens, cost_micros, updated_at) ON public.personalization_usage_daily TO app_worker_general;

CREATE POLICY personalization_usage_daily_worker_general_update ON public.personalization_usage_daily
FOR UPDATE TO app_worker_general USING (workspace_id = (SELECT public.app_current_workspace_id())) WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT UPDATE (units, input_tokens, output_tokens, cost_micros, updated_at) ON public.personalization_usage_daily TO app_worker_general;

DO $postcondition$
BEGIN
    IF pg_catalog.to_regclass('public.personalization_research_cache') IS NULL
       OR pg_catalog.to_regclass('public.personalization_usage_daily') IS NULL THEN
        RAISE EXCEPTION '0027 postcondition failed: a table is missing';
    END IF;
    IF pg_catalog.has_table_privilege('app_api', 'public.personalization_research_cache', 'SELECT') THEN
        RAISE EXCEPTION '0027 postcondition failed: app_api must not read the research cache';
    END IF;
END;
$postcondition$;

COMMIT;
