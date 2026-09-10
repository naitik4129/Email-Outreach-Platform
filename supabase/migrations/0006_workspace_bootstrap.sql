-- Workspace bootstrap command. Forward migration per CLAUDE.md/AGENTS.md.
--
-- 0001_initial.sql intentionally leaves workspace_memberships/workspaces
-- INSERT denied to app_api: "Bootstrap, invitation, membership/ownership
-- mutations, restrictions and deletion remain DENIED to runtime roles until
-- audited commands are added." This migration adds exactly one narrowly
-- scoped SECURITY DEFINER command for that one deferred capability: create a
-- workspace and its initial ACTIVE OWNER membership atomically. It grants no
-- other membership mutation (invite, role change, revoke, transfer, removal)
-- -- those remain out of Phase 1 scope and stay DENIED to runtime roles.
--
-- PREPARED for the project owner's already-migrated development database
-- only. Do not apply to any shared/staging/production environment without a
-- separate review and explicit authorization.

BEGIN;
SET LOCAL search_path = '';

DO $preflight$
BEGIN
    IF pg_catalog.to_regclass('public.workspace_memberships') IS NULL THEN
        RAISE EXCEPTION '0006 requires 0001_initial.sql to already be applied';
    END IF;
    IF pg_catalog.to_regproc('public.app_current_user_id') IS NULL THEN
        RAISE EXCEPTION '0006 requires app_current_user_id() from 0001_initial.sql';
    END IF;
    IF pg_catalog.to_regclass('public.workspace_bootstrap_receipts') IS NOT NULL THEN
        RAISE EXCEPTION '0006 already applied: workspace_bootstrap_receipts exists';
    END IF;
END;
$preflight$;

-- Temporary migration-only elevation to own the new function, mirroring
-- 0001's own convention for app_foundation_reader; revoked again before
-- COMMIT. Never grant this role to an API, worker, browser or operator login.
-- WITH SET TRUE is required explicitly: PostgreSQL 16+ tracks INHERIT/SET/
-- ADMIN as independent grant options, and ALTER ... OWNER TO a role requires
-- the SET option specifically (holding ADMIN OPTION alone is not enough).
GRANT app_foundation_reader TO CURRENT_USER WITH INHERIT TRUE, SET TRUE;

-- ALTER ... OWNER TO also requires the *target* owner to itself hold CREATE
-- on the schema (PostgreSQL checks the new owner "could have created" the
-- object). 0001 permanently revokes this from app_foundation_reader at its
-- own COMMIT, so it must be re-granted here, transiently, exactly like 0001
-- does within its own transaction; revoked again below before COMMIT.
GRANT CREATE ON SCHEMA public TO app_foundation_reader;

CREATE TABLE public.workspace_bootstrap_receipts (
    actor_id uuid NOT NULL,
    request_key text NOT NULL,
    workspace_id uuid,
    created_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    completed_at timestamptz,
    CONSTRAINT workspace_bootstrap_receipts_pkey PRIMARY KEY (actor_id, request_key),
    CONSTRAINT workspace_bootstrap_receipts_actor_fkey FOREIGN KEY (actor_id)
        REFERENCES public.profiles (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT workspace_bootstrap_receipts_workspace_fkey FOREIGN KEY (workspace_id)
        REFERENCES public.workspaces (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT workspace_bootstrap_receipts_request_key_check CHECK (
        pg_catalog.char_length(request_key) BETWEEN 1 AND 200
    ),
    CONSTRAINT workspace_bootstrap_receipts_completion_check CHECK (
        (workspace_id IS NULL) = (completed_at IS NULL)
    )
);

COMMENT ON TABLE public.workspace_bootstrap_receipts IS
    'Idempotency ledger for app_bootstrap_workspace only. Not a general command receipt store (command_receipts requires a pre-existing workspace_id and active membership, neither of which exist yet at bootstrap time). No runtime role reads or writes this table directly; only the bootstrap function below does, via SECURITY DEFINER.';

ALTER TABLE public.workspace_bootstrap_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.workspace_bootstrap_receipts FORCE ROW LEVEL SECURITY;
REVOKE ALL PRIVILEGES ON TABLE public.workspace_bootstrap_receipts
    FROM PUBLIC, anon, authenticated, service_role;

CREATE POLICY workspace_bootstrap_receipts_foundation_reader ON public.workspace_bootstrap_receipts
    FOR ALL TO app_foundation_reader USING (true) WITH CHECK (true);
GRANT SELECT, INSERT, UPDATE ON public.workspace_bootstrap_receipts TO app_foundation_reader;

-- Additional narrow grants for the one function below only. app_api still has
-- no direct INSERT privilege on workspaces or workspace_memberships; every
-- existing 0001-0005 policy and grant is untouched.
GRANT INSERT (name) ON public.workspaces TO app_foundation_reader;
CREATE POLICY workspaces_foundation_reader_insert ON public.workspaces
    FOR INSERT TO app_foundation_reader WITH CHECK (true);

GRANT INSERT (workspace_id, user_id, role_code) ON public.workspace_memberships TO app_foundation_reader;
CREATE POLICY workspace_memberships_foundation_reader_insert ON public.workspace_memberships
    FOR INSERT TO app_foundation_reader WITH CHECK (true);

-- Atomically creates a workspace and its initial ACTIVE OWNER membership for
-- the calling user, exactly once per (actor, request_key). A concurrent or
-- retried call with the same key blocks briefly on the reserved receipt row
-- (FOR UPDATE) and then returns the original result instead of creating a
-- second workspace. The deferred owner-required constraint triggers from
-- 0001 still validate the final state at the caller's transaction commit.
CREATE FUNCTION public.app_bootstrap_workspace(p_name text, p_request_key text)
RETURNS TABLE (
    workspace_id uuid,
    membership_id uuid,
    role_code text,
    workspace_name text,
    workspace_status text,
    membership_version bigint,
    bootstrapped boolean
)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = ''
AS $function$
DECLARE
    actor uuid;
    reserved_workspace_id uuid;
    new_workspace_id uuid;
    new_membership_id uuid;
    new_membership_version bigint;
BEGIN
    actor := public.app_current_user_id();
    IF actor IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = '28000',
            MESSAGE = 'Workspace bootstrap requires an authenticated user context';
    END IF;
    IF p_request_key IS NULL OR pg_catalog.char_length(p_request_key) NOT BETWEEN 1 AND 200 THEN
        RAISE EXCEPTION USING ERRCODE = '22023',
            MESSAGE = 'A valid idempotency request key is required';
    END IF;
    IF p_name IS NULL OR pg_catalog.char_length(p_name) NOT BETWEEN 1 AND 200
        OR p_name !~ '[^[:space:]]' THEN
        RAISE EXCEPTION USING ERRCODE = '22023',
            MESSAGE = 'Workspace name must be 1-200 non-blank characters';
    END IF;

    BEGIN
        INSERT INTO public.workspace_bootstrap_receipts (actor_id, request_key)
        VALUES (actor, p_request_key);
    EXCEPTION WHEN unique_violation THEN
        NULL; -- another call already reserved this key; fall through and wait
    END;

    SELECT r.workspace_id INTO reserved_workspace_id
    FROM public.workspace_bootstrap_receipts AS r
    WHERE r.actor_id = actor AND r.request_key = p_request_key
    FOR UPDATE;

    IF reserved_workspace_id IS NOT NULL THEN
        RETURN QUERY
        SELECT w.id, m.id, m.role_code, w.name, w.status, m.version, false
        FROM public.workspaces AS w
        JOIN public.workspace_memberships AS m
            ON m.workspace_id = w.id AND m.user_id = actor AND m.role_code = 'OWNER'
        WHERE w.id = reserved_workspace_id;
        RETURN;
    END IF;

    INSERT INTO public.workspaces (name) VALUES (p_name)
        RETURNING id INTO new_workspace_id;
    INSERT INTO public.workspace_memberships (workspace_id, user_id, role_code)
        VALUES (new_workspace_id, actor, 'OWNER')
        RETURNING id, version INTO new_membership_id, new_membership_version;
    UPDATE public.workspace_bootstrap_receipts
        SET workspace_id = new_workspace_id, completed_at = pg_catalog.statement_timestamp()
        WHERE actor_id = actor AND request_key = p_request_key;

    RETURN QUERY
    SELECT new_workspace_id, new_membership_id, 'OWNER'::text, p_name, 'ACTIVE'::text,
        new_membership_version, true;
END;
$function$;

COMMENT ON FUNCTION public.app_bootstrap_workspace(text, text) IS
    'The only runtime path permitted to INSERT into workspaces/workspace_memberships. Creates exactly one workspace and its ACTIVE OWNER membership per (actor, request_key); concurrent or retried calls with the same key are serialized on the reservation row and return the original result. Grants no other membership mutation.';

ALTER FUNCTION public.app_bootstrap_workspace(text, text) OWNER TO app_foundation_reader;
REVOKE ALL ON FUNCTION public.app_bootstrap_workspace(text, text)
    FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.app_bootstrap_workspace(text, text) TO app_api;

REVOKE CREATE ON SCHEMA public FROM app_foundation_reader;
REVOKE app_foundation_reader FROM CURRENT_USER;

-- Fail rather than silently leave a lingering elevation, matching 0001's own
-- self-verification convention.
DO $postflight$
BEGIN
    IF pg_catalog.has_schema_privilege('app_foundation_reader', 'public', 'CREATE') THEN
        RAISE EXCEPTION '0006 must not leave app_foundation_reader with CREATE on schema public';
    END IF;
    IF pg_catalog.pg_has_role('app_api', 'app_foundation_reader', 'MEMBER') THEN
        RAISE EXCEPTION '0006 forbids API membership in the foundation reader role';
    END IF;
END;
$postflight$;

COMMIT;
