-- Attachments and inline images for sequence email steps. Forward migration per
-- CLAUDE.md/AGENTS.md.
--
-- Migration review
--   Purpose:        let an EMAIL step carry file attachments and inline images.
--                   Files live in a private Supabase Storage bucket
--                   (email-attachments, created in the dashboard like the
--                   existing imports bucket -- see DEPLOYMENT.md); this table is
--                   the metadata + integrity record the API and the send worker
--                   read.
--   Creates:        public.campaign_step_attachments and its guard function
--                   app_guard_frozen_step_attachments().
--   Modifies:       nothing existing.
--   Deletes:        nothing.
--   Constraints:    size 1..2.5 MiB, content-type allow-list, sha256 shape,
--                   filename/storage-key shape, unique content id per step,
--                   unique (step, sha256, disposition) so a repeated upload of
--                   the same file is a no-op.
--   Indexes:        by step (send-time load, API listing) and by storage key
--                   (reference counting when a step or its copies are deleted).
--   RLS/security:   enabled and forced. app_api may SELECT (product.read) and
--                   INSERT/DELETE (campaigns.draft); there is no UPDATE, so a
--                   row is immutable. app_worker_send may SELECT (the send
--                   worker downloads a step's attachments). A trigger rejects
--                   any change once the parent sequence is frozen, exactly like
--                   sequence_steps.
--   Existing data:  none touched (new table).
--   App impact:     the backend/workers that read or write attachments need this
--                   table, so apply it BEFORE deploying them.
--   Risk:           low; additive.
--   Rollback:       DROP TABLE public.campaign_step_attachments; DROP FUNCTION
--                   public.app_guard_frozen_step_attachments(). Storage objects
--                   are not touched by the migration.
--
-- PREPARED ONLY. Do not apply to any shared/staging/production environment
-- without the project owner's separate review and explicit authorization.

BEGIN;
SET LOCAL search_path = '';

DO $preflight$
BEGIN
    IF pg_catalog.to_regclass('public.sequence_steps') IS NULL THEN
        RAISE EXCEPTION '0025 requires 0003_mailboxes_campaigns.sql to already be applied';
    END IF;
    IF pg_catalog.to_regclass('public.campaign_step_attachments') IS NOT NULL THEN
        RAISE EXCEPTION '0025 already applied: campaign_step_attachments exists';
    END IF;
END;
$preflight$;

CREATE TABLE public.campaign_step_attachments (
    id uuid DEFAULT pg_catalog.gen_random_uuid() CONSTRAINT campaign_step_attachments_pkey PRIMARY KEY,
    workspace_id uuid NOT NULL,
    campaign_id uuid NOT NULL,
    sequence_id uuid NOT NULL,
    step_id uuid NOT NULL,
    disposition text NOT NULL,
    -- Stable token used in the body as <img src="cid:CONTENT_ID">. Kept separate
    -- from id so a copied row (duplicate step/campaign) keeps the same token and
    -- the copied body needs no rewriting.
    content_id text NOT NULL,
    storage_key text NOT NULL,
    filename text NOT NULL,
    content_type text NOT NULL,
    size_bytes integer NOT NULL,
    sha256 text NOT NULL,
    created_by uuid,
    created_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    CONSTRAINT campaign_step_attachments_workspace_fkey FOREIGN KEY (workspace_id)
        REFERENCES public.workspaces (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    -- Composite FK keeps the row inside its own tenant/campaign/sequence. Step
    -- deletion (draft only) removes its attachment rows.
    CONSTRAINT campaign_step_attachments_step_fkey FOREIGN KEY (workspace_id, sequence_id, step_id)
        REFERENCES public.sequence_steps (workspace_id, sequence_id, id) ON UPDATE RESTRICT ON DELETE CASCADE,
    CONSTRAINT campaign_step_attachments_campaign_fkey FOREIGN KEY (workspace_id, campaign_id)
        REFERENCES public.campaigns (workspace_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT campaign_step_attachments_workspace_id_key UNIQUE (workspace_id, id),
    CONSTRAINT campaign_step_attachments_content_id_key UNIQUE (workspace_id, step_id, content_id),
    CONSTRAINT campaign_step_attachments_content_key UNIQUE (workspace_id, step_id, sha256, disposition),
    CONSTRAINT campaign_step_attachments_disposition_check CHECK (disposition IN ('ATTACHMENT', 'INLINE')),
    CONSTRAINT campaign_step_attachments_content_id_check CHECK (content_id ~ '^[A-Za-z0-9_-]{8,64}$'),
    CONSTRAINT campaign_step_attachments_storage_key_check CHECK (
        pg_catalog.char_length(storage_key) BETWEEN 1 AND 512
        AND storage_key !~ '(^/|\.\.|[[:cntrl:]])'
    ),
    CONSTRAINT campaign_step_attachments_filename_check CHECK (
        pg_catalog.char_length(filename) BETWEEN 1 AND 255
        AND filename !~ '[[:cntrl:]/\\]'
    ),
    CONSTRAINT campaign_step_attachments_content_type_check CHECK (content_type IN (
        'image/png', 'image/jpeg', 'image/gif', 'image/webp',
        'application/pdf', 'text/plain', 'text/csv',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        'application/vnd.openxmlformats-officedocument.presentationml.presentation'
    )),
    CONSTRAINT campaign_step_attachments_inline_type_check CHECK (
        disposition = 'ATTACHMENT' OR content_type LIKE 'image/%'
    ),
    CONSTRAINT campaign_step_attachments_size_check CHECK (size_bytes BETWEEN 1 AND 2621440),
    CONSTRAINT campaign_step_attachments_sha256_check CHECK (sha256 ~ '^[0-9a-f]{64}$')
);

CREATE INDEX campaign_step_attachments_step_idx
    ON public.campaign_step_attachments (workspace_id, step_id, created_at);
CREATE INDEX campaign_step_attachments_storage_key_idx
    ON public.campaign_step_attachments (workspace_id, storage_key);

ALTER TABLE public.campaign_step_attachments ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.campaign_step_attachments FORCE ROW LEVEL SECURITY;

REVOKE ALL PRIVILEGES ON TABLE public.campaign_step_attachments FROM PUBLIC, anon, authenticated, service_role;

CREATE POLICY campaign_step_attachments_api_select ON public.campaign_step_attachments
FOR SELECT TO app_api USING (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('product.read'));
GRANT SELECT ON public.campaign_step_attachments TO app_api;

CREATE POLICY campaign_step_attachments_api_insert ON public.campaign_step_attachments
FOR INSERT TO app_api WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('campaigns.draft'));
GRANT INSERT (id, workspace_id, campaign_id, sequence_id, step_id, disposition, content_id, storage_key, filename, content_type, size_bytes, sha256, created_by) ON public.campaign_step_attachments TO app_api;

CREATE POLICY campaign_step_attachments_api_delete ON public.campaign_step_attachments
FOR DELETE TO app_api USING (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('campaigns.draft'));
GRANT DELETE ON public.campaign_step_attachments TO app_api;

CREATE POLICY campaign_step_attachments_worker_send_select ON public.campaign_step_attachments
FOR SELECT TO app_worker_send USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.campaign_step_attachments TO app_worker_send;

-- Frozen sequences are immutable, attachments included. Mirrors
-- app_guard_frozen_sequence_steps(): lock the campaign then the sequence, refuse
-- unless the sequence is still DRAFT.
CREATE FUNCTION public.app_guard_frozen_step_attachments() RETURNS trigger
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = ''
AS $function$
DECLARE
    tenant uuid := COALESCE(NEW.workspace_id, OLD.workspace_id);
    sequence_uuid uuid := COALESCE(NEW.sequence_id, OLD.sequence_id);
    campaign_uuid uuid := COALESCE(NEW.campaign_id, OLD.campaign_id);
    parent_status text;
BEGIN
    PERFORM 1 FROM public.campaigns WHERE workspace_id = tenant AND id = campaign_uuid FOR UPDATE;
    SELECT status INTO STRICT parent_status FROM public.campaign_sequences
        WHERE workspace_id = tenant AND campaign_id = campaign_uuid AND id = sequence_uuid FOR UPDATE;
    IF parent_status <> 'DRAFT' THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Frozen sequence attachments are immutable';
    END IF;
    RETURN COALESCE(NEW, OLD);
END;
$function$;
REVOKE ALL ON FUNCTION public.app_guard_frozen_step_attachments() FROM PUBLIC, anon, authenticated, service_role;

-- The migration identity creates the trigger after ownership transfer; the
-- temporary grants keep function execution closed to runtime/browser roles
-- (same sequence as the equivalent guard in 0003).
GRANT EXECUTE ON FUNCTION public.app_guard_frozen_step_attachments() TO CURRENT_USER;
GRANT app_integrity_guard TO CURRENT_USER;
GRANT CREATE ON SCHEMA public TO app_integrity_guard;
ALTER FUNCTION public.app_guard_frozen_step_attachments() OWNER TO app_integrity_guard;
REVOKE CREATE ON SCHEMA public FROM app_integrity_guard;

CREATE TRIGGER campaign_step_attachments_guard_frozen
    BEFORE INSERT OR UPDATE OR DELETE ON public.campaign_step_attachments
    FOR EACH ROW EXECUTE FUNCTION public.app_guard_frozen_step_attachments();
REVOKE EXECUTE ON FUNCTION public.app_guard_frozen_step_attachments() FROM CURRENT_USER;
REVOKE app_integrity_guard FROM CURRENT_USER;

DO $postcondition$
BEGIN
    IF pg_catalog.to_regclass('public.campaign_step_attachments') IS NULL THEN
        RAISE EXCEPTION '0025 postcondition failed: campaign_step_attachments missing';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_trigger
        WHERE tgrelid = 'public.campaign_step_attachments'::pg_catalog.regclass
          AND tgname = 'campaign_step_attachments_guard_frozen'
    ) THEN
        RAISE EXCEPTION '0025 postcondition failed: frozen guard trigger missing';
    END IF;
END;
$postcondition$;

COMMIT;
