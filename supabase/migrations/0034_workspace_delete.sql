-- Whole-workspace hard delete (ADR-0015 follow-up). Forward migration per
-- CLAUDE.md/AGENTS.md.
--
-- Migration:      0034_workspace_delete.sql
-- Purpose:        Give runtime a single, OWNER-only, irreversible command that
--                 physically deletes a workspace and every row it owns across
--                 the whole schema -- a true hard delete, not an archive and
--                 not the redaction 0033 performs. The Python API layer
--                 (backend/app/modules/erasure/service.py ErasureService.
--                 purge_workspace, backend/app/api/v1/erasure.py) already
--                 checks the typed confirmation and calls exactly
--                 `SELECT public.app_delete_workspace(:ws, :confirm)`.
--
-- *** EXPLICIT DEVIATION FROM 0033 ***
-- 0033's header states "Suppressions are NEVER deleted; an address with an
-- ACTIVE suppression keeps its recipient_addresses row (the minimal opt-out
-- record)". That guarantee is a per-person, per-campaign privacy floor. It
-- does NOT apply here. This is a deliberate, approved product decision: when
-- an OWNER deletes an entire workspace, every row the workspace owns is
-- destroyed, including public.suppressions and public.recipient_addresses,
-- i.e. opt-out/suppression records are deleted along with everything else.
-- There is no workspace left to send from once this command commits, so the
-- usual "never let a suppressed address become sendable again" concern does
-- not apply the way it does to a single erased lead inside a live workspace.
-- Reviewers should treat this as a knowing, one-time override of 0033's
-- compliance guarantee for this one operation, not a general weakening of it.
--
-- Creates:        SECURITY DEFINER command app_delete_workspace(p_workspace
--                 uuid, p_confirmation_name text) RETURNS jsonb, owned by the
--                 existing app_erasure role (created by 0033; not recreated
--                 here), executable by app_api only. No new role.
-- Modifies:       Three existing trigger functions, additively. All three are
--                 SECURITY DEFINER (unchanged from 0003/0025), which means
--                 CURRENT_USER inside their body is always the function's
--                 owner (app_integrity_guard), never the calling role -- so
--                 0033's CURRENT_USER = 'app_erasure' bypass idiom (correct
--                 only in its own SECURITY INVOKER guards) does not work here
--                 and was corrected during testing (see below) to the data-
--                 marker idiom app_guard_campaign_config (0033, itself
--                 SECURITY DEFINER) already uses: bypass on campaigns.
--                 erased_at being set, a column only app_erasure can ever
--                 write (column-level GRANT), rather than on session identity:
--                   app_guard_campaign_snapshot     (0003) -- see Finding A
--                   app_guard_frozen_sequence_steps (0003) -- see Finding B
--                   app_guard_frozen_step_attachments (0025) -- see Finding B
--                 app_guard_campaign_config (0033) and app_require_workspace_
--                 owner (0001) are NOT modified -- see Findings C and D below
--                 for why the existing behaviour already suffices.
-- Deletes:        Nothing at migration time. The command deletes only when a
--                 workspace OWNER invokes it, after the API's typed-
--                 confirmation check and this command's own re-check of both
--                 the confirmation and the caller's authorization.
--
-- ---------------------------------------------------------------------------
-- Investigation findings (required reading before reviewing the code below)
-- ---------------------------------------------------------------------------
--
-- Finding A -- app_guard_campaign_snapshot (0003) blocks nulling an activated
-- campaign's forward references. To delete campaign_sequences/campaign_
-- audiences/campaign_settings_versions for ANY campaign (not just never-
-- activated DRAFT ones, which is all 0033's app_purge_campaign ever handles),
-- campaigns.{draft,activated}_sequence_id, {draft,activated}_audience_id and
-- current_settings_id must first be set to NULL -- those are RESTRICT FKs
-- FROM campaigns INTO its own children, so the children cannot be deleted
-- while campaigns still points at them. But app_guard_campaign_snapshot's
-- BEFORE UPDATE body unconditionally forbids changing activation_id,
-- activated_sequence_id, activated_audience_id, draft_sequence_id or draft_
-- audience_id once OLD.activation_id IS NOT NULL ("Activated audience and
-- content cannot change"). 0033 never hits this because it only ever purges
-- campaigns with activation_id IS NULL. A workspace delete must handle
-- RUNNING/PAUSED/COMPLETED/ARCHIVED campaigns too, so this guard would block
-- the required UPDATE. Fix: bypass (UPDATE-only) whenever NEW.erased_at IS
-- NOT NULL, before any of the existing checks run -- this function is
-- SECURITY DEFINER (CURRENT_USER inside it is always app_integrity_guard, its
-- owner, never the caller, confirmed by running this migration against a real
-- throwaway Postgres during review), so the bypass cannot key on session
-- identity the way 0033's SECURITY INVOKER guards do; erased_at is itself
-- proof of a privileged caller, since only app_erasure has UPDATE privilege
-- on that column. The command's own UPDATE then sets activation_id,
-- activated_sequence_id, activated_audience_id, current_settings_id, draft_
-- sequence_id and draft_audience_id all to NULL and flips status to 'DRAFT'
-- and archived_at to NULL in the SAME statement, which is required to satisfy
-- campaigns_activation_shape_check and campaigns_archive_check (both plain
-- CHECK constraints, evaluated per-statement regardless of the trigger).
--
-- Finding B -- app_guard_frozen_sequence_steps (0003) and app_guard_frozen_
-- step_attachments (0025) block DELETE on sequence_steps / campaign_step_
-- attachments whenever the OWNING campaign_sequences row's own status is
-- 'FROZEN' (i.e. the campaign was ever activated), regardless of the parent
-- CAMPAIGN's status. Setting campaigns.status back to 'DRAFT' (Finding A)
-- does NOT change campaign_sequences.status -- that column is untouched and
-- stays 'FROZEN' for a previously-activated sequence, and campaign_sequences
-- itself cannot be un-frozen (campaign_sequences_guard_frozen / app_guard_
-- sequence explicitly forbids "Activated sequence is immutable" on UPDATE).
-- So a full delete of sequence_steps/campaign_step_attachments for an
-- activated campaign is a second, independent architectural conflict that
-- 0033 never needed to solve. Fix: the same data-marker bypass as Finding A
-- (both are SECURITY DEFINER too), this time DELETE-only and read from the
-- parent campaign's erased_at via a fresh SELECT -- exactly the pattern
-- app_guard_campaign_config (Finding C) already uses for its own DELETE
-- branch. INSERT/UPDATE immutability for ordinary runtime roles is completely
-- untouched.
--
-- Finding C -- app_guard_campaign_config (0033) already suffices unmodified.
-- It is installed BEFORE INSERT ON campaign_settings_versions (INSERT only --
-- DELETE is never guarded there at all) and BEFORE INSERT OR UPDATE OR DELETE
-- ON campaign_mailboxes. Its DELETE branch already allows the delete when the
-- campaign's erased_at is set ("IF TG_OP='DELETE' AND parent_erased IS NOT
-- NULL THEN RETURN OLD"). This command sets campaigns.erased_at for every
-- campaign in the workspace in the same UPDATE described in Finding A, so
-- campaign_mailboxes deletes cleanly with no further change. (Independently,
-- once campaigns.status is forced to 'DRAFT', the guard's other branch --
-- "parent_status <> 'DRAFT'" -- is also false, so either condition alone
-- would suffice; both together is simply belt-and-suspenders.)
--
-- Finding D -- app_require_workspace_owner (0001) does NOT need modification.
-- It is a `CREATE CONSTRAINT TRIGGER ... DEFERRABLE INITIALLY DEFERRED`
-- (both the AFTER INSERT ON workspaces firing and the AFTER INSERT OR UPDATE
-- OR DELETE ON workspace_memberships firing). A deferred constraint trigger's
-- check runs at COMMIT of the enclosing transaction (or at an explicit SET
-- CONSTRAINTS ... IMMEDIATE, which nothing in this session issues), not at
-- the end of each statement or each PL/pgSQL call. This command deletes every
-- workspace_memberships row for the workspace (including the OWNER's) AND
-- the workspaces row itself, in that order, inside the SAME transaction. When
-- the trigger's deferred check finally runs at commit, its body re-queries
-- "does public.workspaces still contain a row with this id" -- by then it
-- does not, so the outer `EXISTS (SELECT 1 FROM public.workspaces ...)`
-- guard in the trigger body is false and the whole check short-circuits to
-- "no exception" without ever requiring an active OWNER membership to exist.
-- (Belt-and-suspenders: `SET CONSTRAINTS ... DEFERRED` is issued explicitly
-- below, naming these two constraint triggers, purely so this behaviour does
-- not silently depend on nobody upstream ever issuing SET CONSTRAINTS ALL
-- IMMEDIATE earlier in the same session/transaction.)
--
-- Finding E -- every other guard/immutability trigger in 0001-0033 that fires
-- on DELETE was checked and does not block this command:
--   workspace_memberships_active_owner_required -- Finding D.
--   campaign_mailboxes_parent_guard (app_guard_campaign_config)  -- Finding C.
--   sequence_steps_guard_frozen (app_guard_frozen_sequence_steps) -- Finding B.
--   campaign_step_attachments_guard_frozen (app_guard_frozen_step_attachments)
--     -- Finding B.
--   lead_list_memberships_capture_guard (app_guard_list_membership, 0002) --
--     blocks DELETE while the list's capture_count <> 0. Not modified; this
--     command instead releases every held audience-capture gate in the
--     workspace first (UPDATE audience_capture_sources SET gate_held=false,
--     released_at=now() WHERE gate_held), which fires the existing
--     audience_capture_sources_counter trigger and drives capture_count back
--     to 0 -- the exact same mechanism app_purge_campaign (0033) already uses
--     per-campaign, just applied workspace-wide before any list membership is
--     deleted.
--   lead_list_memberships_revision (same function, AFTER trigger) -- only
--     maintains a counter; never raises.
--   Every other DELETE-observing trigger in the schema (touch_row triggers,
--   conversations_archive_guard, message_generation_attempts_guard,
--   app_guard_message_snapshot, app_guard_recipient_identity, app_guard_
--   attempt_evidence, app_guard_suppression_release, etc.) fires only on
--   UPDATE, never on DELETE, and is therefore inert here.
--
-- Constraints:    None added or dropped. No foreign key changed, in
--                 particular none changed to CASCADE.
-- Indexes:        None.
-- RLS/security:   app_erasure (NOBYPASSRLS, no login, no runtime member --
--                 unchanged from 0033) gets SELECT+DELETE on every table this
--                 command touches that it did not already have, plus a
--                 permissive FOR ALL policy on each newly-granted FORCE-RLS
--                 table (same "the command enforces tenancy itself, every
--                 statement is filtered by the authorized workspace" pattern
--                 0033 uses). app_delete_workspace re-derives the actor and
--                 workspace from session GUCs via app_current_user_id() /
--                 app_current_workspace_id() (never trusts an argument for
--                 identity) and requires app_has_permission('ownership.manage')
--                 -- the existing OWNER-only capability the transfer-ownership
--                 endpoint already uses; app_has_permission()'s capability
--                 matrix in 0001 is NOT changed. app_erasure_authorize()
--                 (0033) is deliberately NOT reused unchanged: it hardcodes
--                 'workspace.manage' (ADMIN+OWNER), which is too permissive
--                 for destroying an entire workspace. The check here mirrors
--                 its shape with 'ownership.manage' instead. Errors are
--                 'erasure:<code>' messages the existing API translator
--                 (backend/app/modules/erasure/service.py:_translate) already
--                 understands; nothing else leaks.
-- Existing data:  Unchanged by applying this migration. Nothing is deleted
--                 until an OWNER calls the command.
-- Application:    Additive. The Python API layer already calls this exact
--                 function name/signature; nothing else changes call sites.
-- Verification:   Applied to a real, disposable local Postgres (pgserver,
--                 never the Supabase-linked project) via backend/tests/
--                 support/real_pg.py and exercised end-to-end in backend/
--                 tests/test_workspace_delete_real_db.py: OWNER-only
--                 authorization, cross-tenant rejection, wrong-confirmation
--                 rejection, a fully activated workspace with a FROZEN
--                 sequence, sent messages, replies and an ACTIVE suppression
--                 deleted whole, a second call after success failing safely,
--                 and another tenant's data surviving untouched. The full
--                 existing 0033 erasure suite (44 tests) and the rest of the
--                 pgserver-backed suite (archive/tracking/personalization,
--                 164 tests total) still pass unchanged against this
--                 migration, confirming the guard-function edits below did
--                 not affect ordinary (non-erasure) behavior.
-- Risk:           High. This is the most destructive command in the schema:
--                 unlike every 0033 command, it deletes ALL of a tenant's
--                 data in one call, including opt-out/suppression records
--                 (see the deviation notice above) and regardless of any
--                 campaign's send state. It does NOT check for in-flight
--                 sends (QUEUED/SENDING/RETRY_SCHEDULED messages, unresolved
--                 message_attempts) before deleting mailboxes/mailbox_
--                 connections/messages -- app_erase_lead and app_erase_
--                 recipient_records (0033) both refuse in that situation for
--                 a single lead/campaign, but a whole-workspace delete has no
--                 narrower unit of work to defer, and the task did not ask
--                 for new blocking behaviour here. Flagging this as an open
--                 question for human review: should app_delete_workspace
--                 refuse (erasure:in_flight) while any message in the
--                 workspace is QUEUED/SENDING/RETRY_SCHEDULED/UNKNOWN_OUTCOME,
--                 the way the per-lead/per-campaign commands do? Mitigations
--                 in place: OWNER-only, typed-name confirmation (both in the
--                 API and re-checked here), single atomic transaction, and
--                 the audit tombstone below.
-- Recovery:       There is no undo (that is the point). To remove the
--                 feature: DROP FUNCTION public.app_delete_workspace(uuid,
--                 text); revoke the new grants/policies added below; restore
--                 app_guard_campaign_snapshot, app_guard_frozen_sequence_steps
--                 and app_guard_frozen_step_attachments to their pre-0034
--                 bodies (0003/0025).
--
-- Open question for human review (per task instructions, not resolved here):
-- app_erasure_audit() writes one 'workspace.delete' audit_events row so the
-- action is attributable -- but audit_events itself is workspace-scoped and
-- is deleted as part of this same command, so that tombstone does NOT
-- survive the transaction it is written in. There is currently no workspace-
-- independent audit/log table this command could write to instead. Whether
-- one is warranted (e.g. a platform_audit_events-style record, which IS
-- global/non-workspace-scoped and already exists for operator actions) is a
-- product/architecture decision left to the project owner, not invented here.
--
-- PREPARED ONLY. Do not apply to any shared/staging/production environment
-- without the project owner's separate review and explicit authorization.

BEGIN;
SET LOCAL search_path = '';

DO $preflight$
BEGIN
    IF pg_catalog.to_regclass('public.personalization_previews') IS NULL
       OR pg_catalog.to_regclass('public.message_events') IS NULL THEN
        RAISE EXCEPTION '0034 requires migrations through 0030 to already be applied';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'app_erasure')
       OR pg_catalog.to_regprocedure('public.app_purge_campaign(uuid,uuid)') IS NULL THEN
        RAISE EXCEPTION '0034 requires 0033_archive_purge_erasure.sql to already be applied (app_erasure / app_purge_campaign missing)';
    END IF;
    IF pg_catalog.to_regprocedure('public.app_delete_workspace(uuid,text)') IS NOT NULL THEN
        RAISE EXCEPTION '0034 already applied: app_delete_workspace exists';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'app_api')
       OR NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'app_integrity_guard') THEN
        RAISE EXCEPTION '0034 requires roles app_api and app_integrity_guard from 0001/0003';
    END IF;
END;
$preflight$;

-- ---------------------------------------------------------------------------
-- Temporary migration-only elevation. Mirrors 0033 exactly: 0033 revoked
-- CREATE ON SCHEMA public from app_erasure and app_erasure's own membership
-- of CURRENT_USER at its own COMMIT, so both must be re-established here to
-- create a new function owned by app_erasure and to replace two functions
-- owned by app_integrity_guard. Both are revoked again before this COMMIT.
-- ---------------------------------------------------------------------------

GRANT app_erasure TO CURRENT_USER WITH INHERIT TRUE, SET TRUE;
GRANT CREATE ON SCHEMA public TO app_erasure;
-- Needed only to CREATE OR REPLACE the three guard functions below (all
-- owned by app_integrity_guard); they already exist, so no CREATE privilege
-- on the schema is required, only membership for the ownership check.
GRANT app_integrity_guard TO CURRENT_USER WITH INHERIT TRUE, SET TRUE;

-- ---------------------------------------------------------------------------
-- Least-privilege table access for app_erasure: tables 0033 did not already
-- grant SELECT+DELETE on. Column-privilege grants are additive, so re-
-- granting SELECT on a table that already has it (e.g. audit_events, which
-- 0033 granted INSERT on) is harmless.
-- ---------------------------------------------------------------------------

-- Brand-new tables for app_erasure (also need a new FORCE-RLS policy below).
GRANT SELECT, DELETE ON
    public.workspace_memberships, public.workspace_invitations, public.command_receipts,
    public.suppression_sources, public.unsubscribe_tokens, public.controlled_send_authorizations,
    public.conversations, public.attempt_evidence, public.provider_receipts, public.safety_holds,
    public.domain_events, public.outbox_work, public.outbox_deliveries, public.consumer_receipts,
    public.notifications, public.notification_deliveries, public.capacity_debits, public.capacity_debit_scopes,
    public.workspace_bootstrap_receipts, public.personalization_research_cache, public.personalization_usage_daily,
    public.message_generation_attempts, public.workspaces
    TO app_erasure;

-- Tables 0033 already granted SELECT (and/or narrow UPDATE) on, for redaction
-- in place, but never DELETE (0033 never deletes these rows, only redacts or
-- reads them). They already have a permissive 0033 policy, so no new policy
-- is needed here.
GRANT SELECT, DELETE ON
    public.leads, public.recipient_addresses, public.suppressions, public.campaign_enrollments,
    public.messages, public.message_attempts, public.inbound_messages, public.inbound_outreach_links,
    public.recipient_outcomes, public.message_generations, public.message_events, public.audit_events
    TO app_erasure;

-- Additional columns on campaigns, beyond 0033's UPDATE (erased_at, draft_
-- sequence_id, draft_audience_id, current_settings_id): needed to null an
-- ACTIVATED campaign's forward references and force it back to a deletable
-- state (Finding A above).
GRANT UPDATE (activation_id, activated_sequence_id, activated_audience_id, status, archived_at)
    ON public.campaigns TO app_erasure;

-- SELECT ... FOR UPDATE needs some UPDATE privilege on the table, exactly
-- like 0033's identical comment for lead_lists/mailboxes. This command never
-- writes any column on workspaces; it locks the row, then deletes it.
GRANT UPDATE (version) ON public.workspaces TO app_erasure;

-- One permissive policy per brand-new FORCE ROW LEVEL SECURITY table: same
-- rationale as 0033's loop (the owner role would otherwise see zero rows;
-- the command enforces tenancy itself via an explicit workspace_id filter on
-- every statement).
DO $policies$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'workspace_memberships', 'workspace_invitations', 'command_receipts',
        'suppression_sources', 'unsubscribe_tokens', 'controlled_send_authorizations',
        'conversations', 'attempt_evidence', 'provider_receipts', 'safety_holds',
        'domain_events', 'outbox_work', 'outbox_deliveries', 'consumer_receipts',
        'notifications', 'notification_deliveries', 'capacity_debits', 'capacity_debit_scopes',
        'workspace_bootstrap_receipts', 'personalization_research_cache', 'personalization_usage_daily',
        'message_generation_attempts', 'workspaces'
    ] LOOP
        EXECUTE pg_catalog.format(
            'CREATE POLICY %I ON public.%I FOR ALL TO app_erasure USING (true) WITH CHECK (true)',
            t || '_erasure_all', t
        );
    END LOOP;
END;
$policies$;

-- ---------------------------------------------------------------------------
-- Guards: additive DELETE/UPDATE bypasses, each usable only from this command
-- (app_erasure + app.erasure='on'; only app_delete_workspace ever sets that
-- flag, and nobody is a login/runtime member of app_erasure). See Findings A
-- and B above for why each of these three is required.
-- ---------------------------------------------------------------------------

-- Finding A. Unchanged below the new first IF: same body as 0003.
CREATE OR REPLACE FUNCTION public.app_guard_campaign_snapshot() RETURNS trigger
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = ''
AS $function$
DECLARE selected_status text;
BEGIN
    -- Bypass keyed on a column marker, not CURRENT_USER: this function is
    -- SECURITY DEFINER, so CURRENT_USER inside its body is always the
    -- function's owner (app_integrity_guard), never the calling role --
    -- the CURRENT_USER-based idiom 0033 uses works only in its SECURITY
    -- INVOKER guards. Only app_erasure ever has UPDATE privilege on
    -- campaigns.erased_at (granted above), so NEW.erased_at being set is
    -- itself proof this statement came from app_delete_workspace, exactly
    -- the same marker app_guard_campaign_config (0033) already keys its own
    -- DELETE bypass on.
    IF TG_OP = 'UPDATE' AND NEW.erased_at IS NOT NULL THEN
        RETURN NEW;
    END IF;
    IF TG_OP = 'UPDATE' THEN
        IF OLD.activation_id IS NOT NULL AND
          (NEW.activation_id, NEW.activated_sequence_id, NEW.activated_audience_id, NEW.draft_sequence_id, NEW.draft_audience_id)
          IS DISTINCT FROM (OLD.activation_id, OLD.activated_sequence_id, OLD.activated_audience_id, OLD.draft_sequence_id, OLD.draft_audience_id) THEN
            RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Activated audience and content cannot change';
        END IF;
        IF OLD.activation_id IS NOT NULL AND OLD.status <> 'PAUSED' AND
          (NEW.current_settings_id, NEW.start_at) IS DISTINCT FROM (OLD.current_settings_id, OLD.start_at) THEN
            RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Pause before changing execution settings';
        END IF;
        IF (OLD.status = 'RUNNING' AND NEW.status = 'ARCHIVED') OR
           (OLD.status = 'COMPLETED' AND NEW.status NOT IN ('COMPLETED','ARCHIVED')) OR
           (OLD.status = 'ARCHIVED' AND NEW.status <> 'ARCHIVED') THEN
            RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Campaign requires pause or a new draft';
        END IF;
        IF NEW.status IS DISTINCT FROM OLD.status THEN NEW.previous_status := OLD.status; END IF;
    END IF;
    IF NEW.activation_id IS NOT NULL AND (TG_OP = 'INSERT' OR OLD.activation_id IS NULL) THEN
        SELECT status INTO STRICT selected_status FROM public.campaign_sequences
          WHERE workspace_id = NEW.workspace_id AND campaign_id = NEW.id AND id = NEW.activated_sequence_id FOR UPDATE;
        IF selected_status <> 'FROZEN' THEN RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Activation requires frozen sequence'; END IF;
        SELECT status INTO STRICT selected_status FROM public.campaign_audiences
          WHERE workspace_id = NEW.workspace_id AND campaign_id = NEW.id AND id = NEW.activated_audience_id FOR UPDATE;
        IF selected_status <> 'READY' THEN RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Activation requires ready audience'; END IF;
    END IF;
    RETURN NEW;
END;
$function$;

-- Finding B (1/2). Unchanged below the new first IF: same body as 0003.
CREATE OR REPLACE FUNCTION public.app_guard_frozen_sequence_steps() RETURNS trigger
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = ''
AS $function$
DECLARE
    tenant uuid := COALESCE(NEW.workspace_id, OLD.workspace_id);
    sequence_uuid uuid := COALESCE(NEW.sequence_id, OLD.sequence_id);
    campaign_uuid uuid := COALESCE(NEW.campaign_id, OLD.campaign_id);
    parent_status text;
    parent_erased timestamptz;
BEGIN
    -- Bypass keyed on the parent campaign's erased_at marker, not CURRENT_USER
    -- (broken under SECURITY DEFINER -- see app_guard_campaign_snapshot above).
    -- Mirrors app_guard_campaign_config's (0033) own DELETE bypass exactly.
    SELECT erased_at INTO parent_erased FROM public.campaigns
        WHERE workspace_id = tenant AND id = campaign_uuid FOR UPDATE;
    IF TG_OP = 'DELETE' AND parent_erased IS NOT NULL THEN
        RETURN OLD;
    END IF;
    SELECT status INTO STRICT parent_status FROM public.campaign_sequences
        WHERE workspace_id = tenant AND campaign_id = campaign_uuid AND id = sequence_uuid FOR UPDATE;
    IF parent_status <> 'DRAFT' THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Frozen sequence steps are immutable';
    END IF;
    RETURN COALESCE(NEW, OLD);
END;
$function$;

-- Finding B (2/2). Unchanged below the new first IF: same body as 0025.
CREATE OR REPLACE FUNCTION public.app_guard_frozen_step_attachments() RETURNS trigger
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = ''
AS $function$
DECLARE
    tenant uuid := COALESCE(NEW.workspace_id, OLD.workspace_id);
    sequence_uuid uuid := COALESCE(NEW.sequence_id, OLD.sequence_id);
    campaign_uuid uuid := COALESCE(NEW.campaign_id, OLD.campaign_id);
    parent_status text;
    parent_erased timestamptz;
BEGIN
    -- Bypass keyed on the parent campaign's erased_at marker, not CURRENT_USER
    -- (broken under SECURITY DEFINER -- see app_guard_campaign_snapshot above).
    -- Mirrors app_guard_campaign_config's (0033) own DELETE bypass exactly.
    SELECT erased_at INTO parent_erased FROM public.campaigns
        WHERE workspace_id = tenant AND id = campaign_uuid FOR UPDATE;
    IF TG_OP = 'DELETE' AND parent_erased IS NOT NULL THEN
        RETURN OLD;
    END IF;
    SELECT status INTO STRICT parent_status FROM public.campaign_sequences
        WHERE workspace_id = tenant AND campaign_id = campaign_uuid AND id = sequence_uuid FOR UPDATE;
    IF parent_status <> 'DRAFT' THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Frozen sequence attachments are immutable';
    END IF;
    RETURN COALESCE(NEW, OLD);
END;
$function$;

-- ---------------------------------------------------------------------------
-- The command
-- ---------------------------------------------------------------------------

CREATE FUNCTION public.app_delete_workspace(p_workspace uuid, p_confirmation_name text) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = ''
AS $function$
DECLARE
    actor uuid := public.app_current_user_id();
    ws public.workspaces%ROWTYPE;
    n bigint;
    counts jsonb := '{}'::jsonb;
BEGIN
    -- Authorization: inline, not app_erasure_authorize() (0033), which
    -- hardcodes 'workspace.manage' (ADMIN+OWNER) -- too permissive for
    -- destroying a whole workspace. Mirrors its shape with the existing
    -- OWNER-only 'ownership.manage' capability instead (same one transfer-
    -- ownership uses; app_has_permission()'s matrix in 0001 is unchanged).
    IF actor IS NULL OR p_workspace IS NULL
       OR p_workspace IS DISTINCT FROM public.app_current_workspace_id()
       OR NOT public.app_has_permission('ownership.manage') THEN
        RAISE EXCEPTION USING ERRCODE = '42501', MESSAGE = 'erasure:forbidden';
    END IF;

    SELECT * INTO ws FROM public.workspaces WHERE id = p_workspace FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = 'P0002', MESSAGE = 'erasure:not_found';
    END IF;

    -- Defense in depth: the API (backend/app/modules/erasure/service.py
    -- ErasureService.purge_workspace) already compares the typed
    -- confirmation to the workspace name before ever calling this function.
    IF p_confirmation_name IS DISTINCT FROM ws.name THEN
        RAISE EXCEPTION USING ERRCODE = '22023', MESSAGE = 'erasure:invalid',
            DETAIL = 'Typed confirmation does not match the workspace name.';
    END IF;

    -- Finding D: both app_require_workspace_owner constraint triggers are
    -- DEFERRABLE INITIALLY DEFERRED already; this only guards against some
    -- earlier statement in the same session/transaction having switched them
    -- to IMMEDIATE. Deleting every workspace_memberships row (including the
    -- OWNER's) and the workspaces row itself, both inside this transaction,
    -- is safe under DEFERRED timing: the check re-runs at COMMIT, by which
    -- point public.workspaces no longer contains this row at all.
    SET CONSTRAINTS public.workspaces_active_owner_required,
        public.workspace_memberships_active_owner_required DEFERRED;

    PERFORM pg_catalog.set_config('app.erasure', 'on', true);

    BEGIN
        -- -----------------------------------------------------------------
        -- Break circular FKs before any DELETE can reach the tables they
        -- point into (Finding A). erased_at mirrors app_purge_campaign/
        -- app_erase_campaign's own marker; status/archived_at additionally
        -- satisfy campaigns_activation_shape_check and campaigns_archive_
        -- check for a campaign that was ever activated.
        -- -----------------------------------------------------------------
        UPDATE public.campaigns
        SET erased_at = COALESCE(erased_at, pg_catalog.transaction_timestamp()),
            activation_id = NULL, activated_sequence_id = NULL, activated_audience_id = NULL,
            draft_sequence_id = NULL, draft_audience_id = NULL, current_settings_id = NULL,
            status = 'DRAFT', archived_at = NULL
        WHERE workspace_id = p_workspace;

        UPDATE public.templates SET current_version_id = NULL WHERE workspace_id = p_workspace;

        -- Release every held audience-capture gate workspace-wide, exactly
        -- like app_purge_campaign does per-campaign: fires audience_capture_
        -- sources_counter and drives lead_lists.capture_count back to 0, so
        -- lead_list_memberships can be deleted below without tripping
        -- app_guard_list_membership.
        UPDATE public.audience_capture_sources
        SET gate_held = false, released_at = pg_catalog.transaction_timestamp()
        WHERE workspace_id = p_workspace AND gate_held;

        -- -----------------------------------------------------------------
        -- Deletes, in FK-safe (children-before-parents) order.
        -- -----------------------------------------------------------------

        DELETE FROM public.personalization_previews WHERE workspace_id = p_workspace;
        GET DIAGNOSTICS n = ROW_COUNT; counts := counts || pg_catalog.jsonb_build_object('personalization_previews', n);
        DELETE FROM public.campaign_personalization_approvals WHERE workspace_id = p_workspace;
        GET DIAGNOSTICS n = ROW_COUNT; counts := counts || pg_catalog.jsonb_build_object('campaign_personalization_approvals', n);
        DELETE FROM public.message_events WHERE workspace_id = p_workspace;
        GET DIAGNOSTICS n = ROW_COUNT; counts := counts || pg_catalog.jsonb_build_object('message_events', n);
        DELETE FROM public.campaign_step_attachments WHERE workspace_id = p_workspace;
        GET DIAGNOSTICS n = ROW_COUNT; counts := counts || pg_catalog.jsonb_build_object('campaign_step_attachments', n);
        DELETE FROM public.attempt_evidence WHERE workspace_id = p_workspace;
        GET DIAGNOSTICS n = ROW_COUNT; counts := counts || pg_catalog.jsonb_build_object('attempt_evidence', n);
        DELETE FROM public.inbound_outreach_links WHERE workspace_id = p_workspace;
        GET DIAGNOSTICS n = ROW_COUNT; counts := counts || pg_catalog.jsonb_build_object('inbound_outreach_links', n);
        DELETE FROM public.recipient_outcomes WHERE workspace_id = p_workspace;
        GET DIAGNOSTICS n = ROW_COUNT; counts := counts || pg_catalog.jsonb_build_object('recipient_outcomes', n);
        DELETE FROM public.unsubscribe_tokens WHERE workspace_id = p_workspace;
        GET DIAGNOSTICS n = ROW_COUNT; counts := counts || pg_catalog.jsonb_build_object('unsubscribe_tokens', n);
        DELETE FROM public.import_row_results WHERE workspace_id = p_workspace;
        GET DIAGNOSTICS n = ROW_COUNT; counts := counts || pg_catalog.jsonb_build_object('import_row_results', n);
        DELETE FROM public.lead_list_memberships WHERE workspace_id = p_workspace;
        GET DIAGNOSTICS n = ROW_COUNT; counts := counts || pg_catalog.jsonb_build_object('lead_list_memberships', n);
        DELETE FROM public.audience_capture_sources WHERE workspace_id = p_workspace;
        GET DIAGNOSTICS n = ROW_COUNT; counts := counts || pg_catalog.jsonb_build_object('audience_capture_sources', n);
        DELETE FROM public.campaign_planning_jobs WHERE workspace_id = p_workspace;
        GET DIAGNOSTICS n = ROW_COUNT; counts := counts || pg_catalog.jsonb_build_object('campaign_planning_jobs', n);
        DELETE FROM public.oauth_flows WHERE workspace_id = p_workspace;
        GET DIAGNOSTICS n = ROW_COUNT; counts := counts || pg_catalog.jsonb_build_object('oauth_flows', n);
        DELETE FROM public.mailbox_sync_states WHERE workspace_id = p_workspace;
        GET DIAGNOSTICS n = ROW_COUNT; counts := counts || pg_catalog.jsonb_build_object('mailbox_sync_states', n);
        DELETE FROM public.safety_holds WHERE workspace_id = p_workspace;
        GET DIAGNOSTICS n = ROW_COUNT; counts := counts || pg_catalog.jsonb_build_object('safety_holds', n);
        DELETE FROM public.outbox_deliveries WHERE workspace_id = p_workspace;
        GET DIAGNOSTICS n = ROW_COUNT; counts := counts || pg_catalog.jsonb_build_object('outbox_deliveries', n);
        DELETE FROM public.consumer_receipts WHERE workspace_id = p_workspace;
        GET DIAGNOSTICS n = ROW_COUNT; counts := counts || pg_catalog.jsonb_build_object('consumer_receipts', n);
        DELETE FROM public.notification_deliveries WHERE workspace_id = p_workspace;
        GET DIAGNOSTICS n = ROW_COUNT; counts := counts || pg_catalog.jsonb_build_object('notification_deliveries', n);
        DELETE FROM public.capacity_debit_scopes WHERE workspace_id = p_workspace;
        GET DIAGNOSTICS n = ROW_COUNT; counts := counts || pg_catalog.jsonb_build_object('capacity_debit_scopes', n);
        DELETE FROM public.workspace_bootstrap_receipts WHERE workspace_id = p_workspace;
        GET DIAGNOSTICS n = ROW_COUNT; counts := counts || pg_catalog.jsonb_build_object('workspace_bootstrap_receipts', n);
        DELETE FROM public.personalization_research_cache WHERE workspace_id = p_workspace;
        GET DIAGNOSTICS n = ROW_COUNT; counts := counts || pg_catalog.jsonb_build_object('personalization_research_cache', n);
        DELETE FROM public.personalization_usage_daily WHERE workspace_id = p_workspace;
        GET DIAGNOSTICS n = ROW_COUNT; counts := counts || pg_catalog.jsonb_build_object('personalization_usage_daily', n);
        DELETE FROM public.message_generation_attempts WHERE workspace_id = p_workspace;
        GET DIAGNOSTICS n = ROW_COUNT; counts := counts || pg_catalog.jsonb_build_object('message_generation_attempts', n);
        DELETE FROM public.suppression_sources WHERE workspace_id = p_workspace;
        GET DIAGNOSTICS n = ROW_COUNT; counts := counts || pg_catalog.jsonb_build_object('suppression_sources', n);
        DELETE FROM public.workspace_invitations WHERE workspace_id = p_workspace;
        GET DIAGNOSTICS n = ROW_COUNT; counts := counts || pg_catalog.jsonb_build_object('workspace_invitations', n);
        DELETE FROM public.campaign_settings_versions WHERE workspace_id = p_workspace;
        GET DIAGNOSTICS n = ROW_COUNT; counts := counts || pg_catalog.jsonb_build_object('campaign_settings_versions', n);

        DELETE FROM public.suppressions WHERE workspace_id = p_workspace;
        GET DIAGNOSTICS n = ROW_COUNT; counts := counts || pg_catalog.jsonb_build_object('suppressions', n);
        DELETE FROM public.import_jobs WHERE workspace_id = p_workspace;
        GET DIAGNOSTICS n = ROW_COUNT; counts := counts || pg_catalog.jsonb_build_object('import_jobs', n);
        DELETE FROM public.inbound_messages WHERE workspace_id = p_workspace;
        GET DIAGNOSTICS n = ROW_COUNT; counts := counts || pg_catalog.jsonb_build_object('inbound_messages', n);
        DELETE FROM public.provider_receipts WHERE workspace_id = p_workspace;
        GET DIAGNOSTICS n = ROW_COUNT; counts := counts || pg_catalog.jsonb_build_object('provider_receipts', n);
        DELETE FROM public.outbox_work WHERE workspace_id = p_workspace;
        GET DIAGNOSTICS n = ROW_COUNT; counts := counts || pg_catalog.jsonb_build_object('outbox_work', n);
        DELETE FROM public.notifications WHERE workspace_id = p_workspace;
        GET DIAGNOSTICS n = ROW_COUNT; counts := counts || pg_catalog.jsonb_build_object('notifications', n);
        DELETE FROM public.tenant_rate_policies WHERE workspace_id = p_workspace;
        GET DIAGNOSTICS n = ROW_COUNT; counts := counts || pg_catalog.jsonb_build_object('tenant_rate_policies', n);
        DELETE FROM public.capacity_debits WHERE workspace_id = p_workspace;
        GET DIAGNOSTICS n = ROW_COUNT; counts := counts || pg_catalog.jsonb_build_object('capacity_debits', n);
        DELETE FROM public.message_generations WHERE workspace_id = p_workspace;
        GET DIAGNOSTICS n = ROW_COUNT; counts := counts || pg_catalog.jsonb_build_object('message_generations', n);

        DELETE FROM public.domain_events WHERE workspace_id = p_workspace;
        GET DIAGNOSTICS n = ROW_COUNT; counts := counts || pg_catalog.jsonb_build_object('domain_events', n);
        DELETE FROM public.lead_lists WHERE workspace_id = p_workspace;
        GET DIAGNOSTICS n = ROW_COUNT; counts := counts || pg_catalog.jsonb_build_object('lead_lists', n);
        DELETE FROM public.message_attempts WHERE workspace_id = p_workspace;
        GET DIAGNOSTICS n = ROW_COUNT; counts := counts || pg_catalog.jsonb_build_object('message_attempts', n);

        DELETE FROM public.mailbox_connections WHERE workspace_id = p_workspace;
        GET DIAGNOSTICS n = ROW_COUNT; counts := counts || pg_catalog.jsonb_build_object('mailbox_connections', n);
        DELETE FROM public.messages WHERE workspace_id = p_workspace;
        GET DIAGNOSTICS n = ROW_COUNT; counts := counts || pg_catalog.jsonb_build_object('messages', n);

        DELETE FROM public.campaign_enrollments WHERE workspace_id = p_workspace;
        GET DIAGNOSTICS n = ROW_COUNT; counts := counts || pg_catalog.jsonb_build_object('campaign_enrollments', n);
        DELETE FROM public.controlled_send_authorizations WHERE workspace_id = p_workspace;
        GET DIAGNOSTICS n = ROW_COUNT; counts := counts || pg_catalog.jsonb_build_object('controlled_send_authorizations', n);
        DELETE FROM public.conversations WHERE workspace_id = p_workspace;
        GET DIAGNOSTICS n = ROW_COUNT; counts := counts || pg_catalog.jsonb_build_object('conversations', n);

        DELETE FROM public.sequence_steps WHERE workspace_id = p_workspace;
        GET DIAGNOSTICS n = ROW_COUNT; counts := counts || pg_catalog.jsonb_build_object('sequence_steps', n);
        DELETE FROM public.campaign_audience_members WHERE workspace_id = p_workspace;
        GET DIAGNOSTICS n = ROW_COUNT; counts := counts || pg_catalog.jsonb_build_object('campaign_audience_members', n);
        DELETE FROM public.campaign_mailboxes WHERE workspace_id = p_workspace;
        GET DIAGNOSTICS n = ROW_COUNT; counts := counts || pg_catalog.jsonb_build_object('campaign_mailboxes', n);

        DELETE FROM public.template_versions WHERE workspace_id = p_workspace;
        GET DIAGNOSTICS n = ROW_COUNT; counts := counts || pg_catalog.jsonb_build_object('template_versions', n);
        DELETE FROM public.campaign_sequences WHERE workspace_id = p_workspace;
        GET DIAGNOSTICS n = ROW_COUNT; counts := counts || pg_catalog.jsonb_build_object('campaign_sequences', n);
        DELETE FROM public.campaign_audiences WHERE workspace_id = p_workspace;
        GET DIAGNOSTICS n = ROW_COUNT; counts := counts || pg_catalog.jsonb_build_object('campaign_audiences', n);
        DELETE FROM public.mailboxes WHERE workspace_id = p_workspace;
        GET DIAGNOSTICS n = ROW_COUNT; counts := counts || pg_catalog.jsonb_build_object('mailboxes', n);
        DELETE FROM public.leads WHERE workspace_id = p_workspace;
        GET DIAGNOSTICS n = ROW_COUNT; counts := counts || pg_catalog.jsonb_build_object('leads', n);
        DELETE FROM public.recipient_addresses WHERE workspace_id = p_workspace;
        GET DIAGNOSTICS n = ROW_COUNT; counts := counts || pg_catalog.jsonb_build_object('recipient_addresses', n);

        DELETE FROM public.campaigns WHERE workspace_id = p_workspace;
        GET DIAGNOSTICS n = ROW_COUNT; counts := counts || pg_catalog.jsonb_build_object('campaigns', n);
        DELETE FROM public.templates WHERE workspace_id = p_workspace;
        GET DIAGNOSTICS n = ROW_COUNT; counts := counts || pg_catalog.jsonb_build_object('templates', n);

        -- Tombstone written before audit_events itself is wiped below (see
        -- the open question in the header comment: it does not survive).
        PERFORM public.app_erasure_audit(
            p_workspace, actor, 'workspace.delete', 'workspace', p_workspace,
            pg_catalog.jsonb_build_object('name', ws.name), counts);

        DELETE FROM public.audit_events WHERE workspace_id = p_workspace;
        GET DIAGNOSTICS n = ROW_COUNT; counts := counts || pg_catalog.jsonb_build_object('audit_events', n);
        DELETE FROM public.workspace_memberships WHERE workspace_id = p_workspace;
        GET DIAGNOSTICS n = ROW_COUNT; counts := counts || pg_catalog.jsonb_build_object('workspace_memberships', n);
        DELETE FROM public.command_receipts WHERE workspace_id = p_workspace;
        GET DIAGNOSTICS n = ROW_COUNT; counts := counts || pg_catalog.jsonb_build_object('command_receipts', n);

        DELETE FROM public.workspaces WHERE id = p_workspace;
        GET DIAGNOSTICS n = ROW_COUNT; counts := counts || pg_catalog.jsonb_build_object('workspaces', n);
    EXCEPTION
        WHEN foreign_key_violation OR check_violation THEN
            RAISE EXCEPTION USING ERRCODE = '55006', MESSAGE = 'erasure:in_use',
                DETAIL = 'This workspace still has a row referenced from outside the deleted set. Try again, or contact support.';
    END;

    PERFORM pg_catalog.set_config('app.erasure', 'off', true);
    RETURN pg_catalog.jsonb_build_object('deleted', counts);
END;
$function$;

-- ---------------------------------------------------------------------------
-- Ownership and grants
-- ---------------------------------------------------------------------------

ALTER FUNCTION public.app_delete_workspace(uuid, text) OWNER TO app_erasure;

REVOKE ALL ON FUNCTION public.app_delete_workspace(uuid, text) FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.app_delete_workspace(uuid, text) TO app_api;

-- app_current_user_id/app_current_workspace_id/app_has_permission and
-- app_erasure_audit are already executable by app_erasure (granted in 0033);
-- no new EXECUTE grants are required for this command to call them.

REVOKE CREATE ON SCHEMA public FROM app_erasure;
REVOKE app_erasure FROM CURRENT_USER;
REVOKE app_integrity_guard FROM CURRENT_USER;

DO $postflight$
BEGIN
    IF pg_catalog.has_schema_privilege('app_erasure', 'public', 'CREATE') THEN
        RAISE EXCEPTION '0034 must not leave app_erasure with CREATE on schema public';
    END IF;
    IF pg_catalog.pg_has_role('app_api', 'app_erasure', 'MEMBER')
       OR pg_catalog.pg_has_role('app_worker_send', 'app_erasure', 'MEMBER')
       OR pg_catalog.pg_has_role('app_worker_general', 'app_erasure', 'MEMBER') THEN
        RAISE EXCEPTION '0034 forbids runtime membership in app_erasure';
    END IF;
    IF NOT pg_catalog.has_function_privilege('app_api', 'public.app_delete_workspace(uuid,text)', 'EXECUTE') THEN
        RAISE EXCEPTION '0034 postcondition failed: app_api must be able to execute app_delete_workspace';
    END IF;
    IF pg_catalog.has_function_privilege('app_worker_send', 'public.app_delete_workspace(uuid,text)', 'EXECUTE')
       OR pg_catalog.has_function_privilege('app_worker_general', 'public.app_delete_workspace(uuid,text)', 'EXECUTE')
       OR pg_catalog.has_function_privilege('anon', 'public.app_delete_workspace(uuid,text)', 'EXECUTE')
       OR pg_catalog.has_function_privilege('authenticated', 'public.app_delete_workspace(uuid,text)', 'EXECUTE')
       OR pg_catalog.has_function_privilege('service_role', 'public.app_delete_workspace(uuid,text)', 'EXECUTE') THEN
        RAISE EXCEPTION '0034 postcondition failed: only app_api may execute app_delete_workspace';
    END IF;
    IF (SELECT pg_catalog.pg_get_userbyid(proowner) FROM pg_catalog.pg_proc
        WHERE oid = 'public.app_guard_campaign_snapshot()'::pg_catalog.regprocedure) <> 'app_integrity_guard'
       OR (SELECT pg_catalog.pg_get_userbyid(proowner) FROM pg_catalog.pg_proc
        WHERE oid = 'public.app_guard_frozen_sequence_steps()'::pg_catalog.regprocedure) <> 'app_integrity_guard'
       OR (SELECT pg_catalog.pg_get_userbyid(proowner) FROM pg_catalog.pg_proc
        WHERE oid = 'public.app_guard_frozen_step_attachments()'::pg_catalog.regprocedure) <> 'app_integrity_guard' THEN
        RAISE EXCEPTION '0034 postcondition failed: a replaced guard function changed owner';
    END IF;
END;
$postflight$;

COMMIT;
