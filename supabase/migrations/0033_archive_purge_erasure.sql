-- Purge and privacy-erasure commands (ADR-0015). Forward migration per
-- CLAUDE.md/AGENTS.md.
--
-- Migration:      0033_archive_purge_erasure.sql
-- Purpose:        Give runtime a *controlled* way to (a) physically purge
--                 things that never sent anything and (b) erase personal data
--                 from things that did, without changing any foreign key to
--                 CASCADE and without weakening the immutability guards for
--                 any ordinary role. Archive / unarchive need no schema change.
-- Creates:        role app_erasure (NOLOGIN, NOBYPASSRLS, owner of the commands
--                 below); columns campaigns.erased_at and leads.erased_at;
--                 SECURITY DEFINER commands, all executable by app_api only and
--                 all requiring workspace.manage (ADMIN/OWNER) *inside* the
--                 function:
--                   app_purge_campaign(ws, campaign)   never-activated campaign
--                   app_erase_campaign(ws, campaign)   activated ARCHIVED campaign
--                   app_erase_lead(ws, lead)           one person, all campaigns
--                   app_purge_template(ws, template)   unused, archived template
--                   app_purge_lead_list(ws, list)      unused, archived list
--                   app_purge_import(ws, import)       finished import + rows
--                   app_purge_mailbox(ws, mailbox)     disconnected, unused mailbox
--                 plus internal helpers (no runtime grant):
--                   app_erasure_authorize / app_erasure_audit /
--                   app_erase_recipient_records
--                 and the RLS policies + least-privilege grants app_erasure needs.
-- Modifies:       three trigger functions, additively:
--                   app_guard_recipient_identity  (may change canonical_address)
--                   app_guard_message_snapshot    (may rewrite the rendered snapshot)
--                   app_guard_campaign_config     (may DELETE campaign_mailboxes)
--                 The first two allow the change ONLY when the session user is
--                 app_erasure AND the transaction-local flag app.erasure = 'on'
--                 (only the commands above run as app_erasure; the role has no
--                 login and nobody is left a member of it). The third allows it
--                 only for a campaign whose erased_at is set, a column no
--                 runtime role can write.
-- Deletes:        Nothing at migration time. The commands delete only when a
--                 user invokes them.
-- Constraints:    none added; no foreign key changed.
-- Indexes:        none.
-- RLS/security:   app_erasure gets explicit table grants and permissive
--                 policies; NOBYPASSRLS. Commands re-check the caller inside the
--                 function (workspace match + workspace.manage), take no actor
--                 argument (the actor is app_current_user_id()) and write an
--                 audit_events row for every call. Errors are 'erasure:<code>'
--                 messages the API translates; nothing else leaks.
-- Existing data:  Unchanged. Both new columns are NULL for every existing row.
-- Application:    Additive. Code that does not call the commands is unaffected.
-- Risk:           Medium. The commands are destructive by design and irreversible
--                 once committed. Mitigations: archive-first rules, in-flight
--                 checks, atomic transactions, typed confirmation in the UI.
--                 Suppressions are NEVER deleted; an address with an ACTIVE
--                 suppression keeps its recipient_addresses row (the minimal
--                 opt-out record) and only the rest of the person is erased.
-- Recovery:       There is no undo for a purge/erase (that is the point).
--                 To remove the feature: DROP the commands and helpers, revoke
--                 and drop app_erasure, restore the three guards from 0002,
--                 0003 and 0004, then DROP COLUMN erased_at on both tables.
--
-- PREPARED ONLY. Do not apply to any shared/staging/production environment
-- without the project owner's separate review and explicit authorization.

BEGIN;
SET LOCAL search_path = '';

DO $preflight$
BEGIN
    IF pg_catalog.to_regclass('public.personalization_previews') IS NULL
       OR pg_catalog.to_regclass('public.message_events') IS NULL THEN
        RAISE EXCEPTION '0033 requires migrations through 0030 to already be applied';
    END IF;
    IF pg_catalog.to_regprocedure('public.app_purge_campaign(uuid,uuid)') IS NOT NULL
       OR EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'app_erasure') THEN
        RAISE EXCEPTION '0033 already applied: app_erasure or app_purge_campaign exists';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'app_api')
       OR NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'app_integrity_guard') THEN
        RAISE EXCEPTION '0033 requires roles app_api and app_integrity_guard from 0001/0003';
    END IF;
END;
$preflight$;

-- ---------------------------------------------------------------------------
-- Markers
-- ---------------------------------------------------------------------------

ALTER TABLE public.campaigns ADD COLUMN erased_at timestamptz;
ALTER TABLE public.leads ADD COLUMN erased_at timestamptz;

-- ---------------------------------------------------------------------------
-- Owner role for the commands. Temporary migration-only elevation, mirroring
-- 0006/0018; revoked again before COMMIT. Never grant to an API, worker,
-- browser or operator login.
-- ---------------------------------------------------------------------------

CREATE ROLE app_erasure WITH NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT NOREPLICATION NOBYPASSRLS;
GRANT app_erasure TO CURRENT_USER WITH INHERIT TRUE, SET TRUE;
GRANT CREATE ON SCHEMA public TO app_erasure;
-- Needed only to CREATE OR REPLACE app_guard_campaign_config (owned by it).
GRANT app_integrity_guard TO CURRENT_USER WITH INHERIT TRUE, SET TRUE;

-- ---------------------------------------------------------------------------
-- Least-privilege table access for app_erasure
-- ---------------------------------------------------------------------------

GRANT SELECT, DELETE ON
    public.campaign_sequences, public.sequence_steps, public.campaign_settings_versions,
    public.campaign_mailboxes, public.campaign_audiences, public.campaign_planning_jobs,
    public.campaign_personalization_approvals, public.campaign_step_attachments,
    public.tenant_rate_policies, public.lead_list_memberships, public.lead_lists,
    public.import_row_results, public.template_versions, public.mailbox_connections
    TO app_erasure;
GRANT SELECT, DELETE ON public.campaigns, public.templates, public.mailboxes,
    public.import_jobs, public.personalization_previews, public.audience_capture_sources,
    public.campaign_audience_members
    TO app_erasure;
GRANT SELECT, DELETE ON public.oauth_flows, public.mailbox_sync_states TO app_erasure;

GRANT SELECT ON
    public.message_attempts, public.inbound_outreach_links, public.recipient_outcomes,
    public.suppressions
    TO app_erasure;

GRANT UPDATE (erased_at, draft_sequence_id, draft_audience_id, current_settings_id)
    ON public.campaigns TO app_erasure;
GRANT UPDATE (gate_held, released_at) ON public.audience_capture_sources TO app_erasure;
GRANT UPDATE (frozen_variables) ON public.campaign_audience_members TO app_erasure;
GRANT SELECT, UPDATE (state, stop_reason, next_step_id, frozen_destination, frozen_variables, version)
    ON public.campaign_enrollments TO app_erasure;
GRANT SELECT, UPDATE (status, terminal_reason, content_subject, content_body_html, content_digest, frozen_destination)
    ON public.messages TO app_erasure;
GRANT SELECT, UPDATE (detail) ON public.message_events TO app_erasure;
GRANT SELECT, UPDATE (state, completed_at, lease_owner, lease_expires_at, personalization_facts, angle)
    ON public.message_generations TO app_erasure;
GRANT UPDATE (subject, body_html, facts, research_summary) ON public.personalization_previews TO app_erasure;
GRANT SELECT, UPDATE (participants, subject, content_text) ON public.inbound_messages TO app_erasure;
GRANT SELECT, UPDATE (canonical_address, original_display) ON public.recipient_addresses TO app_erasure;
GRANT SELECT, UPDATE (
    original_address, canonical_address, first_name, last_name, company, title, custom_fields,
    status, archived_at, erased_at, phone, department, experience_years, linkedin_url, website,
    city, state, country, company_website, company_industry, company_founded_year,
    company_linkedin_url
) ON public.leads TO app_erasure;
GRANT UPDATE (list_id) ON public.import_jobs TO app_erasure;
GRANT UPDATE (current_version_id) ON public.templates TO app_erasure;
-- SELECT ... FOR UPDATE needs some UPDATE privilege on the table. The commands
-- lock these rows before deleting them but never write these columns.
GRANT UPDATE (version) ON public.lead_lists, public.mailboxes TO app_erasure;
GRANT INSERT ON public.audit_events TO app_erasure;

-- One permissive policy per table: the FORCE ROW LEVEL SECURITY tables would
-- otherwise hide every row from the owner role. The commands enforce tenancy
-- themselves (every statement is filtered by the authorized workspace).
DO $policies$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'campaigns', 'campaign_sequences', 'sequence_steps', 'campaign_settings_versions',
        'campaign_mailboxes', 'campaign_audiences', 'audience_capture_sources',
        'campaign_audience_members', 'campaign_planning_jobs', 'campaign_enrollments',
        'campaign_personalization_approvals', 'campaign_step_attachments',
        'personalization_previews', 'message_generations', 'messages', 'message_attempts',
        'message_events', 'inbound_messages', 'inbound_outreach_links', 'recipient_outcomes',
        'tenant_rate_policies', 'leads', 'recipient_addresses', 'suppressions',
        'lead_lists', 'lead_list_memberships', 'import_jobs', 'import_row_results',
        'templates', 'template_versions', 'mailboxes', 'mailbox_connections',
        'mailbox_sync_states', 'oauth_flows', 'audit_events'
    ] LOOP
        EXECUTE pg_catalog.format(
            'CREATE POLICY %I ON public.%I FOR ALL TO app_erasure USING (true) WITH CHECK (true)',
            t || '_erasure_all', t
        );
    END LOOP;
END;
$policies$;

-- ---------------------------------------------------------------------------
-- Guards: additive exceptions, each usable only from an erasure command.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE FUNCTION public.app_guard_recipient_identity() RETURNS trigger
LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path = ''
AS $function$
BEGIN
    IF (NEW.workspace_id, NEW.canonical_address, NEW.normalization_version) IS DISTINCT FROM
       (OLD.workspace_id, OLD.canonical_address, OLD.normalization_version) THEN
        IF CURRENT_USER = 'app_erasure'
           AND pg_catalog.current_setting('app.erasure', true) = 'on'
           AND NEW.workspace_id = OLD.workspace_id
           AND NEW.normalization_version = OLD.normalization_version THEN
            RETURN NEW;
        END IF;
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Recipient safety identity is immutable';
    END IF;
    RETURN NEW;
END;
$function$;

CREATE OR REPLACE FUNCTION public.app_guard_message_snapshot() RETURNS trigger
LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path = ''
AS $function$
BEGIN
    IF OLD.rendered_at IS NOT NULL AND
       (NEW.content_subject, NEW.content_body_html, NEW.content_digest, NEW.renderer_version, NEW.rendered_at,
        NEW.frozen_destination, NEW.frozen_sender_address, NEW.frozen_sender_name)
       IS DISTINCT FROM
       (OLD.content_subject, OLD.content_body_html, OLD.content_digest, OLD.renderer_version, OLD.rendered_at,
        OLD.frozen_destination, OLD.frozen_sender_address, OLD.frozen_sender_name) THEN
        -- Erasure may replace the recipient-specific parts of the snapshot, and
        -- nothing else: the timing, renderer and sender identity stay fixed.
        IF NOT (
            CURRENT_USER = 'app_erasure'
            AND pg_catalog.current_setting('app.erasure', true) = 'on'
            AND NEW.rendered_at IS NOT DISTINCT FROM OLD.rendered_at
            AND NEW.renderer_version = OLD.renderer_version
            AND NEW.frozen_sender_address IS NOT DISTINCT FROM OLD.frozen_sender_address
            AND NEW.frozen_sender_name IS NOT DISTINCT FROM OLD.frozen_sender_name
        ) THEN
            RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Rendered message snapshot is immutable';
        END IF;
    END IF;
    IF OLD.status IN ('SENT','FAILED','SKIPPED','CANCELLED') AND NEW.status <> OLD.status THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Terminal message cannot be reopened';
    END IF;
    IF OLD.rfc_message_id IS NOT NULL AND NEW.rfc_message_id IS DISTINCT FROM OLD.rfc_message_id THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Transport correlation identity is immutable';
    END IF;
    RETURN NEW;
END;
$function$;

-- SECURITY DEFINER, owned by app_integrity_guard (kept). The campaign row it
-- already locks tells it whether an erasure command is purging this campaign.
CREATE OR REPLACE FUNCTION public.app_guard_campaign_config() RETURNS trigger
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = ''
AS $function$
DECLARE
    tenant uuid := COALESCE(NEW.workspace_id, OLD.workspace_id);
    campaign_uuid uuid := COALESCE(NEW.campaign_id, OLD.campaign_id);
    parent_status text;
    parent_erased timestamptz;
BEGIN
    SELECT status, erased_at INTO STRICT parent_status, parent_erased FROM public.campaigns
      WHERE workspace_id = tenant AND id = campaign_uuid FOR UPDATE;
    IF TG_OP = 'DELETE' AND parent_erased IS NOT NULL THEN
        RETURN OLD;
    END IF;
    IF parent_status <> 'DRAFT' AND NOT (TG_TABLE_NAME = 'campaign_settings_versions' AND parent_status = 'PAUSED') THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Campaign configuration is frozen in this state';
    END IF;
    RETURN COALESCE(NEW, OLD);
END;
$function$;

-- ---------------------------------------------------------------------------
-- Internal helpers (no runtime grant; only the commands below call them)
-- ---------------------------------------------------------------------------

CREATE FUNCTION public.app_erasure_authorize(p_workspace uuid) RETURNS uuid
LANGUAGE plpgsql STABLE SECURITY INVOKER SET search_path = ''
AS $function$
DECLARE
    actor uuid := public.app_current_user_id();
BEGIN
    IF actor IS NULL OR p_workspace IS NULL
       OR p_workspace IS DISTINCT FROM public.app_current_workspace_id()
       OR NOT public.app_has_permission('workspace.manage') THEN
        RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'erasure:forbidden';
    END IF;
    RETURN actor;
END;
$function$;

CREATE FUNCTION public.app_erasure_audit(
    p_workspace uuid, p_actor uuid, p_action text, p_target_type text,
    p_target uuid, p_before jsonb, p_after jsonb
) RETURNS void
LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path = ''
AS $function$
BEGIN
    INSERT INTO public.audit_events
        (workspace_id, actor_kind, actor_id, action, target_type, target_id, before_state, after_state)
    VALUES
        (p_workspace, 'USER', p_actor, p_action, p_target_type, p_target, p_before, p_after);
END;
$function$;

-- Redact the personal data of one campaign's recipients, or of one person across
-- every campaign. Exactly one of p_campaign / p_lead may be NULL. Rows are kept
-- (counts, timing, provider evidence stay); recipient-specific content is not.
CREATE FUNCTION public.app_erase_recipient_records(p_workspace uuid, p_campaign uuid, p_lead uuid)
RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path = ''
AS $function$
DECLARE
    n bigint;
    counts jsonb := '{}'::jsonb;
    erased_digest text := pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to('erased', 'UTF8')), 'hex');
    enr uuid[];
    mem uuid[];
    msgs uuid[];
    inb uuid[];
BEGIN
    IF p_campaign IS NULL AND p_lead IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'erasure:invalid';
    END IF;
    PERFORM pg_catalog.set_config('app.erasure', 'on', true);

    enr := ARRAY(
        SELECT e.id FROM public.campaign_enrollments AS e
        WHERE e.workspace_id = p_workspace
          AND (p_campaign IS NULL OR e.campaign_id = p_campaign)
          AND (p_lead IS NULL OR e.lead_id = p_lead));
    mem := ARRAY(
        SELECT m.id FROM public.campaign_audience_members AS m
        WHERE m.workspace_id = p_workspace
          AND (p_campaign IS NULL OR m.campaign_id = p_campaign)
          AND (p_lead IS NULL OR m.lead_id = p_lead));
    msgs := ARRAY(
        SELECT m.id FROM public.messages AS m
        WHERE m.workspace_id = p_workspace AND m.enrollment_id = ANY (enr));

    -- Work that is already leased or has an unresolved provider attempt must be
    -- reconciled first; erasure never declares it unsent (MESSAGE_STATE_MACHINE).
    IF EXISTS (
        SELECT 1 FROM public.messages AS m
        WHERE m.workspace_id = p_workspace AND m.id = ANY (msgs)
          AND m.status IN ('QUEUED', 'SENDING', 'RETRY_SCHEDULED', 'UNKNOWN_OUTCOME')
    ) OR EXISTS (
        SELECT 1 FROM public.message_attempts AS a
        WHERE a.workspace_id = p_workspace AND a.message_id = ANY (msgs)
          AND a.evidence_state IN ('PREPARED', 'UNKNOWN')
    ) THEN
        RAISE EXCEPTION USING ERRCODE = '55006', MESSAGE = 'erasure:in_flight',
            DETAIL = 'A send is in flight or unresolved. Try again once it has been reconciled.';
    END IF;

    -- Stop everything that has not been sent.
    UPDATE public.messages SET status = 'SKIPPED', terminal_reason = 'erased'
    WHERE workspace_id = p_workspace AND id = ANY (msgs) AND status IN ('PLANNED', 'SCHEDULED');
    GET DIAGNOSTICS n = ROW_COUNT;
    counts := counts || pg_catalog.jsonb_build_object('messages_cancelled', n);

    UPDATE public.message_generations
    SET state = 'SUPERSEDED', completed_at = pg_catalog.transaction_timestamp(),
        lease_owner = NULL, lease_expires_at = NULL
    WHERE workspace_id = p_workspace AND enrollment_id = ANY (enr) AND state = 'PENDING';

    -- One statement per enrollment: stop it if it is still running, and replace
    -- the destination first, because the message-destination guard compares
    -- rendered messages against it.
    UPDATE public.campaign_enrollments
    SET state = CASE WHEN state = 'ACTIVE' THEN 'STOPPED' ELSE state END,
        stop_reason = CASE WHEN state = 'ACTIVE' THEN 'ERASED' ELSE stop_reason END,
        next_step_id = CASE WHEN state = 'ACTIVE' THEN NULL ELSE next_step_id END,
        frozen_destination = 'erased-' || id::text || '@erased.invalid',
        frozen_variables = '{}'::jsonb,
        version = version + 1
    WHERE workspace_id = p_workspace AND id = ANY (enr);
    GET DIAGNOSTICS n = ROW_COUNT;
    counts := counts || pg_catalog.jsonb_build_object('enrollments_redacted', n);

    UPDATE public.messages AS m
    SET content_subject = '[erased]', content_body_html = '[erased]', content_digest = erased_digest,
        frozen_destination = 'erased-' || m.enrollment_id::text || '@erased.invalid'
    WHERE m.workspace_id = p_workspace AND m.id = ANY (msgs) AND m.rendered_at IS NOT NULL;
    GET DIAGNOSTICS n = ROW_COUNT;
    counts := counts || pg_catalog.jsonb_build_object('messages_redacted', n);

    UPDATE public.message_events SET detail = NULL
    WHERE workspace_id = p_workspace AND message_id = ANY (msgs) AND detail IS NOT NULL;

    UPDATE public.campaign_audience_members SET frozen_variables = '{}'::jsonb
    WHERE workspace_id = p_workspace AND id = ANY (mem);
    GET DIAGNOSTICS n = ROW_COUNT;
    counts := counts || pg_catalog.jsonb_build_object('audience_members_redacted', n);

    UPDATE public.personalization_previews
    SET subject = CASE WHEN state = 'OK' THEN '[erased]' END,
        body_html = CASE WHEN state = 'OK' THEN '[erased]' END,
        facts = NULL, research_summary = NULL
    WHERE workspace_id = p_workspace AND audience_member_id = ANY (mem);

    UPDATE public.message_generations SET personalization_facts = NULL, angle = NULL
    WHERE workspace_id = p_workspace AND enrollment_id = ANY (enr);

    inb := ARRAY(
        SELECT l.inbound_message_id FROM public.inbound_outreach_links AS l
        WHERE l.workspace_id = p_workspace AND l.outbound_message_id = ANY (msgs)
        UNION
        SELECT o.inbound_message_id FROM public.recipient_outcomes AS o
        WHERE o.workspace_id = p_workspace AND o.enrollment_id = ANY (enr)
          AND o.inbound_message_id IS NOT NULL);
    UPDATE public.inbound_messages
    SET participants = '{}'::jsonb, subject = NULL, content_text = NULL
    WHERE workspace_id = p_workspace AND id = ANY (inb);
    GET DIAGNOSTICS n = ROW_COUNT;
    counts := counts || pg_catalog.jsonb_build_object('replies_redacted', n);

    RETURN counts;
END;
$function$;

-- ---------------------------------------------------------------------------
-- Commands
-- ---------------------------------------------------------------------------

-- A campaign that was never activated has no messages, enrollments or send
-- history; everything it owns is disposable configuration.
CREATE FUNCTION public.app_purge_campaign(p_workspace uuid, p_campaign uuid) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = ''
AS $function$
DECLARE
    actor uuid := public.app_erasure_authorize(p_workspace);
    camp public.campaigns%ROWTYPE;
    n bigint;
    counts jsonb := '{}'::jsonb;
    keys text[];
BEGIN
    SELECT * INTO camp FROM public.campaigns
    WHERE workspace_id = p_workspace AND id = p_campaign FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = 'P0002', MESSAGE = 'erasure:not_found';
    END IF;
    IF camp.activation_id IS NOT NULL THEN
        RAISE EXCEPTION USING ERRCODE = '55000', MESSAGE = 'erasure:state',
            DETAIL = 'This campaign was activated. Archive it and erase its data instead.';
    END IF;
    IF camp.status NOT IN ('DRAFT', 'ARCHIVED') THEN
        RAISE EXCEPTION USING ERRCODE = '55000', MESSAGE = 'erasure:state',
            DETAIL = 'Only draft or archived campaigns can be deleted.';
    END IF;
    PERFORM pg_catalog.set_config('app.erasure', 'on', true);

    BEGIN
        keys := ARRAY(
            SELECT a.storage_key FROM public.campaign_step_attachments AS a
            WHERE a.workspace_id = p_workspace AND a.campaign_id = p_campaign);

        -- The marker lets the campaign_mailboxes guard allow the delete below.
        UPDATE public.campaigns
        SET erased_at = pg_catalog.transaction_timestamp(), draft_sequence_id = NULL,
            draft_audience_id = NULL, current_settings_id = NULL
        WHERE workspace_id = p_workspace AND id = p_campaign;

        DELETE FROM public.personalization_previews WHERE workspace_id = p_workspace AND campaign_id = p_campaign;
        DELETE FROM public.campaign_personalization_approvals WHERE workspace_id = p_workspace AND campaign_id = p_campaign;
        DELETE FROM public.campaign_step_attachments WHERE workspace_id = p_workspace AND campaign_id = p_campaign;
        GET DIAGNOSTICS n = ROW_COUNT;
        counts := counts || pg_catalog.jsonb_build_object('attachments', n);
        DELETE FROM public.campaign_mailboxes WHERE workspace_id = p_workspace AND campaign_id = p_campaign;
        DELETE FROM public.campaign_planning_jobs WHERE workspace_id = p_workspace AND campaign_id = p_campaign;
        DELETE FROM public.campaign_audience_members WHERE workspace_id = p_workspace AND campaign_id = p_campaign;
        GET DIAGNOSTICS n = ROW_COUNT;
        counts := counts || pg_catalog.jsonb_build_object('audience_members', n);
        -- Release any list capture gate through its counter trigger, then remove.
        UPDATE public.audience_capture_sources
        SET gate_held = false, released_at = pg_catalog.transaction_timestamp()
        WHERE workspace_id = p_workspace AND campaign_id = p_campaign AND gate_held;
        DELETE FROM public.audience_capture_sources WHERE workspace_id = p_workspace AND campaign_id = p_campaign;
        DELETE FROM public.campaign_audiences WHERE workspace_id = p_workspace AND campaign_id = p_campaign;
        DELETE FROM public.sequence_steps WHERE workspace_id = p_workspace AND campaign_id = p_campaign;
        GET DIAGNOSTICS n = ROW_COUNT;
        counts := counts || pg_catalog.jsonb_build_object('steps', n);
        DELETE FROM public.campaign_sequences WHERE workspace_id = p_workspace AND campaign_id = p_campaign;
        DELETE FROM public.campaign_settings_versions WHERE workspace_id = p_workspace AND campaign_id = p_campaign;
        DELETE FROM public.tenant_rate_policies WHERE workspace_id = p_workspace AND campaign_id = p_campaign;
        DELETE FROM public.campaigns WHERE workspace_id = p_workspace AND id = p_campaign;
    EXCEPTION
        WHEN foreign_key_violation OR check_violation THEN
            RAISE EXCEPTION USING ERRCODE = '55006', MESSAGE = 'erasure:in_use',
                DETAIL = 'This campaign is still referenced by other records.';
    END;

    PERFORM public.app_erasure_audit(
        p_workspace, actor, 'campaign.purge', 'campaign', p_campaign,
        pg_catalog.jsonb_build_object('status', camp.status), counts);
    PERFORM pg_catalog.set_config('app.erasure', 'off', true);
    RETURN pg_catalog.jsonb_build_object('storage_keys', pg_catalog.to_jsonb(keys), 'deleted', counts);
END;
$function$;

-- An activated campaign keeps its rows (counters, timing, evidence); the
-- recipients' personal data in it is redacted.
CREATE FUNCTION public.app_erase_campaign(p_workspace uuid, p_campaign uuid) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = ''
AS $function$
DECLARE
    actor uuid := public.app_erasure_authorize(p_workspace);
    camp public.campaigns%ROWTYPE;
    counts jsonb;
BEGIN
    SELECT * INTO camp FROM public.campaigns
    WHERE workspace_id = p_workspace AND id = p_campaign FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = 'P0002', MESSAGE = 'erasure:not_found';
    END IF;
    IF camp.activation_id IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = '55000', MESSAGE = 'erasure:state',
            DETAIL = 'This campaign was never activated. Delete it permanently instead.';
    END IF;
    IF camp.status <> 'ARCHIVED' THEN
        RAISE EXCEPTION USING ERRCODE = '55000', MESSAGE = 'erasure:state',
            DETAIL = 'Archive the campaign before erasing its data.';
    END IF;

    counts := public.app_erase_recipient_records(p_workspace, p_campaign, NULL);
    UPDATE public.campaigns
    SET erased_at = COALESCE(erased_at, pg_catalog.transaction_timestamp())
    WHERE workspace_id = p_workspace AND id = p_campaign;

    PERFORM public.app_erasure_audit(
        p_workspace, actor, 'campaign.erase', 'campaign', p_campaign,
        pg_catalog.jsonb_build_object('status', camp.status), counts);
    PERFORM pg_catalog.set_config('app.erasure', 'off', true);
    RETURN pg_catalog.jsonb_build_object('redacted', counts);
END;
$function$;

-- One person, everywhere. Active suppressions are never removed: an address
-- with one keeps its recipient_addresses row, which is the opt-out record.
CREATE FUNCTION public.app_erase_lead(p_workspace uuid, p_lead uuid) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = ''
AS $function$
DECLARE
    actor uuid := public.app_erasure_authorize(p_workspace);
    lead public.leads%ROWTYPE;
    addr public.recipient_addresses%ROWTYPE;
    suppressed boolean := false;
    counts jsonb;
    n bigint;
    erased_digest text := pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to('erased', 'UTF8')), 'hex');
BEGIN
    SELECT * INTO lead FROM public.leads
    WHERE workspace_id = p_workspace AND id = p_lead FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = 'P0002', MESSAGE = 'erasure:not_found';
    END IF;
    PERFORM pg_catalog.set_config('app.erasure', 'on', true);

    SELECT * INTO addr FROM public.recipient_addresses
    WHERE workspace_id = p_workspace AND canonical_address = lead.canonical_address
      AND normalization_version = lead.normalization_version;

    BEGIN
        IF FOUND AND EXISTS (
            SELECT 1 FROM public.messages AS m
            WHERE m.workspace_id = p_workspace AND m.address_id = addr.id
              AND m.status IN ('QUEUED', 'SENDING', 'RETRY_SCHEDULED', 'UNKNOWN_OUTCOME')
        ) THEN
            RAISE EXCEPTION USING ERRCODE = '55006', MESSAGE = 'erasure:in_flight',
                DETAIL = 'A send to this person is in flight. Try again once it has been reconciled.';
        END IF;

        counts := public.app_erase_recipient_records(p_workspace, NULL, p_lead);

        DELETE FROM public.lead_list_memberships WHERE workspace_id = p_workspace AND lead_id = p_lead;
        GET DIAGNOSTICS n = ROW_COUNT;
        counts := counts || pg_catalog.jsonb_build_object('list_memberships_removed', n);

        IF addr.id IS NOT NULL THEN
            suppressed := EXISTS (
                SELECT 1 FROM public.suppressions AS s
                WHERE s.workspace_id = p_workspace AND s.address_id = addr.id AND s.status = 'ACTIVE');
            IF NOT suppressed THEN
                UPDATE public.recipient_addresses
                SET canonical_address = 'erased-' || id::text || '@erased.invalid', original_display = NULL
                WHERE workspace_id = p_workspace AND id = addr.id;
                -- One-off test sends have no enrollment; their destination is
                -- checked against the address row, so follow it.
                UPDATE public.messages
                SET content_subject = '[erased]', content_body_html = '[erased]', content_digest = erased_digest,
                    frozen_destination = 'erased-' || addr.id::text || '@erased.invalid'
                WHERE workspace_id = p_workspace AND address_id = addr.id
                  AND enrollment_id IS NULL AND rendered_at IS NOT NULL;
            ELSE
                UPDATE public.messages
                SET content_subject = '[erased]', content_body_html = '[erased]', content_digest = erased_digest
                WHERE workspace_id = p_workspace AND address_id = addr.id
                  AND enrollment_id IS NULL AND rendered_at IS NOT NULL;
            END IF;
        END IF;
        counts := counts || pg_catalog.jsonb_build_object('suppression_kept', suppressed);

        UPDATE public.leads
        SET original_address = 'erased-' || id::text || '@erased.invalid',
            canonical_address = 'erased-' || id::text || '@erased.invalid',
            first_name = NULL, last_name = NULL, company = NULL, title = NULL,
            custom_fields = '{}'::jsonb, phone = NULL, department = NULL, experience_years = NULL,
            linkedin_url = NULL, website = NULL, city = NULL, state = NULL, country = NULL,
            company_website = NULL, company_industry = NULL, company_founded_year = NULL,
            company_linkedin_url = NULL,
            status = 'ARCHIVED',
            archived_at = COALESCE(archived_at, pg_catalog.transaction_timestamp()),
            erased_at = COALESCE(erased_at, pg_catalog.transaction_timestamp())
        WHERE workspace_id = p_workspace AND id = p_lead;
    EXCEPTION
        WHEN check_violation THEN
            -- e.g. a list whose membership is held by an audience capture.
            RAISE EXCEPTION USING ERRCODE = '55006', MESSAGE = 'erasure:in_use',
                DETAIL = 'A list this person belongs to is being captured for a campaign. Try again shortly.';
    END;

    PERFORM public.app_erasure_audit(
        p_workspace, actor, 'lead.erase', 'lead', p_lead, NULL, counts);
    PERFORM pg_catalog.set_config('app.erasure', 'off', true);
    RETURN pg_catalog.jsonb_build_object('redacted', counts);
END;
$function$;

CREATE FUNCTION public.app_purge_template(p_workspace uuid, p_template uuid) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = ''
AS $function$
DECLARE
    actor uuid := public.app_erasure_authorize(p_workspace);
    tmpl public.templates%ROWTYPE;
    n bigint;
BEGIN
    SELECT * INTO tmpl FROM public.templates
    WHERE workspace_id = p_workspace AND id = p_template FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = 'P0002', MESSAGE = 'erasure:not_found';
    END IF;
    IF tmpl.archived_at IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = '55000', MESSAGE = 'erasure:state',
            DETAIL = 'Archive the template before deleting it.';
    END IF;
    IF EXISTS (
        SELECT 1 FROM public.sequence_steps AS s
        JOIN public.template_versions AS v
          ON v.workspace_id = s.workspace_id AND v.id = s.source_template_version_id
        WHERE v.workspace_id = p_workspace AND v.template_id = p_template
    ) THEN
        RAISE EXCEPTION USING ERRCODE = '55006', MESSAGE = 'erasure:in_use',
            DETAIL = 'This template was used in a campaign step, so it can only stay archived.';
    END IF;

    BEGIN
        UPDATE public.templates SET current_version_id = NULL
        WHERE workspace_id = p_workspace AND id = p_template;
        DELETE FROM public.template_versions WHERE workspace_id = p_workspace AND template_id = p_template;
        GET DIAGNOSTICS n = ROW_COUNT;
        DELETE FROM public.templates WHERE workspace_id = p_workspace AND id = p_template;
    EXCEPTION
        WHEN foreign_key_violation THEN
            RAISE EXCEPTION USING ERRCODE = '55006', MESSAGE = 'erasure:in_use',
                DETAIL = 'This template is still referenced by other records.';
    END;

    PERFORM public.app_erasure_audit(
        p_workspace, actor, 'template.purge', 'template', p_template,
        NULL, pg_catalog.jsonb_build_object('versions_deleted', n));
    RETURN pg_catalog.jsonb_build_object('deleted', pg_catalog.jsonb_build_object('versions', n));
END;
$function$;

CREATE FUNCTION public.app_purge_lead_list(p_workspace uuid, p_list uuid) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = ''
AS $function$
DECLARE
    actor uuid := public.app_erasure_authorize(p_workspace);
    lst public.lead_lists%ROWTYPE;
    n bigint;
BEGIN
    SELECT * INTO lst FROM public.lead_lists
    WHERE workspace_id = p_workspace AND id = p_list FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = 'P0002', MESSAGE = 'erasure:not_found';
    END IF;
    IF lst.archived_at IS NULL THEN
        RAISE EXCEPTION USING ERRCODE = '55000', MESSAGE = 'erasure:state',
            DETAIL = 'Archive the list before deleting it.';
    END IF;
    IF lst.capture_count <> 0 THEN
        RAISE EXCEPTION USING ERRCODE = '55006', MESSAGE = 'erasure:in_use',
            DETAIL = 'This list is being captured for a campaign. Try again shortly.';
    END IF;

    BEGIN
        -- Import history only remembers which list it targeted.
        UPDATE public.import_jobs SET list_id = NULL WHERE workspace_id = p_workspace AND list_id = p_list;
        DELETE FROM public.lead_list_memberships WHERE workspace_id = p_workspace AND list_id = p_list;
        GET DIAGNOSTICS n = ROW_COUNT;
        DELETE FROM public.lead_lists WHERE workspace_id = p_workspace AND id = p_list;
    EXCEPTION
        WHEN foreign_key_violation OR check_violation THEN
            RAISE EXCEPTION USING ERRCODE = '55006', MESSAGE = 'erasure:in_use',
                DETAIL = 'This list was used to build a campaign audience, so it can only stay archived.';
    END;

    PERFORM public.app_erasure_audit(
        p_workspace, actor, 'lead_list.purge', 'lead_list', p_list,
        NULL, pg_catalog.jsonb_build_object('memberships_removed', n));
    RETURN pg_catalog.jsonb_build_object('deleted', pg_catalog.jsonb_build_object('memberships', n));
END;
$function$;

CREATE FUNCTION public.app_purge_import(p_workspace uuid, p_import uuid) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = ''
AS $function$
DECLARE
    actor uuid := public.app_erasure_authorize(p_workspace);
    imp public.import_jobs%ROWTYPE;
    n bigint;
    keys text[];
BEGIN
    SELECT * INTO imp FROM public.import_jobs
    WHERE workspace_id = p_workspace AND id = p_import FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = 'P0002', MESSAGE = 'erasure:not_found';
    END IF;
    IF imp.status IN ('PENDING', 'PROCESSING') OR imp.lease_owner IS NOT NULL THEN
        RAISE EXCEPTION USING ERRCODE = '55000', MESSAGE = 'erasure:state',
            DETAIL = 'This import is still running.';
    END IF;

    keys := ARRAY(
        SELECT DISTINCT k FROM (
            SELECT imp.storage_object_key AS k
            UNION ALL
            SELECT r.error_artifact_ref FROM public.import_row_results AS r
            WHERE r.workspace_id = p_workspace AND r.import_id = p_import
              AND r.error_artifact_ref IS NOT NULL
        ) AS refs);

    BEGIN
        DELETE FROM public.import_row_results WHERE workspace_id = p_workspace AND import_id = p_import;
        GET DIAGNOSTICS n = ROW_COUNT;
        DELETE FROM public.import_jobs WHERE workspace_id = p_workspace AND id = p_import;
    EXCEPTION
        WHEN foreign_key_violation THEN
            RAISE EXCEPTION USING ERRCODE = '55006', MESSAGE = 'erasure:in_use',
                DETAIL = 'This import is still referenced by other records.';
    END;

    PERFORM public.app_erasure_audit(
        p_workspace, actor, 'import.purge', 'import', p_import,
        NULL, pg_catalog.jsonb_build_object('rows_deleted', n));
    RETURN pg_catalog.jsonb_build_object(
        'storage_keys', pg_catalog.to_jsonb(keys),
        'deleted', pg_catalog.jsonb_build_object('rows', n));
END;
$function$;

-- Removing a mailbox row is only possible when it never took part in sending.
-- Credentials are already destroyed and revoked by the existing disconnect flow,
-- which is a precondition here.
CREATE FUNCTION public.app_purge_mailbox(p_workspace uuid, p_mailbox uuid) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = ''
AS $function$
DECLARE
    actor uuid := public.app_erasure_authorize(p_workspace);
    mb public.mailboxes%ROWTYPE;
BEGIN
    SELECT * INTO mb FROM public.mailboxes
    WHERE workspace_id = p_workspace AND id = p_mailbox FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = 'P0002', MESSAGE = 'erasure:not_found';
    END IF;
    IF mb.connection_state <> 'DISCONNECTED' THEN
        RAISE EXCEPTION USING ERRCODE = '55000', MESSAGE = 'erasure:state',
            DETAIL = 'Disconnect the mailbox before removing it.';
    END IF;

    BEGIN
        DELETE FROM public.tenant_rate_policies WHERE workspace_id = p_workspace AND mailbox_id = p_mailbox;
        DELETE FROM public.mailbox_sync_states WHERE workspace_id = p_workspace AND mailbox_id = p_mailbox;
        DELETE FROM public.oauth_flows WHERE workspace_id = p_workspace AND resulting_mailbox_id = p_mailbox;
        DELETE FROM public.mailbox_connections WHERE workspace_id = p_workspace AND mailbox_id = p_mailbox;
        DELETE FROM public.mailboxes WHERE workspace_id = p_workspace AND id = p_mailbox;
    EXCEPTION
        WHEN foreign_key_violation OR check_violation THEN
            RAISE EXCEPTION USING ERRCODE = '55006', MESSAGE = 'erasure:in_use',
                DETAIL = 'This mailbox has sending history or is assigned to a campaign, so it stays disconnected.';
    END;

    PERFORM public.app_erasure_audit(
        p_workspace, actor, 'mailbox.purge', 'mailbox', p_mailbox, NULL, '{}'::jsonb);
    RETURN '{}'::jsonb;
END;
$function$;

-- ---------------------------------------------------------------------------
-- Ownership and grants
-- ---------------------------------------------------------------------------

ALTER FUNCTION public.app_erasure_authorize(uuid) OWNER TO app_erasure;
ALTER FUNCTION public.app_erasure_audit(uuid, uuid, text, text, uuid, jsonb, jsonb) OWNER TO app_erasure;
ALTER FUNCTION public.app_erase_recipient_records(uuid, uuid, uuid) OWNER TO app_erasure;
ALTER FUNCTION public.app_purge_campaign(uuid, uuid) OWNER TO app_erasure;
ALTER FUNCTION public.app_erase_campaign(uuid, uuid) OWNER TO app_erasure;
ALTER FUNCTION public.app_erase_lead(uuid, uuid) OWNER TO app_erasure;
ALTER FUNCTION public.app_purge_template(uuid, uuid) OWNER TO app_erasure;
ALTER FUNCTION public.app_purge_lead_list(uuid, uuid) OWNER TO app_erasure;
ALTER FUNCTION public.app_purge_import(uuid, uuid) OWNER TO app_erasure;
ALTER FUNCTION public.app_purge_mailbox(uuid, uuid) OWNER TO app_erasure;

REVOKE ALL ON FUNCTION
    public.app_erasure_authorize(uuid),
    public.app_erasure_audit(uuid, uuid, text, text, uuid, jsonb, jsonb),
    public.app_erase_recipient_records(uuid, uuid, uuid),
    public.app_purge_campaign(uuid, uuid),
    public.app_erase_campaign(uuid, uuid),
    public.app_erase_lead(uuid, uuid),
    public.app_purge_template(uuid, uuid),
    public.app_purge_lead_list(uuid, uuid),
    public.app_purge_import(uuid, uuid),
    public.app_purge_mailbox(uuid, uuid)
    FROM PUBLIC, anon, authenticated, service_role;

GRANT EXECUTE ON FUNCTION
    public.app_purge_campaign(uuid, uuid),
    public.app_erase_campaign(uuid, uuid),
    public.app_erase_lead(uuid, uuid),
    public.app_purge_template(uuid, uuid),
    public.app_purge_lead_list(uuid, uuid),
    public.app_purge_import(uuid, uuid),
    public.app_purge_mailbox(uuid, uuid)
    TO app_api;

-- The helpers run inside the commands, as their owner, so app_erasure needs to
-- reach the identity/permission functions and each other.
GRANT EXECUTE ON FUNCTION
    public.app_current_user_id(), public.app_current_workspace_id(),
    public.app_current_workspace_role(), public.app_has_permission(text)
    TO app_erasure;
GRANT EXECUTE ON FUNCTION
    public.app_erasure_authorize(uuid),
    public.app_erasure_audit(uuid, uuid, text, text, uuid, jsonb, jsonb),
    public.app_erase_recipient_records(uuid, uuid, uuid)
    TO app_erasure;

REVOKE CREATE ON SCHEMA public FROM app_erasure;
REVOKE app_erasure FROM CURRENT_USER;
REVOKE app_integrity_guard FROM CURRENT_USER;

DO $postflight$
BEGIN
    IF pg_catalog.has_schema_privilege('app_erasure', 'public', 'CREATE') THEN
        RAISE EXCEPTION '0033 must not leave app_erasure with CREATE on schema public';
    END IF;
    IF pg_catalog.pg_has_role('app_api', 'app_erasure', 'MEMBER')
       OR pg_catalog.pg_has_role('app_worker_send', 'app_erasure', 'MEMBER')
       OR pg_catalog.pg_has_role('app_worker_general', 'app_erasure', 'MEMBER') THEN
        RAISE EXCEPTION '0033 forbids runtime membership in app_erasure';
    END IF;
    IF NOT pg_catalog.has_function_privilege('app_api', 'public.app_erase_lead(uuid,uuid)', 'EXECUTE')
       OR pg_catalog.has_function_privilege('app_worker_send', 'public.app_erase_lead(uuid,uuid)', 'EXECUTE')
       OR pg_catalog.has_function_privilege('app_api', 'public.app_erase_recipient_records(uuid,uuid,uuid)', 'EXECUTE')
       OR pg_catalog.has_function_privilege('app_api', 'public.app_erasure_authorize(uuid)', 'EXECUTE') THEN
        RAISE EXCEPTION '0033 postcondition failed: only the seven commands may be executable by app_api';
    END IF;
    IF (SELECT pg_catalog.pg_get_userbyid(proowner) FROM pg_catalog.pg_proc
        WHERE oid = 'public.app_guard_campaign_config()'::pg_catalog.regprocedure) <> 'app_integrity_guard' THEN
        RAISE EXCEPTION '0033 postcondition failed: app_guard_campaign_config owner changed';
    END IF;
END;
$postflight$;

COMMIT;
