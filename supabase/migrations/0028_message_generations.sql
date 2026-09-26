-- Just-in-time generation jobs for hyper-personalized messages. Forward
-- migration per CLAUDE.md/AGENTS.md. See docs/adr/0011 and docs/adr/0012.
--
-- Migration review
--   Purpose:        give each hyper-personalized message a durable generation
--                   job (lease, bounded attempt counters, provenance, the facts
--                   used) and an append-only attempt ledger, without adding any
--                   state to messages (which stays PLANNED until a validated
--                   snapshot is written).
--   Creates:        public.message_generations, public.message_generation_attempts
--                   and the guard function app_guard_generation_attempt().
--   Modifies:       nothing existing.
--   Deletes:        nothing.
--   Constraints:    one generation per message (unique workspace+message);
--                   state and outcome allow-lists; attempt_count <= max_attempts;
--                   lease columns set together and only while PENDING; a FAILED
--                   generation carries a failure code; facts <= 4 KiB, angle
--                   <= 240 chars; failure codes are short lowercase tokens.
--                   Attempt rows carry NO message content and NO lead data.
--   Indexes:        partial (next_attempt_at, id) WHERE state = 'PENDING' for the
--                   scheduler sweep; (workspace, campaign, state) for progress.
--   RLS/security:   enabled and forced. app_worker_general: SELECT/INSERT and
--                   UPDATE of named columns (it never gets DELETE). app_api:
--                   SELECT (product.read) so progress can be shown. app_scheduler:
--                   read-only cross-tenant SELECT of message_generations ONLY
--                   (same narrow discovery pattern as 0011); every claim and
--                   mutation stays workspace-scoped. app_worker_send has no
--                   access. A trigger makes an attempt immutable once it leaves
--                   RESERVED.
--   Existing data:  none touched (new tables).
--   App impact:     only the flag-gated personalization pipeline uses them; apply
--                   before enabling PERSONALIZATION_ENABLED.
--   Risk:           low; additive. The scheduler discovery policy widens
--                   scheduler reads to this one table (IDs and timestamps only).
--   Rollback:       DROP TABLE public.message_generation_attempts;
--                   DROP TABLE public.message_generations;
--                   DROP FUNCTION public.app_guard_generation_attempt();
--
-- PREPARED ONLY. Do not apply to any shared/staging/production environment
-- without the project owner's separate review and explicit authorization.

BEGIN;
SET LOCAL search_path = '';

DO $preflight$
BEGIN
    IF pg_catalog.to_regclass('public.messages') IS NULL
       OR pg_catalog.to_regclass('public.campaign_enrollments') IS NULL THEN
        RAISE EXCEPTION '0028 requires 0003 and 0004 to already be applied';
    END IF;
    IF pg_catalog.to_regclass('public.personalization_usage_daily') IS NULL THEN
        RAISE EXCEPTION '0028 requires 0027_personalization_research_and_usage.sql to already be applied';
    END IF;
    IF pg_catalog.to_regclass('public.message_generations') IS NOT NULL
       OR pg_catalog.to_regclass('public.message_generation_attempts') IS NOT NULL THEN
        RAISE EXCEPTION '0028 already applied: a generation table exists';
    END IF;
END;
$preflight$;

CREATE TABLE public.message_generations (
    id uuid DEFAULT pg_catalog.gen_random_uuid() CONSTRAINT message_generations_pkey PRIMARY KEY,
    workspace_id uuid NOT NULL,
    campaign_id uuid NOT NULL,
    sequence_id uuid NOT NULL,
    step_id uuid NOT NULL,
    enrollment_id uuid NOT NULL,
    message_id uuid NOT NULL,
    state text NOT NULL DEFAULT 'PENDING',
    attempt_count integer NOT NULL DEFAULT 0,
    max_attempts integer NOT NULL,
    transient_error_count integer NOT NULL DEFAULT 0,
    next_attempt_at timestamptz NOT NULL,
    lease_owner text,
    lease_expires_at timestamptz,
    failure_code text,
    last_failure_codes text[],
    provider text,
    model text,
    prompt_version text,
    context_digest text,
    fallback_used boolean NOT NULL DEFAULT false,
    personalization_facts jsonb,
    angle text,
    research_status text,
    created_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    completed_at timestamptz,
    CONSTRAINT message_generations_workspace_fkey FOREIGN KEY (workspace_id)
        REFERENCES public.workspaces (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT message_generations_message_fkey FOREIGN KEY (workspace_id, message_id)
        REFERENCES public.messages (workspace_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT message_generations_enrollment_fkey FOREIGN KEY (workspace_id, campaign_id, sequence_id, enrollment_id)
        REFERENCES public.campaign_enrollments (workspace_id, campaign_id, sequence_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT message_generations_step_fkey FOREIGN KEY (workspace_id, sequence_id, step_id)
        REFERENCES public.sequence_steps (workspace_id, sequence_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT message_generations_workspace_id_key UNIQUE (workspace_id, id),
    CONSTRAINT message_generations_message_key UNIQUE (workspace_id, message_id),
    CONSTRAINT message_generations_state_check CHECK (state IN ('PENDING', 'SUCCEEDED', 'FAILED', 'SUPERSEDED')),
    CONSTRAINT message_generations_attempts_check CHECK (
        max_attempts BETWEEN 1 AND 10
        AND attempt_count >= 0
        AND attempt_count <= max_attempts
        AND transient_error_count >= 0
    ),
    CONSTRAINT message_generations_lease_check CHECK (
        (lease_owner IS NULL) = (lease_expires_at IS NULL)
        AND (lease_owner IS NULL OR state = 'PENDING')
        AND (lease_owner IS NULL OR pg_catalog.char_length(lease_owner) BETWEEN 1 AND 200)
    ),
    CONSTRAINT message_generations_failure_check CHECK (
        (state <> 'FAILED' OR failure_code IS NOT NULL)
        AND (failure_code IS NULL OR failure_code ~ '^[a-z0-9_]{1,64}$')
        AND (last_failure_codes IS NULL OR pg_catalog.cardinality(last_failure_codes) <= 10)
    ),
    CONSTRAINT message_generations_completion_check CHECK (
        (state = 'PENDING') = (completed_at IS NULL)
    ),
    CONSTRAINT message_generations_provenance_check CHECK (
        (provider IS NULL OR pg_catalog.char_length(provider) <= 64)
        AND (model IS NULL OR pg_catalog.char_length(model) <= 128)
        AND (prompt_version IS NULL OR pg_catalog.char_length(prompt_version) <= 64)
        AND (context_digest IS NULL OR context_digest ~ '^[0-9a-f]{64}$')
    ),
    CONSTRAINT message_generations_facts_check CHECK (
        personalization_facts IS NULL
        OR (
            pg_catalog.jsonb_typeof(personalization_facts) = 'array'
            AND pg_catalog.octet_length(pg_catalog.convert_to(personalization_facts::text, 'UTF8')) <= 4096
        )
    ),
    CONSTRAINT message_generations_angle_check CHECK (angle IS NULL OR pg_catalog.char_length(angle) <= 240),
    CONSTRAINT message_generations_research_status_check CHECK (
        research_status IS NULL OR research_status IN ('NONE', 'OK', 'EMPTY', 'BLOCKED', 'ERROR', 'SKIPPED')
    )
);

CREATE INDEX message_generations_pending_idx
    ON public.message_generations (next_attempt_at, id)
    WHERE state = 'PENDING';
CREATE INDEX message_generations_campaign_state_idx
    ON public.message_generations (workspace_id, campaign_id, state);

CREATE TABLE public.message_generation_attempts (
    id uuid DEFAULT pg_catalog.gen_random_uuid() CONSTRAINT message_generation_attempts_pkey PRIMARY KEY,
    workspace_id uuid NOT NULL,
    generation_id uuid NOT NULL,
    attempt_no integer NOT NULL,
    outcome text NOT NULL DEFAULT 'RESERVED',
    failure_codes text[],
    model text,
    prompt_version text,
    input_tokens integer,
    output_tokens integer,
    cost_micros bigint,
    latency_ms integer,
    context_digest text,
    output_digest text,
    started_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    finished_at timestamptz,
    CONSTRAINT message_generation_attempts_workspace_fkey FOREIGN KEY (workspace_id)
        REFERENCES public.workspaces (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT message_generation_attempts_generation_fkey FOREIGN KEY (workspace_id, generation_id)
        REFERENCES public.message_generations (workspace_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT message_generation_attempts_number_key UNIQUE (workspace_id, generation_id, attempt_no),
    CONSTRAINT message_generation_attempts_no_check CHECK (attempt_no >= 1),
    CONSTRAINT message_generation_attempts_outcome_check CHECK (outcome IN (
        'RESERVED', 'ACCEPTED', 'FALLBACK', 'REJECTED_VALIDATION', 'PROVIDER_ERROR',
        'TIMEOUT', 'MALFORMED_OUTPUT', 'ABANDONED', 'ABORTED_INELIGIBLE'
    )),
    CONSTRAINT message_generation_attempts_codes_check CHECK (
        failure_codes IS NULL OR pg_catalog.cardinality(failure_codes) <= 10
    ),
    CONSTRAINT message_generation_attempts_usage_check CHECK (
        (input_tokens IS NULL OR input_tokens >= 0)
        AND (output_tokens IS NULL OR output_tokens >= 0)
        AND (cost_micros IS NULL OR cost_micros >= 0)
        AND (latency_ms IS NULL OR latency_ms >= 0)
    ),
    CONSTRAINT message_generation_attempts_digest_check CHECK (
        (context_digest IS NULL OR context_digest ~ '^[0-9a-f]{64}$')
        AND (output_digest IS NULL OR output_digest ~ '^[0-9a-f]{64}$')
    ),
    CONSTRAINT message_generation_attempts_finish_check CHECK (
        (outcome = 'RESERVED') = (finished_at IS NULL)
    )
);

-- An attempt is a ledger entry: it may leave RESERVED exactly once and is
-- immutable afterwards. SECURITY INVOKER like app_guard_message_snapshot().
CREATE FUNCTION public.app_guard_generation_attempt() RETURNS trigger
LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path = ''
AS $function$
BEGIN
    IF OLD.outcome <> 'RESERVED' THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'A finished generation attempt is immutable';
    END IF;
    IF NEW.workspace_id <> OLD.workspace_id OR NEW.generation_id <> OLD.generation_id
       OR NEW.attempt_no <> OLD.attempt_no OR NEW.started_at <> OLD.started_at THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Generation attempt identity is immutable';
    END IF;
    RETURN NEW;
END;
$function$;
REVOKE ALL ON FUNCTION public.app_guard_generation_attempt() FROM PUBLIC, anon, authenticated, service_role;

CREATE TRIGGER message_generation_attempts_guard
    BEFORE UPDATE ON public.message_generation_attempts
    FOR EACH ROW EXECUTE FUNCTION public.app_guard_generation_attempt();

ALTER TABLE public.message_generations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.message_generations FORCE ROW LEVEL SECURITY;
ALTER TABLE public.message_generation_attempts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.message_generation_attempts FORCE ROW LEVEL SECURITY;

REVOKE ALL PRIVILEGES ON TABLE public.message_generations FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL PRIVILEGES ON TABLE public.message_generation_attempts FROM PUBLIC, anon, authenticated, service_role;

CREATE POLICY message_generations_api_select ON public.message_generations
FOR SELECT TO app_api USING (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('product.read'));
GRANT SELECT ON public.message_generations TO app_api;

CREATE POLICY message_generations_worker_general_select ON public.message_generations
FOR SELECT TO app_worker_general USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.message_generations TO app_worker_general;

CREATE POLICY message_generations_worker_general_insert ON public.message_generations
FOR INSERT TO app_worker_general WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT INSERT (id, workspace_id, campaign_id, sequence_id, step_id, enrollment_id, message_id, state, max_attempts, next_attempt_at) ON public.message_generations TO app_worker_general;

CREATE POLICY message_generations_worker_general_update ON public.message_generations
FOR UPDATE TO app_worker_general USING (workspace_id = (SELECT public.app_current_workspace_id())) WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT UPDATE (state, attempt_count, transient_error_count, next_attempt_at, lease_owner, lease_expires_at, failure_code, last_failure_codes, provider, model, prompt_version, context_digest, fallback_used, personalization_facts, angle, research_status, updated_at, completed_at) ON public.message_generations TO app_worker_general;

-- Cross-tenant, read-only discovery for the scheduler sweep (mirrors 0011).
CREATE POLICY message_generations_scheduler_discovery_select ON public.message_generations
FOR SELECT TO app_scheduler USING (true);
GRANT SELECT ON public.message_generations TO app_scheduler;

CREATE POLICY message_generation_attempts_worker_general_select ON public.message_generation_attempts
FOR SELECT TO app_worker_general USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.message_generation_attempts TO app_worker_general;

CREATE POLICY message_generation_attempts_worker_general_insert ON public.message_generation_attempts
FOR INSERT TO app_worker_general WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT INSERT (id, workspace_id, generation_id, attempt_no, outcome, started_at) ON public.message_generation_attempts TO app_worker_general;

CREATE POLICY message_generation_attempts_worker_general_update ON public.message_generation_attempts
FOR UPDATE TO app_worker_general USING (workspace_id = (SELECT public.app_current_workspace_id())) WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT UPDATE (outcome, failure_codes, model, prompt_version, input_tokens, output_tokens, cost_micros, latency_ms, context_digest, output_digest, finished_at) ON public.message_generation_attempts TO app_worker_general;

DO $postcondition$
BEGIN
    IF pg_catalog.to_regclass('public.message_generations') IS NULL
       OR pg_catalog.to_regclass('public.message_generation_attempts') IS NULL THEN
        RAISE EXCEPTION '0028 postcondition failed: a table is missing';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_trigger
        WHERE tgrelid = 'public.message_generation_attempts'::pg_catalog.regclass
          AND tgname = 'message_generation_attempts_guard'
    ) THEN
        RAISE EXCEPTION '0028 postcondition failed: attempt guard trigger missing';
    END IF;
    IF pg_catalog.has_table_privilege('app_worker_send', 'public.message_generations', 'SELECT') THEN
        RAISE EXCEPTION '0028 postcondition failed: app_worker_send must not access generations';
    END IF;
END;
$postcondition$;

COMMIT;
