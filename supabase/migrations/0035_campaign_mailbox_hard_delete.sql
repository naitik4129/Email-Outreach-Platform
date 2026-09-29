-- Hard delete of one archived campaign and of one disconnected mailbox, sent
-- history included (ADR-0015 amendment). Forward migration per CLAUDE.md/AGENTS.md.
--
-- Migration:      0035_campaign_mailbox_hard_delete.sql
-- Purpose:        0033 could only physically delete a campaign that never sent
--                 (app_purge_campaign) and only erase (redact) one that did, and
--                 could only remove a mailbox that never sent. The project owner
--                 asked to remove archived campaigns and disconnected mailboxes
--                 completely, history included. This adds two commands that do
--                 that for ONE campaign / ONE mailbox, reusing the mechanism
--                 0034 (app_delete_workspace) already proved: SECURITY DEFINER
--                 commands owned by app_erasure, the campaigns.erased_at marker
--                 to pass the campaign guard triggers, and no foreign key
--                 changed to CASCADE.
-- Creates:        app_delete_send_history(ws, messages[], inbound[])  internal
--                   helper, no runtime grant: deletes the given messages with
--                   everything that hangs off them, the given inbound messages,
--                   and conversations left empty.
--                 app_delete_campaign(ws, campaign)  DRAFT/ARCHIVED campaign.
--                 app_delete_mailbox(ws, mailbox)    DISCONNECTED mailbox.
--                 Both commands: SECURITY DEFINER, owner app_erasure, EXECUTE
--                 for app_api only, workspace.manage (ADMIN/OWNER) re-checked
--                 inside via app_erasure_authorize, one audit_events row each.
-- Modifies:       Two column-level grants for app_erasure only:
--                   UPDATE (campaign_summary_id) ON conversations
--                   UPDATE (assigned_mailbox_id) ON campaign_enrollments
--                 No trigger function, table, column, constraint or policy.
-- Deletes:        Nothing at migration time. The commands delete only when a
--                 user invokes them.
-- Refusals:       Both commands refuse (erasure:<code>, translated by the API)
--                   state        campaign not DRAFT/ARCHIVED; mailbox not
--                                DISCONNECTED
--                   in_use       mailbox used by a campaign that is not
--                                DRAFT/ARCHIVED; mailbox with an ACTIVE safety
--                                hold; anything still referenced elsewhere
--                   in_flight    a message QUEUED/SENDING/RETRY_SCHEDULED/
--                                UNKNOWN_OUTCOME or an attempt PREPARED/UNKNOWN
--                   recent_sends any send attempt in the last 25 hours (the
--                                rate controller's RECONSTRUCTION_LOOKBACK):
--                                deleting fresh capacity debits would make the
--                                shared rate windows under-count after a Redis
--                                rebuild.
-- What is kept:   leads, recipient_addresses and ALL suppressions (the opt-out
--                 record). Templates, lead lists, other campaigns, other
--                 mailboxes.
-- What is lost:   For the campaign: its sequence/steps/audience snapshot/
--                 settings, enrollments, every message (content, attempts,
--                 provider evidence, tracking events, generation records),
--                 replies that belong only to it, its attachments rows (the API
--                 removes the files afterwards). For the mailbox: every message
--                 sent from it, every reply received on it, its conversations,
--                 provider receipts and the suppression_sources rows that point
--                 at those receipts (the suppression itself stays ACTIVE), its
--                 credentials rows and sync state. Unsubscribe links inside
--                 already-sent emails stop resolving because their tokens are
--                 deleted with the message (a foreign key forces this); the
--                 recipient's existing suppression is unaffected.
-- Side effects:   Deleting a mailbox stamps campaigns.erased_at on ARCHIVED
--                 campaigns that used it: that column is the only marker the
--                 campaign_mailboxes guard honours, and only app_erasure can
--                 write it. Their enrollments lose assigned_mailbox_id, and
--                 their counts drop by the deleted messages.
-- Constraints/Indexes: none added.
-- RLS/security:   No new policy or role. app_erasure already has SELECT+DELETE
--                 and a permissive policy on every table used (0033, 0034).
--                 The commands take no actor argument and enforce tenancy with
--                 an explicit workspace_id filter on every statement.
-- Existing data:  Unchanged by applying this migration.
-- Application:    backend ErasureService.purge_campaign / purge_mailbox call
--                 the new commands. app_purge_campaign / app_purge_mailbox
--                 (0033) remain but are no longer called.
-- Risk:           High. Irreversible physical deletion of sent history. Unlike
--                 0033's erase it removes evidence the reconciliation and
--                 analytics paths rely on, which is the request. Mitigations:
--                 ADMIN/OWNER only, archive-first (campaigns) and
--                 disconnect-first (mailboxes) rules, in-flight and 25-hour
--                 recent-send refusals, typed confirmation checked in the API,
--                 one atomic transaction, audit row (counts only).
-- Recovery:       No undo for a delete. To remove the feature: DROP FUNCTION
--                 app_delete_campaign(uuid,uuid), app_delete_mailbox(uuid,uuid),
--                 app_delete_send_history(uuid,uuid[],uuid[]) and revoke the two
--                 column grants below.
-- Needs:          0033 and 0034 applied first.
--
-- PREPARED ONLY. Do not apply to any shared/staging/production environment
-- without the project owner's separate review and explicit authorization.

BEGIN;
SET LOCAL search_path = '';

DO $preflight$
BEGIN
    IF pg_catalog.to_regprocedure('public.app_delete_workspace(uuid,text)') IS NULL
       OR NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'app_erasure') THEN
        RAISE EXCEPTION '0035 requires 0033 and 0034 to already be applied';
    END IF;
    IF pg_catalog.to_regprocedure('public.app_delete_campaign(uuid,uuid)') IS NOT NULL THEN
        RAISE EXCEPTION '0035 already applied: app_delete_campaign exists';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'app_api') THEN
        RAISE EXCEPTION '0035 requires role app_api from 0001';
    END IF;
END;
$preflight$;

-- Temporary migration-only elevation, mirroring 0033/0034; revoked before COMMIT.
GRANT app_erasure TO CURRENT_USER WITH INHERIT TRUE, SET TRUE;
GRANT CREATE ON SCHEMA public TO app_erasure;

GRANT UPDATE (campaign_summary_id) ON public.conversations TO app_erasure;
GRANT UPDATE (assigned_mailbox_id) ON public.campaign_enrollments TO app_erasure;

-- ---------------------------------------------------------------------------
-- Internal helper: messages + everything hanging off them + given inbound
-- ---------------------------------------------------------------------------

CREATE FUNCTION public.app_delete_send_history(p_workspace uuid, p_messages uuid[], p_inbound uuid[])
RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path = ''
AS $function$
DECLARE
    n bigint;
    counts jsonb := '{}'::jsonb;
    atts uuid[];
    debs uuid[];
    gens uuid[];
    convs uuid[];
BEGIN
    convs := ARRAY(
        SELECT m.conversation_id FROM public.messages AS m
        WHERE m.workspace_id = p_workspace AND m.id = ANY (p_messages) AND m.conversation_id IS NOT NULL
        UNION
        SELECT i.conversation_id FROM public.inbound_messages AS i
        WHERE i.workspace_id = p_workspace AND i.id = ANY (p_inbound));
    atts := ARRAY(
        SELECT a.id FROM public.message_attempts AS a
        WHERE a.workspace_id = p_workspace AND a.message_id = ANY (p_messages));
    debs := ARRAY(
        SELECT d.id FROM public.capacity_debits AS d
        WHERE d.workspace_id = p_workspace AND d.attempt_id = ANY (atts));
    gens := ARRAY(
        SELECT g.id FROM public.message_generations AS g
        WHERE g.workspace_id = p_workspace AND g.message_id = ANY (p_messages));

    DELETE FROM public.message_events WHERE workspace_id = p_workspace AND message_id = ANY (p_messages);
    GET DIAGNOSTICS n = ROW_COUNT;
    counts := counts || pg_catalog.jsonb_build_object('message_events', n);
    DELETE FROM public.unsubscribe_tokens WHERE workspace_id = p_workspace AND message_id = ANY (p_messages);
    DELETE FROM public.message_generation_attempts WHERE workspace_id = p_workspace AND generation_id = ANY (gens);
    DELETE FROM public.message_generations WHERE workspace_id = p_workspace AND id = ANY (gens);
    DELETE FROM public.attempt_evidence WHERE workspace_id = p_workspace AND attempt_id = ANY (atts);
    DELETE FROM public.capacity_debit_scopes WHERE workspace_id = p_workspace AND debit_id = ANY (debs);
    DELETE FROM public.capacity_debits WHERE workspace_id = p_workspace AND id = ANY (debs);
    DELETE FROM public.inbound_outreach_links
    WHERE workspace_id = p_workspace
      AND (outbound_message_id = ANY (p_messages) OR inbound_message_id = ANY (p_inbound));
    DELETE FROM public.recipient_outcomes
    WHERE workspace_id = p_workspace AND inbound_message_id = ANY (p_inbound);
    DELETE FROM public.message_attempts WHERE workspace_id = p_workspace AND id = ANY (atts);
    GET DIAGNOSTICS n = ROW_COUNT;
    counts := counts || pg_catalog.jsonb_build_object('message_attempts', n);
    DELETE FROM public.messages WHERE workspace_id = p_workspace AND id = ANY (p_messages);
    GET DIAGNOSTICS n = ROW_COUNT;
    counts := counts || pg_catalog.jsonb_build_object('messages', n);
    DELETE FROM public.inbound_messages WHERE workspace_id = p_workspace AND id = ANY (p_inbound);
    GET DIAGNOSTICS n = ROW_COUNT;
    counts := counts || pg_catalog.jsonb_build_object('replies', n);

    DELETE FROM public.conversations AS c
    WHERE c.workspace_id = p_workspace AND c.id = ANY (convs)
      AND NOT EXISTS (SELECT 1 FROM public.messages AS m
                      WHERE m.workspace_id = p_workspace AND m.conversation_id = c.id)
      AND NOT EXISTS (SELECT 1 FROM public.inbound_messages AS i
                      WHERE i.workspace_id = p_workspace AND i.conversation_id = c.id);
    RETURN counts;
END;
$function$;

-- ---------------------------------------------------------------------------
-- Commands
-- ---------------------------------------------------------------------------

CREATE FUNCTION public.app_delete_campaign(p_workspace uuid, p_campaign uuid) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = ''
AS $function$
DECLARE
    actor uuid := public.app_erasure_authorize(p_workspace);
    camp public.campaigns%ROWTYPE;
    n bigint;
    counts jsonb := '{}'::jsonb;
    keys text[];
    msgs uuid[];
    enr uuid[];
    inb uuid[];
BEGIN
    SELECT * INTO camp FROM public.campaigns
    WHERE workspace_id = p_workspace AND id = p_campaign FOR UPDATE;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = 'P0002', MESSAGE = 'erasure:not_found';
    END IF;
    IF camp.status NOT IN ('DRAFT', 'ARCHIVED') THEN
        RAISE EXCEPTION USING ERRCODE = '55000', MESSAGE = 'erasure:state',
            DETAIL = 'Archive the campaign before deleting it.';
    END IF;

    msgs := ARRAY(
        SELECT m.id FROM public.messages AS m
        WHERE m.workspace_id = p_workspace AND m.campaign_id = p_campaign);
    enr := ARRAY(
        SELECT e.id FROM public.campaign_enrollments AS e
        WHERE e.workspace_id = p_workspace AND e.campaign_id = p_campaign);

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
    IF EXISTS (
        SELECT 1 FROM public.message_attempts AS a
        WHERE a.workspace_id = p_workspace AND a.message_id = ANY (msgs)
          AND a.started_at > pg_catalog.transaction_timestamp() - INTERVAL '25 hours'
    ) THEN
        RAISE EXCEPTION USING ERRCODE = '55006', MESSAGE = 'erasure:recent_sends',
            DETAIL = 'This campaign sent email in the last 25 hours. Try again later.';
    END IF;

    -- Replies that belong only to this campaign (no link to, and no outcome for,
    -- anything outside it).
    inb := ARRAY(
        SELECT c.i FROM (
            SELECT l.inbound_message_id AS i FROM public.inbound_outreach_links AS l
            WHERE l.workspace_id = p_workspace AND l.outbound_message_id = ANY (msgs)
            UNION
            SELECT o.inbound_message_id FROM public.recipient_outcomes AS o
            WHERE o.workspace_id = p_workspace AND o.enrollment_id = ANY (enr)
              AND o.inbound_message_id IS NOT NULL
        ) AS c
        WHERE NOT EXISTS (
                SELECT 1 FROM public.inbound_outreach_links AS l2
                WHERE l2.workspace_id = p_workspace AND l2.inbound_message_id = c.i
                  AND NOT (l2.outbound_message_id = ANY (msgs)))
          AND NOT EXISTS (
                SELECT 1 FROM public.recipient_outcomes AS o2
                WHERE o2.workspace_id = p_workspace AND o2.inbound_message_id = c.i
                  AND NOT (o2.enrollment_id = ANY (enr))));

    BEGIN
        keys := ARRAY(
            SELECT a.storage_key FROM public.campaign_step_attachments AS a
            WHERE a.workspace_id = p_workspace AND a.campaign_id = p_campaign);

        -- Planning jobs reference campaigns(ws, id, activation_id): they must go
        -- before activation_id is cleared below.
        DELETE FROM public.campaign_planning_jobs WHERE workspace_id = p_workspace AND campaign_id = p_campaign;

        -- Release any list capture gate through its counter trigger.
        UPDATE public.audience_capture_sources
        SET gate_held = false, released_at = pg_catalog.transaction_timestamp()
        WHERE workspace_id = p_workspace AND campaign_id = p_campaign AND gate_held;

        -- The marker lets the campaign guards allow the deletes below; the
        -- forward references are RESTRICT foreign keys from campaigns into its own
        -- children. status/archived_at satisfy campaigns_activation_shape_check
        -- and campaigns_archive_check (same UPDATE as app_delete_workspace).
        UPDATE public.campaigns
        SET erased_at = COALESCE(erased_at, pg_catalog.transaction_timestamp()),
            activation_id = NULL, activated_sequence_id = NULL, activated_audience_id = NULL,
            draft_sequence_id = NULL, draft_audience_id = NULL, current_settings_id = NULL,
            status = 'DRAFT', archived_at = NULL
        WHERE workspace_id = p_workspace AND id = p_campaign;

        DELETE FROM public.personalization_previews WHERE workspace_id = p_workspace AND campaign_id = p_campaign;
        DELETE FROM public.campaign_personalization_approvals WHERE workspace_id = p_workspace AND campaign_id = p_campaign;

        counts := counts || public.app_delete_send_history(p_workspace, msgs, inb);

        DELETE FROM public.recipient_outcomes WHERE workspace_id = p_workspace AND enrollment_id = ANY (enr);
        DELETE FROM public.campaign_enrollments WHERE workspace_id = p_workspace AND campaign_id = p_campaign;
        GET DIAGNOSTICS n = ROW_COUNT;
        counts := counts || pg_catalog.jsonb_build_object('enrollments', n);
        DELETE FROM public.campaign_step_attachments WHERE workspace_id = p_workspace AND campaign_id = p_campaign;
        GET DIAGNOSTICS n = ROW_COUNT;
        counts := counts || pg_catalog.jsonb_build_object('attachments', n);
        DELETE FROM public.sequence_steps WHERE workspace_id = p_workspace AND campaign_id = p_campaign;
        GET DIAGNOSTICS n = ROW_COUNT;
        counts := counts || pg_catalog.jsonb_build_object('steps', n);
        DELETE FROM public.campaign_audience_members WHERE workspace_id = p_workspace AND campaign_id = p_campaign;
        GET DIAGNOSTICS n = ROW_COUNT;
        counts := counts || pg_catalog.jsonb_build_object('audience_members', n);
        DELETE FROM public.audience_capture_sources WHERE workspace_id = p_workspace AND campaign_id = p_campaign;
        DELETE FROM public.campaign_mailboxes WHERE workspace_id = p_workspace AND campaign_id = p_campaign;
        DELETE FROM public.campaign_settings_versions WHERE workspace_id = p_workspace AND campaign_id = p_campaign;
        DELETE FROM public.campaign_sequences WHERE workspace_id = p_workspace AND campaign_id = p_campaign;
        DELETE FROM public.campaign_audiences WHERE workspace_id = p_workspace AND campaign_id = p_campaign;
        DELETE FROM public.tenant_rate_policies WHERE workspace_id = p_workspace AND campaign_id = p_campaign;
        -- A conversation shared with another campaign keeps existing; it just
        -- stops pointing at this one.
        UPDATE public.conversations SET campaign_summary_id = NULL
        WHERE workspace_id = p_workspace AND campaign_summary_id = p_campaign;
        DELETE FROM public.campaigns WHERE workspace_id = p_workspace AND id = p_campaign;
    EXCEPTION
        WHEN foreign_key_violation OR check_violation THEN
            RAISE EXCEPTION USING ERRCODE = '55006', MESSAGE = 'erasure:in_use',
                DETAIL = 'This campaign is still referenced by other records.';
    END;

    PERFORM public.app_erasure_audit(
        p_workspace, actor, 'campaign.delete', 'campaign', p_campaign,
        pg_catalog.jsonb_build_object('status', camp.status), counts);
    RETURN pg_catalog.jsonb_build_object('storage_keys', pg_catalog.to_jsonb(keys), 'deleted', counts);
END;
$function$;

CREATE FUNCTION public.app_delete_mailbox(p_workspace uuid, p_mailbox uuid) RETURNS jsonb
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = ''
AS $function$
DECLARE
    actor uuid := public.app_erasure_authorize(p_workspace);
    mb public.mailboxes%ROWTYPE;
    n bigint;
    counts jsonb := '{}'::jsonb;
    msgs uuid[];
    inb uuid[];
    receipts uuid[];
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
    IF EXISTS (
        SELECT 1 FROM public.campaign_mailboxes AS cm
        JOIN public.campaigns AS c ON c.workspace_id = cm.workspace_id AND c.id = cm.campaign_id
        WHERE cm.workspace_id = p_workspace AND cm.mailbox_id = p_mailbox
          AND c.status NOT IN ('DRAFT', 'ARCHIVED')
    ) THEN
        RAISE EXCEPTION USING ERRCODE = '55006', MESSAGE = 'erasure:in_use',
            DETAIL = 'A campaign that is not archived still uses this mailbox. Archive or remove it from that campaign first.';
    END IF;

    msgs := ARRAY(
        SELECT m.id FROM public.messages AS m
        WHERE m.workspace_id = p_workspace AND m.mailbox_id = p_mailbox);
    inb := ARRAY(
        SELECT i.id FROM public.inbound_messages AS i
        WHERE i.workspace_id = p_workspace AND i.mailbox_id = p_mailbox);
    receipts := ARRAY(
        SELECT r.id FROM public.provider_receipts AS r
        WHERE r.workspace_id = p_workspace AND r.mailbox_id = p_mailbox);

    IF EXISTS (
        SELECT 1 FROM public.messages AS m
        WHERE m.workspace_id = p_workspace AND m.id = ANY (msgs)
          AND m.status IN ('QUEUED', 'SENDING', 'RETRY_SCHEDULED', 'UNKNOWN_OUTCOME')
    ) OR EXISTS (
        SELECT 1 FROM public.message_attempts AS a
        WHERE a.workspace_id = p_workspace AND a.mailbox_id = p_mailbox
          AND a.evidence_state IN ('PREPARED', 'UNKNOWN')
    ) THEN
        RAISE EXCEPTION USING ERRCODE = '55006', MESSAGE = 'erasure:in_flight',
            DETAIL = 'A send is in flight or unresolved. Try again once it has been reconciled.';
    END IF;
    IF EXISTS (
        SELECT 1 FROM public.message_attempts AS a
        WHERE a.workspace_id = p_workspace AND a.mailbox_id = p_mailbox
          AND a.started_at > pg_catalog.transaction_timestamp() - INTERVAL '25 hours'
    ) THEN
        RAISE EXCEPTION USING ERRCODE = '55006', MESSAGE = 'erasure:recent_sends',
            DETAIL = 'This mailbox sent email in the last 25 hours. Try again later.';
    END IF;
    -- Deleting an ACTIVE hold would leave its pending_safety_count stale.
    IF EXISTS (
        SELECT 1 FROM public.safety_holds AS h
        WHERE h.workspace_id = p_workspace AND h.status = 'ACTIVE'
          AND (h.target_mailbox_id = p_mailbox OR h.source_receipt_id = ANY (receipts))
    ) THEN
        RAISE EXCEPTION USING ERRCODE = '55006', MESSAGE = 'erasure:in_use',
            DETAIL = 'This mailbox has an unresolved safety hold. Resolve it first.';
    END IF;

    BEGIN
        -- Archived campaigns that used this mailbox: the erased_at marker is the
        -- only thing the campaign_mailboxes guard accepts for a DELETE.
        UPDATE public.campaigns
        SET erased_at = pg_catalog.transaction_timestamp()
        WHERE workspace_id = p_workspace AND status = 'ARCHIVED' AND erased_at IS NULL
          AND id IN (SELECT cm.campaign_id FROM public.campaign_mailboxes AS cm
                     WHERE cm.workspace_id = p_workspace AND cm.mailbox_id = p_mailbox);

        counts := counts || public.app_delete_send_history(p_workspace, msgs, inb);

        DELETE FROM public.conversations WHERE workspace_id = p_workspace AND mailbox_id = p_mailbox;
        DELETE FROM public.safety_holds
        WHERE workspace_id = p_workspace
          AND (target_mailbox_id = p_mailbox OR source_receipt_id = ANY (receipts));
        DELETE FROM public.suppression_sources
        WHERE workspace_id = p_workspace AND provider_receipt_id = ANY (receipts);
        DELETE FROM public.provider_receipts WHERE workspace_id = p_workspace AND id = ANY (receipts);
        DELETE FROM public.controlled_send_authorizations WHERE workspace_id = p_workspace AND mailbox_id = p_mailbox;

        UPDATE public.campaign_enrollments SET assigned_mailbox_id = NULL
        WHERE workspace_id = p_workspace AND assigned_mailbox_id = p_mailbox;
        DELETE FROM public.campaign_mailboxes WHERE workspace_id = p_workspace AND mailbox_id = p_mailbox;
        DELETE FROM public.tenant_rate_policies WHERE workspace_id = p_workspace AND mailbox_id = p_mailbox;
        DELETE FROM public.mailbox_sync_states WHERE workspace_id = p_workspace AND mailbox_id = p_mailbox;
        DELETE FROM public.oauth_flows WHERE workspace_id = p_workspace AND resulting_mailbox_id = p_mailbox;
        DELETE FROM public.mailbox_connections WHERE workspace_id = p_workspace AND mailbox_id = p_mailbox;
        DELETE FROM public.mailboxes WHERE workspace_id = p_workspace AND id = p_mailbox;
        GET DIAGNOSTICS n = ROW_COUNT;
    EXCEPTION
        WHEN foreign_key_violation OR check_violation THEN
            RAISE EXCEPTION USING ERRCODE = '55006', MESSAGE = 'erasure:in_use',
                DETAIL = 'This mailbox is still referenced by other records.';
    END;

    PERFORM public.app_erasure_audit(
        p_workspace, actor, 'mailbox.delete', 'mailbox', p_mailbox, NULL, counts);
    RETURN pg_catalog.jsonb_build_object('deleted', counts);
END;
$function$;

-- ---------------------------------------------------------------------------
-- Ownership and grants
-- ---------------------------------------------------------------------------

ALTER FUNCTION public.app_delete_send_history(uuid, uuid[], uuid[]) OWNER TO app_erasure;
ALTER FUNCTION public.app_delete_campaign(uuid, uuid) OWNER TO app_erasure;
ALTER FUNCTION public.app_delete_mailbox(uuid, uuid) OWNER TO app_erasure;

REVOKE ALL ON FUNCTION
    public.app_delete_send_history(uuid, uuid[], uuid[]),
    public.app_delete_campaign(uuid, uuid),
    public.app_delete_mailbox(uuid, uuid)
    FROM PUBLIC, anon, authenticated, service_role;

GRANT EXECUTE ON FUNCTION
    public.app_delete_campaign(uuid, uuid),
    public.app_delete_mailbox(uuid, uuid)
    TO app_api;
GRANT EXECUTE ON FUNCTION public.app_delete_send_history(uuid, uuid[], uuid[]) TO app_erasure;

REVOKE CREATE ON SCHEMA public FROM app_erasure;
REVOKE app_erasure FROM CURRENT_USER;

DO $postflight$
BEGIN
    IF pg_catalog.has_schema_privilege('app_erasure', 'public', 'CREATE') THEN
        RAISE EXCEPTION '0035 must not leave app_erasure with CREATE on schema public';
    END IF;
    IF pg_catalog.pg_has_role('app_api', 'app_erasure', 'MEMBER')
       OR pg_catalog.pg_has_role('app_worker_send', 'app_erasure', 'MEMBER')
       OR pg_catalog.pg_has_role('app_worker_general', 'app_erasure', 'MEMBER') THEN
        RAISE EXCEPTION '0035 forbids runtime membership in app_erasure';
    END IF;
    IF NOT pg_catalog.has_function_privilege('app_api', 'public.app_delete_campaign(uuid,uuid)', 'EXECUTE')
       OR NOT pg_catalog.has_function_privilege('app_api', 'public.app_delete_mailbox(uuid,uuid)', 'EXECUTE')
       OR pg_catalog.has_function_privilege('app_api', 'public.app_delete_send_history(uuid,uuid[],uuid[])', 'EXECUTE')
       OR pg_catalog.has_function_privilege('app_worker_send', 'public.app_delete_campaign(uuid,uuid)', 'EXECUTE')
       OR pg_catalog.has_function_privilege('app_worker_general', 'public.app_delete_mailbox(uuid,uuid)', 'EXECUTE') THEN
        RAISE EXCEPTION '0035 postcondition failed: only app_api may execute the two commands, and nobody the helper';
    END IF;
END;
$postflight$;

COMMIT;
