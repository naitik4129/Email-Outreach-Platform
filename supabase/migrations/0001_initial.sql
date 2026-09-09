-- Identity, tenancy and database security foundation. PREPARED ONLY: do not
-- apply without the separate migration review and environment authorization.
--
-- Sources: AGENTS.md; CLAUDE.md; docs/product/{PROJECT_CONTEXT,MVP,USER_ROLES,
-- PAGE_MAP}.md; docs/architecture/{SYSTEM_ARCHITECTURE,DOMAIN_MODEL}.md and
-- their campaign/message, provider, scheduler/worker/queue, rate, event, reply
-- and suppression specifications; docs/database/DATABASE.md;
-- docs/security/SECURITY_ARCHITECTURE.md; docs/adr/0003-supabase-migrations-only.md.
--
-- APPROVED FOR THIS MIGRATION by the project owner on 2026-09-09:
-- * Exactly one active OWNER membership per workspace; no second owner field.
-- * Only the Owner may manage other memberships; no self-service role changes.
-- * Future transfer demotes the old Owner to ADMIN, then promotes an existing
--   active member, atomically under the workspace lock and expected versions.
-- * OWNER/ADMIN may edit ordinary workspace name/defaults. All active members
--   may read their selected workspace's directory, excluding private preferences.
-- * PostgreSQL maintains updated_at/version; applications compare the expected
--   version but do not independently increment it.
-- * Bootstrap, invitation, membership/ownership mutations, restrictions and
--   deletion remain DENIED to runtime roles until audited commands are added.
--
-- USER_ROLES.md is the approved permission matrix; USER_FLOWS.md preserves
-- the original journeys. See docs/database/MIGRATION_REVIEW.md for scope,
-- runtime capability contracts and remaining backend milestones.
-- Historical Alembic recommendations are superseded by the migration-only ADR.
--
-- Deployment prerequisites: PostgreSQL >= 15, Supabase Auth and standard
-- anon/authenticated/service_role roles; trusted migration identity with role
-- management, public-schema creation and auth.users REFERENCES privileges.
-- This migration creates NO login, password, extension, seed, Auth signup
-- trigger, invitation, audit/idempotency store or feature-domain table.
-- Object/role name collisions fail loudly. No remote schema state was inspected.
--
-- Future API connection contract:
--   verified JWT -> BEGIN (READ COMMITTED) -> SET LOCAL ROLE app_api ->
--   set_config('app.user_id', verified_subject, true) ->
--   set_config('app.workspace_id', authorized_workspace, true) ->
--   current authorization + explicitly scoped SQL -> COMMIT/ROLLBACK.
-- Bind setting VALUES; never interpolate SQL. Each transaction must establish
-- fresh context; never use session-wide context on a pooled connection. Self
-- profile access and app_list_my_workspaces() require only the user context.
-- These GUCs are trusted backend input, not proof against arbitrary SQL execution
-- in a compromised backend. Browser roles cannot call these database interfaces.
-- Workers/internal operators need separate scoped service grants in later
-- migrations; neither a GUC string nor app_api confers system authority.
--
-- Future tenant children must reference (workspace_id,id) candidate keys; do not
-- introduce cross-tenant FKs using a child ID alone. Retention/erasure and all
-- workflow audit/idempotency remain backend responsibilities to implement later.

BEGIN;
SET LOCAL search_path = '';

DO $preflight$
BEGIN
    IF pg_catalog.current_setting('server_version_num')::integer < 150000 THEN
        RAISE EXCEPTION '0001 requires PostgreSQL 15 or newer';
    END IF;
    IF pg_catalog.to_regclass('auth.users') IS NULL THEN
        RAISE EXCEPTION '0001 requires existing Supabase Auth (auth.users)';
    END IF;
END;
$preflight$;

CREATE ROLE app_api WITH
    NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
CREATE ROLE app_foundation_reader WITH
    NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;

-- Temporary migration-only membership/CREATE allow ownership transfer without
-- assuming a true PostgreSQL superuser. Both are removed before COMMIT. Never
-- grant this read-helper owner role to an API, worker, browser or operator login.
GRANT app_foundation_reader TO CURRENT_USER;
GRANT USAGE ON SCHEMA public TO app_api, app_foundation_reader;
GRANT CREATE ON SCHEMA public TO app_foundation_reader;

CREATE ROLE app_connection WITH NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
GRANT USAGE ON SCHEMA public TO app_connection;
CREATE ROLE app_worker_general WITH NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
GRANT USAGE ON SCHEMA public TO app_worker_general;
CREATE ROLE app_worker_send WITH NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
GRANT USAGE ON SCHEMA public TO app_worker_send;
CREATE ROLE app_worker_sync WITH NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
GRANT USAGE ON SCHEMA public TO app_worker_sync;
CREATE ROLE app_scheduler WITH NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
GRANT USAGE ON SCHEMA public TO app_scheduler;
CREATE ROLE app_outbox_relay WITH NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
GRANT USAGE ON SCHEMA public TO app_outbox_relay;
CREATE ROLE app_rate_controller WITH NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
GRANT USAGE ON SCHEMA public TO app_rate_controller;
CREATE ROLE app_integrity_guard WITH NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
GRANT USAGE ON SCHEMA public TO app_integrity_guard;

CREATE TABLE public.profiles (
    id uuid CONSTRAINT profiles_pkey PRIMARY KEY,
    display_name text,
    preferences jsonb NOT NULL DEFAULT '{}'::jsonb,
    version bigint NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    CONSTRAINT profiles_auth_user_fkey FOREIGN KEY (id)
        REFERENCES auth.users (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT profiles_display_name_check CHECK (
        display_name IS NULL OR (
            pg_catalog.char_length(display_name) BETWEEN 1 AND 200
            AND display_name ~ '[^[:space:]]'
        )
    ),
    CONSTRAINT profiles_preferences_check CHECK (
        pg_catalog.jsonb_typeof(preferences) = 'object'
        AND pg_catalog.octet_length(pg_catalog.convert_to(preferences::text, 'UTF8')) <= 16384
    ),
    CONSTRAINT profiles_version_check CHECK (version > 0)
);

CREATE TABLE public.workspaces (
    id uuid DEFAULT pg_catalog.gen_random_uuid() CONSTRAINT workspaces_pkey PRIMARY KEY,
    name text NOT NULL,
    status text NOT NULL DEFAULT 'ACTIVE',
    sending_restriction_reason text,
    sending_restriction_version bigint NOT NULL DEFAULT 1,
    pending_safety_count bigint NOT NULL DEFAULT 0,
    defaults jsonb NOT NULL DEFAULT '{}'::jsonb,
    owner_policy_version bigint NOT NULL DEFAULT 1,
    version bigint NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    CONSTRAINT workspaces_name_check CHECK (
        pg_catalog.char_length(name) BETWEEN 1 AND 200 AND name ~ '[^[:space:]]'
    ),
    CONSTRAINT workspaces_status_check CHECK (status IN ('ACTIVE', 'RESTRICTED', 'DELETION_PENDING')),
    CONSTRAINT workspaces_restriction_reason_check CHECK (
        (sending_restriction_reason IS NULL OR sending_restriction_reason ~ '[^[:space:]]')
        AND (status <> 'RESTRICTED' OR sending_restriction_reason IS NOT NULL)
    ),
    CONSTRAINT workspaces_restriction_version_check CHECK (sending_restriction_version > 0),
    CONSTRAINT workspaces_pending_safety_count_check CHECK (pending_safety_count >= 0),
    CONSTRAINT workspaces_defaults_check CHECK (
        pg_catalog.jsonb_typeof(defaults) = 'object'
        AND pg_catalog.octet_length(pg_catalog.convert_to(defaults::text, 'UTF8')) <= 16384
    ),
    CONSTRAINT workspaces_owner_policy_version_check CHECK (owner_policy_version > 0),
    CONSTRAINT workspaces_version_check CHECK (version > 0)
);

CREATE TABLE public.workspace_memberships (
    id uuid DEFAULT pg_catalog.gen_random_uuid() CONSTRAINT workspace_memberships_pkey PRIMARY KEY,
    workspace_id uuid NOT NULL,
    user_id uuid NOT NULL,
    role_code text NOT NULL,
    status text NOT NULL DEFAULT 'ACTIVE',
    joined_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    revoked_at timestamptz,
    version bigint NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    CONSTRAINT workspace_memberships_workspace_fkey FOREIGN KEY (workspace_id)
        REFERENCES public.workspaces (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT workspace_memberships_user_fkey FOREIGN KEY (user_id)
        REFERENCES public.profiles (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT workspace_memberships_workspace_user_key UNIQUE (workspace_id, user_id),
    CONSTRAINT workspace_memberships_workspace_id_key UNIQUE (workspace_id, id),
    CONSTRAINT workspace_memberships_role_check CHECK (
        role_code IN ('OWNER', 'ADMIN', 'MANAGER', 'MEMBER', 'VIEWER')
    ),
    CONSTRAINT workspace_memberships_status_check CHECK (status IN ('ACTIVE', 'REVOKED')),
    CONSTRAINT workspace_memberships_revocation_check CHECK (
        (status = 'ACTIVE' AND revoked_at IS NULL)
        OR (status = 'REVOKED' AND revoked_at IS NOT NULL AND revoked_at >= joined_at)
    ),
    CONSTRAINT workspace_memberships_version_check CHECK (version > 0)
);

-- PK/UNIQUE indexes already cover workspace/user authorization and FK prefixes.
CREATE INDEX workspaces_status_id_idx ON public.workspaces (status, id);
CREATE INDEX workspace_memberships_user_status_workspace_idx
    ON public.workspace_memberships (user_id, status, workspace_id);
CREATE INDEX workspace_memberships_workspace_status_id_idx
    ON public.workspace_memberships (workspace_id, status, id);

-- Immediate uniqueness prevents concurrent introduction of a second Owner.
-- A future transfer must demote first, promote second, and commit together.
CREATE UNIQUE INDEX workspace_memberships_one_active_owner_idx
    ON public.workspace_memberships (workspace_id)
    WHERE role_code = 'OWNER' AND status = 'ACTIVE';

ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.profiles FORCE ROW LEVEL SECURITY;
ALTER TABLE public.workspaces ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.workspaces FORCE ROW LEVEL SECURITY;
ALTER TABLE public.workspace_memberships ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.workspace_memberships FORCE ROW LEVEL SECURITY;

-- Supabase installations may automatically grant new public objects to Data API
-- roles. Remove those grants explicitly; BYPASSRLS does not bypass table ACLs.
REVOKE ALL PRIVILEGES ON TABLE public.profiles, public.workspaces, public.workspace_memberships
    FROM PUBLIC, anon, authenticated, service_role;

-- These nonrecursive policies apply only to the isolated helper owner, which
-- has neither login nor mutation authority. FORCE RLS still applies. No API role
-- inherits this role; function outputs are the only runtime access to its reads.
CREATE POLICY profiles_foundation_reader_select ON public.profiles
    FOR SELECT TO app_foundation_reader USING (true);
CREATE POLICY workspaces_foundation_reader_select ON public.workspaces
    FOR SELECT TO app_foundation_reader USING (true);
CREATE POLICY workspace_memberships_foundation_reader_select ON public.workspace_memberships
    FOR SELECT TO app_foundation_reader USING (true);

GRANT SELECT (id, display_name) ON public.profiles TO app_foundation_reader;
GRANT SELECT (id, name, status) ON public.workspaces TO app_foundation_reader;
GRANT SELECT (id, workspace_id, user_id, role_code, status, version, joined_at, revoked_at)
    ON public.workspace_memberships TO app_foundation_reader;

CREATE FUNCTION public.app_current_user_id()
RETURNS uuid
LANGUAGE plpgsql STABLE SECURITY INVOKER
SET search_path = ''
AS $function$
BEGIN
    RETURN NULLIF(pg_catalog.current_setting('app.user_id', true), '')::uuid;
EXCEPTION
    WHEN invalid_text_representation THEN
        -- Do not disclose the invalid setting or treat it as authenticated.
        RETURN NULL;
END;
$function$;

CREATE FUNCTION public.app_current_workspace_id()
RETURNS uuid
LANGUAGE plpgsql STABLE SECURITY INVOKER
SET search_path = ''
AS $function$
BEGIN
    RETURN NULLIF(pg_catalog.current_setting('app.workspace_id', true), '')::uuid;
EXCEPTION
    WHEN invalid_text_representation THEN
        RETURN NULL;
END;
$function$;

CREATE FUNCTION public.app_current_workspace_role()
RETURNS text
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = ''
AS $function$
    SELECT membership.role_code
    FROM public.workspace_memberships AS membership
    WHERE membership.workspace_id = public.app_current_workspace_id()
      AND membership.user_id = public.app_current_user_id()
      AND membership.status = 'ACTIVE';
$function$;

-- Explicit, user-only exception to selected-workspace reads for the selector.
-- Return safe summaries, not defaults, restrictions, profiles or other users.
CREATE FUNCTION public.app_list_my_workspaces()
RETURNS TABLE (
    workspace_id uuid,
    workspace_name text,
    workspace_status text,
    membership_id uuid,
    role_code text,
    membership_version bigint
)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = ''
AS $function$
    SELECT workspace.id, workspace.name, workspace.status,
           membership.id, membership.role_code, membership.version
    FROM public.workspace_memberships AS membership
    JOIN public.workspaces AS workspace ON workspace.id = membership.workspace_id
    WHERE membership.user_id = public.app_current_user_id()
      AND membership.status = 'ACTIVE'
    ORDER BY workspace.id;
$function$;

CREATE FUNCTION public.app_list_workspace_members()
RETURNS TABLE (
    membership_id uuid,
    user_id uuid,
    display_name text,
    role_code text,
    status text,
    version bigint,
    joined_at timestamptz,
    revoked_at timestamptz
)
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = ''
AS $function$
    SELECT membership.id, membership.user_id, profile.display_name,
           membership.role_code, membership.status, membership.version,
           membership.joined_at, membership.revoked_at
    FROM public.workspace_memberships AS membership
    JOIN public.profiles AS profile ON profile.id = membership.user_id
    WHERE membership.workspace_id = public.app_current_workspace_id()
      AND public.app_current_workspace_role() IS NOT NULL
    ORDER BY membership.id;
$function$;

CREATE FUNCTION public.app_touch_row()
RETURNS trigger
LANGUAGE plpgsql VOLATILE SECURITY INVOKER
SET search_path = ''
AS $function$
BEGIN
    IF NEW.id IS DISTINCT FROM OLD.id OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514', MESSAGE = 'Row identity and creation time are immutable';
    END IF;
    NEW.updated_at := pg_catalog.statement_timestamp();
    NEW.version := OLD.version + 1;
    RETURN NEW;
END;
$function$;

CREATE FUNCTION public.app_guard_membership_identity()
RETURNS trigger
LANGUAGE plpgsql VOLATILE SECURITY INVOKER
SET search_path = ''
AS $function$
BEGIN
    IF NEW.workspace_id IS DISTINCT FROM OLD.workspace_id OR NEW.user_id IS DISTINCT FROM OLD.user_id THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514', MESSAGE = 'Membership workspace and user are immutable';
    END IF;
    RETURN NEW;
END;
$function$;

-- Check the final state, not the event's old role: bootstrap/transfer may have
-- temporarily zero Owners. Never condition trigger scheduling on a role query.
-- The unique active-owner index supplies the other half of this invariant.
-- Only a reviewed deletion workflow may remove the workspace itself; a removed
-- workspace needs no Owner. Runtime roles have no DELETE or TRUNCATE privileges.
-- Future owner mutations must lock workspace first, then memberships, recheck
-- current Owner/expected versions, and commit audit and command receipt together.
CREATE FUNCTION public.app_require_workspace_owner()
RETURNS trigger
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = ''
AS $function$
DECLARE
    affected_workspace_id uuid;
BEGIN
    IF TG_TABLE_NAME = 'workspaces' THEN
        affected_workspace_id := NEW.id;
    ELSIF TG_OP = 'DELETE' THEN
        affected_workspace_id := OLD.workspace_id;
    ELSE
        affected_workspace_id := NEW.workspace_id;
    END IF;

    IF EXISTS (
        SELECT 1 FROM public.workspaces AS workspace
        WHERE workspace.id = affected_workspace_id
    ) AND NOT EXISTS (
        SELECT 1 FROM public.workspace_memberships AS membership
        WHERE membership.workspace_id = affected_workspace_id
          AND membership.role_code = 'OWNER'
          AND membership.status = 'ACTIVE'
    ) THEN
        RAISE EXCEPTION USING
            ERRCODE = '23514',
            MESSAGE = 'Every workspace must retain one active Owner',
            SCHEMA = 'public', TABLE = 'workspaces',
            CONSTRAINT = 'workspaces_active_owner_required';
    END IF;
    RETURN NULL;
END;
$function$;

CREATE TRIGGER profiles_touch_row
    BEFORE UPDATE ON public.profiles
    FOR EACH ROW EXECUTE FUNCTION public.app_touch_row();
CREATE TRIGGER workspaces_touch_row
    BEFORE UPDATE ON public.workspaces
    FOR EACH ROW EXECUTE FUNCTION public.app_touch_row();
CREATE TRIGGER workspace_memberships_touch_row
    BEFORE UPDATE ON public.workspace_memberships
    FOR EACH ROW EXECUTE FUNCTION public.app_touch_row();
CREATE TRIGGER workspace_memberships_guard_identity
    BEFORE UPDATE ON public.workspace_memberships
    FOR EACH ROW EXECUTE FUNCTION public.app_guard_membership_identity();
CREATE CONSTRAINT TRIGGER workspaces_active_owner_required
    AFTER INSERT ON public.workspaces
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION public.app_require_workspace_owner();
CREATE CONSTRAINT TRIGGER workspace_memberships_active_owner_required
    AFTER INSERT OR UPDATE OR DELETE ON public.workspace_memberships
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION public.app_require_workspace_owner();

-- The migration identity still compiles policies/helpers below after ownership
-- transfer and PUBLIC revocation, so keep this validation grant temporary.
GRANT EXECUTE ON FUNCTION public.app_current_workspace_role() TO CURRENT_USER;

ALTER FUNCTION public.app_current_workspace_role() OWNER TO app_foundation_reader;
ALTER FUNCTION public.app_list_my_workspaces() OWNER TO app_foundation_reader;
ALTER FUNCTION public.app_list_workspace_members() OWNER TO app_foundation_reader;
ALTER FUNCTION public.app_require_workspace_owner() OWNER TO app_foundation_reader;

REVOKE ALL PRIVILEGES ON FUNCTION
    public.app_current_user_id(),
    public.app_current_workspace_id(),
    public.app_current_workspace_role(),
    public.app_list_my_workspaces(),
    public.app_list_workspace_members(),
    public.app_touch_row(),
    public.app_guard_membership_identity(),
    public.app_require_workspace_owner()
    FROM PUBLIC, anon, authenticated, service_role;

GRANT EXECUTE ON FUNCTION public.app_current_user_id(), public.app_current_workspace_id()
    TO app_api, app_foundation_reader;
GRANT EXECUTE ON FUNCTION public.app_current_workspace_role(),
    public.app_list_my_workspaces(), public.app_list_workspace_members()
    TO app_api;

CREATE POLICY profiles_api_select ON public.profiles
    FOR SELECT TO app_api
    USING (id = (SELECT public.app_current_user_id()));
CREATE POLICY profiles_api_insert ON public.profiles
    FOR INSERT TO app_api
    WITH CHECK (id = (SELECT public.app_current_user_id()));
CREATE POLICY profiles_api_update ON public.profiles
    FOR UPDATE TO app_api
    USING (id = (SELECT public.app_current_user_id()))
    WITH CHECK (id = (SELECT public.app_current_user_id()));

CREATE POLICY workspaces_api_select ON public.workspaces
    FOR SELECT TO app_api
    USING (
        id = (SELECT public.app_current_workspace_id())
        AND (SELECT public.app_current_workspace_role()) IS NOT NULL
    );
CREATE POLICY workspaces_api_update ON public.workspaces
    FOR UPDATE TO app_api
    USING (
        id = (SELECT public.app_current_workspace_id())
        AND (SELECT public.app_current_workspace_role()) IN ('OWNER', 'ADMIN')
    )
    WITH CHECK (
        id = (SELECT public.app_current_workspace_id())
        AND (SELECT public.app_current_workspace_role()) IN ('OWNER', 'ADMIN')
    );
CREATE POLICY workspace_memberships_api_select ON public.workspace_memberships
    FOR SELECT TO app_api
    USING (
        workspace_id = (SELECT public.app_current_workspace_id())
        AND (SELECT public.app_current_workspace_role()) IS NOT NULL
    );

-- Column grants keep safety fields, ownership, generated timestamps and versions
-- unwritable even when a row UPDATE policy allows an ordinary settings edit.
GRANT SELECT ON public.profiles, public.workspaces, public.workspace_memberships TO app_api;
GRANT INSERT (id, display_name, preferences) ON public.profiles TO app_api;
GRANT UPDATE (display_name, preferences) ON public.profiles TO app_api;
GRANT UPDATE (name, defaults) ON public.workspaces TO app_api;

COMMENT ON TABLE public.profiles IS
    'Supabase Auth-linked display identity. Full profile is self-only; teammate display names use the authorized directory function. Auth deletion is coordinated, never cascading.';
COMMENT ON TABLE public.workspaces IS
    'Tenant root and future sending-safety gate. Exactly one active OWNER membership is required at commit. Runtime bootstrap, safety mutation and deletion are deferred.';
COMMENT ON TABLE public.workspace_memberships IS
    'Retained workspace/user relationship. Revocation removes authorization; identities are immutable. Runtime membership and ownership mutations await audited commands.';
COMMENT ON COLUMN public.workspaces.defaults IS
    'Bounded ordinary preferences, validated by future backend schemas. Never an authorization source or an override of platform restrictions, safety gates or broader limits.';
COMMENT ON COLUMN public.profiles.preferences IS
    'Private self-owned display preferences. Never authentication, workspace permissions or secret material.';
COMMENT ON COLUMN public.workspaces.owner_policy_version IS
    'Approved ownership policy revision; 1 means exactly one active Owner via membership only. Not an alternate owner identity or a runtime-editable permission switch.';
COMMENT ON FUNCTION public.app_current_workspace_role() IS
    'Returns only the trusted current user active membership role in the selected workspace. Isolated reader owner and nonrecursive RLS; missing context returns NULL.';
COMMENT ON FUNCTION public.app_list_my_workspaces() IS
    'User-scoped workspace selector before workspace selection. Safe summaries only; current active memberships required.';
COMMENT ON FUNCTION public.app_list_workspace_members() IS
    'Selected-workspace directory for current active members, including retained revoked membership metadata. Never returns private profile preferences.';

REVOKE CREATE ON SCHEMA public FROM app_foundation_reader;

-- Fail against unsafe existing PUBLIC grants rather than silently changing
-- unrelated schemas/database privileges. Temporary schemas do not confer CREATE
-- SCHEMA, and empty function search paths prevent temporary-object shadowing.
DO $privilege_check$
DECLARE
    foundation_role text;
BEGIN
    FOREACH foundation_role IN ARRAY ARRAY['app_api', 'app_foundation_reader'] LOOP
        IF pg_catalog.has_database_privilege(foundation_role, pg_catalog.current_database(), 'CREATE')
           OR EXISTS (
               SELECT 1 FROM pg_catalog.pg_namespace AS namespace
               WHERE namespace.nspname NOT LIKE 'pg_temp_%'
                 AND namespace.nspname NOT LIKE 'pg_toast_temp_%'
                 AND pg_catalog.has_schema_privilege(foundation_role, namespace.oid, 'CREATE')
           ) THEN
            RAISE EXCEPTION '0001 requires foundation roles without effective database/schema CREATE privileges';
        END IF;
    END LOOP;
    IF pg_catalog.pg_has_role('app_api', 'app_foundation_reader', 'MEMBER') THEN
        RAISE EXCEPTION '0001 forbids API membership in the foundation reader role';
    END IF;
END;
$privilege_check$;

-- No existing rows are inserted, changed or removed. Failures roll back the
-- transaction; once applied to a shared environment, correct via forward
-- migrations, never rewrite this history or casually drop the foundation.
-- Closed, approved capability matrix. Current membership is read on every
-- statement; missing context, revoked membership and unknown actions deny.
CREATE FUNCTION public.app_has_permission(capability text)
RETURNS boolean LANGUAGE sql STABLE SECURITY INVOKER SET search_path = ''
AS $function$
    SELECT COALESCE(CASE capability
        WHEN 'product.read' THEN public.app_current_workspace_role() IS NOT NULL
        WHEN 'contacts.manage' THEN public.app_current_workspace_role() IN ('MEMBER','MANAGER','ADMIN','OWNER')
        WHEN 'templates.manage' THEN public.app_current_workspace_role() IN ('MEMBER','MANAGER','ADMIN','OWNER')
        WHEN 'campaigns.draft' THEN public.app_current_workspace_role() IN ('MEMBER','MANAGER','ADMIN','OWNER')
        WHEN 'campaigns.execute' THEN public.app_current_workspace_role() IN ('MANAGER','ADMIN','OWNER')
        WHEN 'mailboxes.manage' THEN public.app_current_workspace_role() IN ('MANAGER','ADMIN','OWNER')
        WHEN 'inbox.manage' THEN public.app_current_workspace_role() IN ('MANAGER','ADMIN','OWNER')
        WHEN 'suppression.add' THEN public.app_current_workspace_role() IN ('MEMBER','MANAGER','ADMIN','OWNER')
        WHEN 'suppression.release_manual' THEN public.app_current_workspace_role() IN ('ADMIN','OWNER')
        WHEN 'workspace.manage' THEN public.app_current_workspace_role() IN ('ADMIN','OWNER')
        WHEN 'audit.read' THEN public.app_current_workspace_role() IN ('ADMIN','OWNER')
        WHEN 'team.manage' THEN public.app_current_workspace_role() = 'OWNER'
        WHEN 'ownership.manage' THEN public.app_current_workspace_role() = 'OWNER'
        ELSE false END, false);
$function$;
REVOKE ALL ON FUNCTION public.app_has_permission(text) FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.app_has_permission(text), public.app_current_user_id(),
    public.app_current_workspace_id(), public.app_current_workspace_role() TO app_connection;
GRANT EXECUTE ON FUNCTION public.app_has_permission(text) TO app_api;
ALTER TABLE public.workspace_memberships ADD CONSTRAINT workspace_memberships_user_identity_key
    UNIQUE (workspace_id, user_id, id);

GRANT EXECUTE ON FUNCTION public.app_current_workspace_id() TO app_connection;

GRANT SELECT ON public.workspaces TO app_connection;
CREATE POLICY workspaces_app_connection_select ON public.workspaces FOR SELECT TO app_connection
USING (id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('mailboxes.manage'));

GRANT EXECUTE ON FUNCTION public.app_current_workspace_id() TO app_worker_general;

GRANT SELECT ON public.workspaces TO app_worker_general;
CREATE POLICY workspaces_app_worker_general_select ON public.workspaces FOR SELECT TO app_worker_general
USING (id = (SELECT public.app_current_workspace_id()) );

GRANT EXECUTE ON FUNCTION public.app_current_workspace_id() TO app_worker_send;

GRANT SELECT ON public.workspaces TO app_worker_send;
CREATE POLICY workspaces_app_worker_send_select ON public.workspaces FOR SELECT TO app_worker_send
USING (id = (SELECT public.app_current_workspace_id()) );

GRANT EXECUTE ON FUNCTION public.app_current_workspace_id() TO app_worker_sync;

GRANT SELECT ON public.workspaces TO app_worker_sync;
CREATE POLICY workspaces_app_worker_sync_select ON public.workspaces FOR SELECT TO app_worker_sync
USING (id = (SELECT public.app_current_workspace_id()) );

GRANT EXECUTE ON FUNCTION public.app_current_workspace_id() TO app_scheduler;

GRANT SELECT ON public.workspaces TO app_scheduler;
CREATE POLICY workspaces_app_scheduler_select ON public.workspaces FOR SELECT TO app_scheduler
USING (id = (SELECT public.app_current_workspace_id()) );

GRANT EXECUTE ON FUNCTION public.app_current_workspace_id() TO app_outbox_relay;

GRANT EXECUTE ON FUNCTION public.app_current_workspace_id() TO app_rate_controller;

GRANT EXECUTE ON FUNCTION public.app_current_workspace_id() TO app_integrity_guard;

GRANT SELECT (id, workspace_id, user_id, role_code, status) ON public.workspace_memberships TO app_worker_general;
CREATE POLICY workspace_memberships_notification_read ON public.workspace_memberships FOR SELECT TO app_worker_general
USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT (id, preferences) ON public.profiles TO app_worker_general;
CREATE POLICY profiles_notification_read ON public.profiles FOR SELECT TO app_worker_general
USING (EXISTS (SELECT 1 FROM public.workspace_memberships m WHERE m.workspace_id = public.app_current_workspace_id()
 AND m.user_id = profiles.id AND m.status = 'ACTIVE'));

CREATE POLICY workspace_memberships_integrity_guard_select ON public.workspace_memberships
FOR SELECT TO app_integrity_guard USING (true);
GRANT SELECT ON public.workspace_memberships TO app_integrity_guard;

-- Fail deployment if inherited PUBLIC capabilities undermine role isolation.
DO $runtime_privileges$
DECLARE runtime_role text;
BEGIN
    FOREACH runtime_role IN ARRAY ARRAY['app_connection','app_worker_general','app_worker_send',
      'app_worker_sync','app_scheduler','app_outbox_relay','app_rate_controller','app_integrity_guard'] LOOP
        IF pg_catalog.has_database_privilege(runtime_role, pg_catalog.current_database(), 'CREATE')
           OR EXISTS (SELECT 1 FROM pg_catalog.pg_namespace n
             WHERE n.nspname NOT LIKE 'pg_temp_%' AND n.nspname NOT LIKE 'pg_toast_temp_%'
               AND pg_catalog.has_schema_privilege(runtime_role,n.oid,'CREATE'))
           OR EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members m JOIN pg_catalog.pg_roles r ON r.oid=m.member WHERE r.rolname=runtime_role) THEN
            RAISE EXCEPTION 'Unsafe inherited capability for %', runtime_role;
        END IF;
    END LOOP;
END;
$runtime_privileges$;

GRANT EXECUTE ON FUNCTION public.app_current_user_id() TO app_integrity_guard;

GRANT SELECT ON public.workspaces TO app_integrity_guard;
CREATE POLICY workspaces_app_integrity_guard_additional_read ON public.workspaces FOR SELECT TO app_integrity_guard USING (true);

GRANT UPDATE (pending_safety_count) ON public.workspaces TO app_integrity_guard;
CREATE POLICY workspaces_integrity_counter ON public.workspaces FOR UPDATE TO app_integrity_guard USING (true) WITH CHECK (true);

REVOKE EXECUTE ON FUNCTION public.app_current_workspace_role() FROM CURRENT_USER;
REVOKE app_foundation_reader FROM CURRENT_USER;

COMMIT;
