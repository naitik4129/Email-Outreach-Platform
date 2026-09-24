-- 0018_team_notifications_admin.sql
--
-- Audited commands for:
-- 1. Team management: invitation creation, revocation, acceptance, role change, member removal, and atomic ownership transfer.
-- 2. In-app notifications: creation helper and user profile preference updates.
-- 3. Platform operator controls: workspace restriction/restoration and platform audit logging.
--
-- Follows repository security architecture (docs/security/SECURITY_ARCHITECTURE.md),
-- user roles permissions matrix (docs/product/USER_ROLES.md), and workspace bootstrap
-- precedent (0006_workspace_bootstrap.sql).
--
-- PREPARED ONLY: do not apply without the separate migration review and environment authorization.

BEGIN;
SET LOCAL search_path = '';

DO $preflight$
BEGIN
    IF pg_catalog.to_regclass('public.workspace_memberships') IS NULL THEN
        RAISE EXCEPTION '0018 requires 0001_initial.sql to already be applied';
    END IF;
    IF pg_catalog.to_regclass('public.workspace_invitations') IS NULL THEN
        RAISE EXCEPTION '0018 requires 0002_contacts_content.sql to already be applied';
    END IF;
    IF pg_catalog.to_regclass('public.audit_events') IS NULL THEN
        RAISE EXCEPTION '0018 requires 0005_operations_platform.sql to already be applied';
    END IF;
END;
$preflight$;

-- Temporary migration elevation mirroring 0001 and 0006 conventions.
GRANT app_foundation_reader TO CURRENT_USER WITH INHERIT TRUE, SET TRUE;
GRANT CREATE ON SCHEMA public TO app_foundation_reader;

-- ---------------------------------------------------------------------------
-- 1. Invitation & Team Audited Commands
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.app_create_workspace_invitation(
    p_invited_email text,
    p_role_code text,
    p_token_digest text,
    p_expires_at timestamptz
)
RETURNS TABLE (
    invitation_id uuid,
    workspace_id uuid,
    inviter_id uuid,
    invited_email text,
    role_code text,
    status text,
    expires_at timestamptz,
    created_at timestamptz
)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = ''
AS $function$
DECLARE
    actor uuid;
    ws_id uuid;
    clean_email text;
    new_id uuid;
    new_created_at timestamptz;
BEGIN
    actor := public.app_current_user_id();
    ws_id := public.app_current_workspace_id();

    IF actor IS NULL OR ws_id IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = '28000',
            MESSAGE = 'Authentication and active workspace context required';
    END IF;

    IF NOT public.app_has_permission('team.manage') THEN
        RAISE EXCEPTION USING ERRCODE = '42501',
            MESSAGE = 'Only workspace owners may manage team invitations';
    END IF;

    IF p_role_code NOT IN ('ADMIN', 'MANAGER', 'MEMBER', 'VIEWER') THEN
        RAISE EXCEPTION USING ERRCODE = '22023',
            MESSAGE = 'Invitations cannot grant the OWNER role';
    END IF;

    clean_email := pg_catalog.lower(pg_catalog.trim(p_invited_email));
    IF clean_email IS NULL OR pg_catalog.char_length(clean_email) NOT BETWEEN 3 AND 320 OR clean_email !~ '@' THEN
        RAISE EXCEPTION USING ERRCODE = '22023',
            MESSAGE = 'A valid email address is required';
    END IF;

    -- Check if invitee is already an active member of this workspace
    IF EXISTS (
        SELECT 1
        FROM public.workspace_memberships AS m
        JOIN auth.users AS u ON u.id = m.user_id
        WHERE m.workspace_id = ws_id
          AND pg_catalog.lower(u.email) = clean_email
          AND m.status = 'ACTIVE'
    ) THEN
        RAISE EXCEPTION USING ERRCODE = '23505',
            MESSAGE = 'User is already an active member of this workspace';
    END IF;

    -- If a pending invite exists for this email, revoke it first (resend replaces token)
    UPDATE public.workspace_invitations
    SET status = 'REVOKED', revoked_at = pg_catalog.statement_timestamp()
    WHERE workspace_id = ws_id
      AND invited_email = clean_email
      AND status = 'PENDING';

    INSERT INTO public.workspace_invitations (
        workspace_id,
        inviter_id,
        invited_email,
        role_code,
        token_digest,
        status,
        expires_at
    ) VALUES (
        ws_id,
        actor,
        clean_email,
        p_role_code,
        p_token_digest,
        'PENDING',
        p_expires_at
    )
    RETURNING id, created_at INTO new_id, new_created_at;

    -- Durable tenant audit event
    INSERT INTO public.audit_events (
        workspace_id,
        actor_kind,
        actor_id,
        action,
        target_type,
        target_id,
        after_state
    ) VALUES (
        ws_id,
        'USER',
        actor,
        'invitation.created',
        'workspace_invitation',
        new_id,
        pg_catalog.json_build_object(
            'invited_email', clean_email,
            'role_code', p_role_code,
            'expires_at', p_expires_at
        )::jsonb
    );

    RETURN QUERY
    SELECT new_id, ws_id, actor, clean_email, p_role_code, 'PENDING'::text, p_expires_at, new_created_at;
END;
$function$;

CREATE OR REPLACE FUNCTION public.app_revoke_workspace_invitation(
    p_invitation_id uuid
)
RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = ''
AS $function$
DECLARE
    actor uuid;
    ws_id uuid;
    invite_row record;
BEGIN
    actor := public.app_current_user_id();
    ws_id := public.app_current_workspace_id();

    IF actor IS NULL OR ws_id IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = '28000',
            MESSAGE = 'Authentication and active workspace context required';
    END IF;

    IF NOT public.app_has_permission('team.manage') THEN
        RAISE EXCEPTION USING ERRCODE = '42501',
            MESSAGE = 'Only workspace owners may revoke team invitations';
    END IF;

    SELECT id, invited_email, role_code INTO invite_row
    FROM public.workspace_invitations
    WHERE id = p_invitation_id AND workspace_id = ws_id AND status = 'PENDING'
    FOR UPDATE;

    IF invite_row.id IS NULL THEN
        RETURN false;
    END IF;

    UPDATE public.workspace_invitations
    SET status = 'REVOKED', revoked_at = pg_catalog.statement_timestamp()
    WHERE id = p_invitation_id;

    INSERT INTO public.audit_events (
        workspace_id,
        actor_kind,
        actor_id,
        action,
        target_type,
        target_id,
        before_state
    ) VALUES (
        ws_id,
        'USER',
        actor,
        'invitation.revoked',
        'workspace_invitation',
        p_invitation_id,
        pg_catalog.json_build_object(
            'invited_email', invite_row.invited_email,
            'role_code', invite_row.role_code
        )::jsonb
    );

    RETURN true;
END;
$function$;

CREATE OR REPLACE FUNCTION public.app_accept_workspace_invitation(
    p_token_digest text
)
RETURNS TABLE (
    workspace_id uuid,
    membership_id uuid,
    role_code text,
    workspace_name text
)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = ''
AS $function$
DECLARE
    actor uuid;
    actor_email text;
    inv public.workspace_invitations%ROWTYPE;
    target_ws public.workspaces%ROWTYPE;
    existing_mem public.workspace_memberships%ROWTYPE;
    res_mem_id uuid;
    res_role text;
BEGIN
    actor := public.app_current_user_id();
    IF actor IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = '28000',
            MESSAGE = 'Accepting an invitation requires an authenticated session';
    END IF;

    -- Resolve caller's email safely from auth.users
    SELECT email INTO actor_email
    FROM auth.users
    WHERE id = actor;

    IF actor_email IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = '28000',
            MESSAGE = 'Authenticated user has no verified email address';
    END IF;

    -- Look up invitation by digest
    SELECT * INTO inv
    FROM public.workspace_invitations
    WHERE token_digest = p_token_digest
    FOR UPDATE;

    IF inv.id IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = '02000',
            MESSAGE = 'Invitation token not found';
    END IF;

    IF inv.status <> 'PENDING' THEN
        RAISE EXCEPTION USING ERRCODE = '22023',
            MESSAGE = 'Invitation is no longer pending';
    END IF;

    IF inv.expires_at <= pg_catalog.statement_timestamp() THEN
        UPDATE public.workspace_invitations
        SET status = 'EXPIRED'
        WHERE id = inv.id;
        RAISE EXCEPTION USING ERRCODE = '22023',
            MESSAGE = 'Invitation has expired';
    END IF;

    -- Verify caller matches invited email
    IF pg_catalog.lower(actor_email) <> inv.invited_email THEN
        RAISE EXCEPTION USING ERRCODE = '42501',
            MESSAGE = 'This invitation was sent to a different email address';
    END IF;

    SELECT * INTO target_ws
    FROM public.workspaces
    WHERE id = inv.workspace_id;

    -- Handle existing membership if user was previously revoked
    SELECT * INTO existing_mem
    FROM public.workspace_memberships
    WHERE workspace_id = inv.workspace_id AND user_id = actor
    FOR UPDATE;

    IF existing_mem.id IS NOT NULL THEN
        IF existing_mem.status = 'ACTIVE' THEN
            -- Already member: mark invitation accepted and return existing
            res_mem_id := existing_mem.id;
            res_role := existing_mem.role_code;
        ELSE
            -- Reactivate previously revoked membership
            UPDATE public.workspace_memberships
            SET status = 'ACTIVE',
                role_code = inv.role_code,
                revoked_at = NULL,
                updated_at = pg_catalog.statement_timestamp()
            WHERE id = existing_mem.id
            RETURNING id, role_code INTO res_mem_id, res_role;
        END IF;
    ELSE
        -- Insert new membership
        INSERT INTO public.workspace_memberships (
            workspace_id,
            user_id,
            role_code,
            status
        ) VALUES (
            inv.workspace_id,
            actor,
            inv.role_code,
            'ACTIVE'
        )
        RETURNING id, role_code INTO res_mem_id, res_role;
    END IF;

    -- Mark invitation as accepted
    UPDATE public.workspace_invitations
    SET status = 'ACCEPTED',
        accepted_membership_id = res_mem_id,
        accepted_at = pg_catalog.statement_timestamp(),
        updated_at = pg_catalog.statement_timestamp()
    WHERE id = inv.id;

    -- Audit record
    INSERT INTO public.audit_events (
        workspace_id,
        actor_kind,
        actor_id,
        action,
        target_type,
        target_id,
        after_state
    ) VALUES (
        inv.workspace_id,
        'USER',
        actor,
        'invitation.accepted',
        'workspace_membership',
        res_mem_id,
        pg_catalog.json_build_object(
            'invitation_id', inv.id,
            'role_code', res_role
        )::jsonb
    );

    RETURN QUERY
    SELECT inv.workspace_id, res_mem_id, res_role, target_ws.name;
END;
$function$;

CREATE OR REPLACE FUNCTION public.app_update_membership_role(
    p_membership_id uuid,
    p_new_role text,
    p_expected_version bigint
)
RETURNS TABLE (
    membership_id uuid,
    user_id uuid,
    role_code text,
    status text,
    version bigint
)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = ''
AS $function$
DECLARE
    actor uuid;
    ws_id uuid;
    target_row public.workspace_memberships%ROWTYPE;
    old_role text;
BEGIN
    actor := public.app_current_user_id();
    ws_id := public.app_current_workspace_id();

    IF actor IS NULL OR ws_id IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = '28000',
            MESSAGE = 'Authentication and active workspace context required';
    END IF;

    IF NOT public.app_has_permission('team.manage') THEN
        RAISE EXCEPTION USING ERRCODE = '42501',
            MESSAGE = 'Only workspace owners may change member roles';
    END IF;

    IF p_new_role NOT IN ('ADMIN', 'MANAGER', 'MEMBER', 'VIEWER') THEN
        RAISE EXCEPTION USING ERRCODE = '22023',
            MESSAGE = 'Role changes cannot assign OWNER; use ownership transfer';
    END IF;

    SELECT * INTO target_row
    FROM public.workspace_memberships
    WHERE id = p_membership_id AND workspace_id = ws_id
    FOR UPDATE;

    IF target_row.id IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = '02000',
            MESSAGE = 'Target membership not found in current workspace';
    END IF;

    IF target_row.user_id = actor THEN
        RAISE EXCEPTION USING ERRCODE = '42501',
            MESSAGE = 'Self-role changes are prohibited';
    END IF;

    IF target_row.role_code = 'OWNER' THEN
        RAISE EXCEPTION USING ERRCODE = '42501',
            MESSAGE = 'Cannot modify an OWNER membership directly; use ownership transfer';
    END IF;

    IF target_row.version <> p_expected_version THEN
        RAISE EXCEPTION USING ERRCODE = '40001',
            MESSAGE = 'Membership was modified concurrently; reload and retry';
    END IF;

    old_role := target_row.role_code;

    UPDATE public.workspace_memberships
    SET role_code = p_new_role,
        updated_at = pg_catalog.statement_timestamp()
    WHERE id = p_membership_id
    RETURNING * INTO target_row;

    INSERT INTO public.audit_events (
        workspace_id,
        actor_kind,
        actor_id,
        action,
        target_type,
        target_id,
        before_state,
        after_state
    ) VALUES (
        ws_id,
        'USER',
        actor,
        'member.role_changed',
        'workspace_membership',
        p_membership_id,
        pg_catalog.json_build_object('role_code', old_role)::jsonb,
        pg_catalog.json_build_object('role_code', p_new_role)::jsonb
    );

    RETURN QUERY
    SELECT target_row.id, target_row.user_id, target_row.role_code, target_row.status, target_row.version;
END;
$function$;

CREATE OR REPLACE FUNCTION public.app_revoke_membership(
    p_membership_id uuid,
    p_expected_version bigint
)
RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = ''
AS $function$
DECLARE
    actor uuid;
    ws_id uuid;
    target_row public.workspace_memberships%ROWTYPE;
BEGIN
    actor := public.app_current_user_id();
    ws_id := public.app_current_workspace_id();

    IF actor IS NULL OR ws_id IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = '28000',
            MESSAGE = 'Authentication and active workspace context required';
    END IF;

    IF NOT public.app_has_permission('team.manage') THEN
        RAISE EXCEPTION USING ERRCODE = '42501',
            MESSAGE = 'Only workspace owners may remove members';
    END IF;

    SELECT * INTO target_row
    FROM public.workspace_memberships
    WHERE id = p_membership_id AND workspace_id = ws_id
    FOR UPDATE;

    IF target_row.id IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = '02000',
            MESSAGE = 'Target membership not found in current workspace';
    END IF;

    IF target_row.user_id = actor THEN
        RAISE EXCEPTION USING ERRCODE = '42501',
            MESSAGE = 'Self-removal from workspace is prohibited';
    END IF;

    IF target_row.role_code = 'OWNER' THEN
        RAISE EXCEPTION USING ERRCODE = '42501',
            MESSAGE = 'Cannot revoke an active OWNER membership';
    END IF;

    IF target_row.version <> p_expected_version THEN
        RAISE EXCEPTION USING ERRCODE = '40001',
            MESSAGE = 'Membership was modified concurrently; reload and retry';
    END IF;

    UPDATE public.workspace_memberships
    SET status = 'REVOKED',
        revoked_at = pg_catalog.statement_timestamp(),
        updated_at = pg_catalog.statement_timestamp()
    WHERE id = p_membership_id;

    INSERT INTO public.audit_events (
        workspace_id,
        actor_kind,
        actor_id,
        action,
        target_type,
        target_id,
        before_state,
        after_state
    ) VALUES (
        ws_id,
        'USER',
        actor,
        'member.removed',
        'workspace_membership',
        p_membership_id,
        pg_catalog.json_build_object('status', 'ACTIVE', 'role_code', target_row.role_code)::jsonb,
        pg_catalog.json_build_object('status', 'REVOKED')::jsonb
    );

    RETURN true;
END;
$function$;

CREATE OR REPLACE FUNCTION public.app_transfer_workspace_ownership(
    p_target_membership_id uuid,
    p_expected_owner_version bigint,
    p_expected_target_version bigint
)
RETURNS TABLE (
    workspace_id uuid,
    previous_owner_id uuid,
    new_owner_id uuid
)
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = ''
AS $function$
DECLARE
    actor uuid;
    ws_id uuid;
    owner_row public.workspace_memberships%ROWTYPE;
    target_row public.workspace_memberships%ROWTYPE;
BEGIN
    actor := public.app_current_user_id();
    ws_id := public.app_current_workspace_id();

    IF actor IS NULL OR ws_id IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = '28000',
            MESSAGE = 'Authentication and active workspace context required';
    END IF;

    IF NOT public.app_has_permission('ownership.manage') THEN
        RAISE EXCEPTION USING ERRCODE = '42501',
            MESSAGE = 'Only the active workspace owner may transfer ownership';
    END IF;

    -- Lock the workspace to serialize ownership transfers
    PERFORM 1 FROM public.workspaces WHERE id = ws_id FOR UPDATE;

    SELECT * INTO owner_row
    FROM public.workspace_memberships
    WHERE workspace_id = ws_id AND user_id = actor AND role_code = 'OWNER' AND status = 'ACTIVE'
    FOR UPDATE;

    IF owner_row.id IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = '42501',
            MESSAGE = 'Current user is not the active workspace owner';
    END IF;

    IF owner_row.version <> p_expected_owner_version THEN
        RAISE EXCEPTION USING ERRCODE = '40001',
            MESSAGE = 'Owner membership version conflict; reload and retry';
    END IF;

    SELECT * INTO target_row
    FROM public.workspace_memberships
    WHERE id = p_target_membership_id AND workspace_id = ws_id AND status = 'ACTIVE'
    FOR UPDATE;

    IF target_row.id IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = '02000',
            MESSAGE = 'Target active member not found in current workspace';
    END IF;

    IF target_row.id = owner_row.id THEN
        RAISE EXCEPTION USING ERRCODE = '22023',
            MESSAGE = 'Cannot transfer ownership to yourself';
    END IF;

    IF target_row.version <> p_expected_target_version THEN
        RAISE EXCEPTION USING ERRCODE = '40001',
            MESSAGE = 'Target member version conflict; reload and retry';
    END IF;

    -- Demote old owner to ADMIN first
    UPDATE public.workspace_memberships
    SET role_code = 'ADMIN',
        updated_at = pg_catalog.statement_timestamp()
    WHERE id = owner_row.id;

    -- Promote target member to OWNER second
    UPDATE public.workspace_memberships
    SET role_code = 'OWNER',
        updated_at = pg_catalog.statement_timestamp()
    WHERE id = target_row.id;

    -- Increment workspace owner policy version
    UPDATE public.workspaces
    SET owner_policy_version = owner_policy_version + 1,
        updated_at = pg_catalog.statement_timestamp()
    WHERE id = ws_id;

    INSERT INTO public.audit_events (
        workspace_id,
        actor_kind,
        actor_id,
        action,
        target_type,
        target_id,
        before_state,
        after_state
    ) VALUES (
        ws_id,
        'USER',
        actor,
        'ownership.transferred',
        'workspace',
        ws_id,
        pg_catalog.json_build_object('owner_user_id', actor, 'owner_membership_id', owner_row.id)::jsonb,
        pg_catalog.json_build_object('owner_user_id', target_row.user_id, 'owner_membership_id', target_row.id)::jsonb
    );

    RETURN QUERY
    SELECT ws_id, owner_row.id, target_row.id;
END;
$function$;

-- ---------------------------------------------------------------------------
-- 2. Notification Helper & Profile Preference Grants
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.app_create_in_app_notification(
    p_workspace_id uuid,
    p_recipient_user_id uuid,
    p_type text,
    p_source_event_id uuid,
    p_summary text,
    p_resource_link text DEFAULT NULL
)
RETURNS uuid
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = ''
AS $function$
DECLARE
    mem_id uuid;
    notif_id uuid;
BEGIN
    -- Find recipient membership id
    SELECT id INTO mem_id
    FROM public.workspace_memberships
    WHERE workspace_id = p_workspace_id
      AND user_id = p_recipient_user_id
      AND status = 'ACTIVE';

    IF mem_id IS NULL THEN
        RETURN NULL;
    END IF;

    INSERT INTO public.notifications (
        workspace_id,
        recipient_user_id,
        recipient_membership_id,
        source_event_id,
        type,
        summary,
        resource_link
    ) VALUES (
        p_workspace_id,
        p_recipient_user_id,
        mem_id,
        p_source_event_id,
        p_type,
        p_summary,
        p_resource_link
    )
    ON CONFLICT (workspace_id, recipient_user_id, source_event_id, type)
    DO NOTHING
    RETURNING id INTO notif_id;

    RETURN notif_id;
END;
$function$;

-- Allow app_api to update user profile preferences
GRANT UPDATE (preferences, display_name) ON public.profiles TO app_api;

DO $policy_check$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_policies
        WHERE schemaname = 'public'
          AND tablename = 'profiles'
          AND policyname = 'profiles_api_update'
    ) THEN
        CREATE POLICY profiles_api_update ON public.profiles
            FOR UPDATE TO app_api
            USING (id = (SELECT public.app_current_user_id()))
            WITH CHECK (id = (SELECT public.app_current_user_id()));
    END IF;
END;
$policy_check$;

-- ---------------------------------------------------------------------------
-- 3. Platform Operator Admin Commands
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.app_admin_restrict_workspace(
    p_workspace_id uuid,
    p_reason text,
    p_operator_id uuid DEFAULT NULL
)
RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = ''
AS $function$
DECLARE
    ws public.workspaces%ROWTYPE;
BEGIN
    IF p_reason IS NULL OR pg_catalog.char_length(p_reason) < 3 THEN
        RAISE EXCEPTION USING ERRCODE = '22023',
            MESSAGE = 'A specific restriction reason is required';
    END IF;

    SELECT * INTO ws
    FROM public.workspaces
    WHERE id = p_workspace_id
    FOR UPDATE;

    IF ws.id IS NULL THEN
        RETURN false;
    END IF;

    UPDATE public.workspaces
    SET status = 'RESTRICTED',
        sending_restriction_reason = p_reason,
        sending_restriction_version = sending_restriction_version + 1,
        updated_at = pg_catalog.statement_timestamp()
    WHERE id = p_workspace_id;

    INSERT INTO public.platform_audit_events (
        actor_kind,
        actor_id,
        action,
        target_type,
        target_id,
        before_state,
        after_state,
        reason
    ) VALUES (
        'OPERATOR',
        p_operator_id,
        'workspace.restrict',
        'workspace',
        p_workspace_id,
        pg_catalog.json_build_object('status', ws.status)::jsonb,
        pg_catalog.json_build_object('status', 'RESTRICTED')::jsonb,
        p_reason
    );

    RETURN true;
END;
$function$;

CREATE OR REPLACE FUNCTION public.app_admin_restore_workspace(
    p_workspace_id uuid,
    p_operator_id uuid DEFAULT NULL
)
RETURNS boolean
LANGUAGE plpgsql VOLATILE SECURITY DEFINER
SET search_path = ''
AS $function$
DECLARE
    ws public.workspaces%ROWTYPE;
BEGIN
    SELECT * INTO ws
    FROM public.workspaces
    WHERE id = p_workspace_id
    FOR UPDATE;

    IF ws.id IS NULL THEN
        RETURN false;
    END IF;

    UPDATE public.workspaces
    SET status = 'ACTIVE',
        sending_restriction_reason = NULL,
        sending_restriction_version = sending_restriction_version + 1,
        updated_at = pg_catalog.statement_timestamp()
    WHERE id = p_workspace_id;

    INSERT INTO public.platform_audit_events (
        actor_kind,
        actor_id,
        action,
        target_type,
        target_id,
        before_state,
        after_state,
        reason
    ) VALUES (
        'OPERATOR',
        p_operator_id,
        'workspace.restore',
        'workspace',
        p_workspace_id,
        pg_catalog.json_build_object('status', ws.status)::jsonb,
        pg_catalog.json_build_object('status', 'ACTIVE')::jsonb,
        'Restored by platform operator'
    );

    RETURN true;
END;
$function$;

-- Function owners and execution grants
ALTER FUNCTION public.app_create_workspace_invitation(text, text, text, timestamptz) OWNER TO app_foundation_reader;
ALTER FUNCTION public.app_revoke_workspace_invitation(uuid) OWNER TO app_foundation_reader;
ALTER FUNCTION public.app_accept_workspace_invitation(text) OWNER TO app_foundation_reader;
ALTER FUNCTION public.app_update_membership_role(uuid, text, bigint) OWNER TO app_foundation_reader;
ALTER FUNCTION public.app_revoke_membership(uuid, bigint) OWNER TO app_foundation_reader;
ALTER FUNCTION public.app_transfer_workspace_ownership(uuid, bigint, bigint) OWNER TO app_foundation_reader;
ALTER FUNCTION public.app_create_in_app_notification(uuid, uuid, text, uuid, text, text) OWNER TO app_foundation_reader;
ALTER FUNCTION public.app_admin_restrict_workspace(uuid, text, uuid) OWNER TO app_foundation_reader;
ALTER FUNCTION public.app_admin_restore_workspace(uuid, uuid) OWNER TO app_foundation_reader;

REVOKE ALL ON FUNCTION public.app_create_workspace_invitation(text, text, text, timestamptz) FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.app_revoke_workspace_invitation(uuid) FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.app_accept_workspace_invitation(text) FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.app_update_membership_role(uuid, text, bigint) FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.app_revoke_membership(uuid, bigint) FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.app_transfer_workspace_ownership(uuid, bigint, bigint) FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.app_create_in_app_notification(uuid, uuid, text, uuid, text, text) FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.app_admin_restrict_workspace(uuid, text, uuid) FROM PUBLIC, anon, authenticated, service_role;
REVOKE ALL ON FUNCTION public.app_admin_restore_workspace(uuid, uuid) FROM PUBLIC, anon, authenticated, service_role;

GRANT EXECUTE ON FUNCTION public.app_create_workspace_invitation(text, text, text, timestamptz) TO app_api;
GRANT EXECUTE ON FUNCTION public.app_revoke_workspace_invitation(uuid) TO app_api;
GRANT EXECUTE ON FUNCTION public.app_accept_workspace_invitation(text) TO app_api;
GRANT EXECUTE ON FUNCTION public.app_update_membership_role(uuid, text, bigint) TO app_api;
GRANT EXECUTE ON FUNCTION public.app_revoke_membership(uuid, bigint) TO app_api;
GRANT EXECUTE ON FUNCTION public.app_transfer_workspace_ownership(uuid, bigint, bigint) TO app_api;
GRANT EXECUTE ON FUNCTION public.app_create_in_app_notification(uuid, uuid, text, uuid, text, text) TO app_api, app_worker_general;
GRANT EXECUTE ON FUNCTION public.app_admin_restrict_workspace(uuid, text, uuid) TO app_api;
GRANT EXECUTE ON FUNCTION public.app_admin_restore_workspace(uuid, uuid) TO app_api;

REVOKE CREATE ON SCHEMA public FROM app_foundation_reader;
REVOKE app_foundation_reader FROM CURRENT_USER;

COMMIT;
