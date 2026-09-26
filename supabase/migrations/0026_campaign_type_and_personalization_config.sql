-- Hyper-personalized campaign type and objective. Forward migration per
-- CLAUDE.md/AGENTS.md. See docs/adr/0011-hyper-personalized-campaign-type.md.
--
-- Migration review
--   Purpose:        let a campaign be created as STANDARD (every existing
--                   campaign) or HYPER_PERSONALIZED, and let a sequence carry the
--                   campaign objective used to generate per-recipient emails.
--   Creates:        campaigns.campaign_type (text NOT NULL DEFAULT 'STANDARD'),
--                   campaign_sequences.personalization_config (jsonb NULL), one
--                   CHECK on each.
--   Modifies:       column-level grants for app_api (0003 lists writable columns
--                   explicitly, so new columns need their own grants):
--                   INSERT (campaign_type) on campaigns; INSERT and UPDATE
--                   (personalization_config) on campaign_sequences.
--   Deletes:        nothing.
--   Constraints:    campaign_type IN ('STANDARD','HYPER_PERSONALIZED');
--                   personalization_config must be a JSON object of at most
--                   16384 bytes.
--   Indexes:        none.
--   RLS/security:   unchanged. Existing row policies are row-level and already
--                   cover the new columns. campaign_type is IMMUTABLE by design:
--                   no role is granted UPDATE on it (asserted below).
--                   campaign_sequences_guard_frozen (app_guard_sequence) already
--                   rejects any UPDATE once the campaign is not DRAFT or the
--                   sequence is FROZEN, so the objective is frozen at activation
--                   for free. Reference templates reuse sequence_steps content
--                   columns, so their guard already applies.
--   Existing data:  none rewritten. ADD COLUMN ... DEFAULT with a constant is
--                   metadata-only in PostgreSQL 11+; every existing campaign
--                   becomes STANDARD and every sequence has a NULL objective.
--   App impact:     backend/workers select and write the new columns, so apply
--                   this BEFORE deploying the matching backend/workers.
--                   Nothing in the personalization pipeline is active until
--                   PERSONALIZATION_ENABLED=true and migrations 0027-0029 are
--                   also applied.
--   Risk:           low; additive.
--   Rollback:       REVOKE the three column grants, DROP CONSTRAINT
--                   campaigns_campaign_type_check and
--                   campaign_sequences_personalization_config_check, DROP COLUMN
--                   campaign_type and personalization_config (only safe while no
--                   HYPER_PERSONALIZED campaign exists that the owner wants to
--                   keep).
--
-- PREPARED ONLY. Do not apply to any shared/staging/production environment
-- without the project owner's separate review and explicit authorization.

BEGIN;
SET LOCAL search_path = '';

DO $preflight$
BEGIN
    IF pg_catalog.to_regclass('public.campaigns') IS NULL
       OR pg_catalog.to_regclass('public.campaign_sequences') IS NULL THEN
        RAISE EXCEPTION '0026 requires 0003_mailboxes_campaigns.sql to already be applied';
    END IF;
    IF EXISTS (
        SELECT 1 FROM pg_catalog.pg_attribute
        WHERE attrelid = 'public.campaigns'::pg_catalog.regclass
          AND attname = 'campaign_type' AND NOT attisdropped
    ) OR EXISTS (
        SELECT 1 FROM pg_catalog.pg_attribute
        WHERE attrelid = 'public.campaign_sequences'::pg_catalog.regclass
          AND attname = 'personalization_config' AND NOT attisdropped
    ) THEN
        RAISE EXCEPTION '0026 already applied: campaign_type or personalization_config already exists';
    END IF;
END;
$preflight$;

ALTER TABLE public.campaigns
    ADD COLUMN campaign_type text NOT NULL DEFAULT 'STANDARD',
    ADD CONSTRAINT campaigns_campaign_type_check CHECK (
        campaign_type IN ('STANDARD', 'HYPER_PERSONALIZED')
    );

ALTER TABLE public.campaign_sequences
    ADD COLUMN personalization_config jsonb,
    ADD CONSTRAINT campaign_sequences_personalization_config_check CHECK (
        personalization_config IS NULL
        OR (
            pg_catalog.jsonb_typeof(personalization_config) = 'object'
            AND pg_catalog.octet_length(pg_catalog.convert_to(personalization_config::text, 'UTF8')) <= 16384
        )
    );

-- INSERT only: the type is chosen at creation and never changes.
GRANT INSERT (campaign_type) ON public.campaigns TO app_api;
GRANT INSERT (personalization_config) ON public.campaign_sequences TO app_api;
GRANT UPDATE (personalization_config) ON public.campaign_sequences TO app_api;

DO $postcondition$
DECLARE
    runtime_role text;
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_constraint
        WHERE conname = 'campaigns_campaign_type_check'
          AND conrelid = 'public.campaigns'::pg_catalog.regclass
    ) OR NOT EXISTS (
        SELECT 1 FROM pg_catalog.pg_constraint
        WHERE conname = 'campaign_sequences_personalization_config_check'
          AND conrelid = 'public.campaign_sequences'::pg_catalog.regclass
    ) THEN
        RAISE EXCEPTION '0026 postcondition failed: CHECK constraints missing';
    END IF;

    -- campaign_type must be immutable for every runtime role.
    FOREACH runtime_role IN ARRAY ARRAY[
        'app_api', 'app_worker_general', 'app_worker_send', 'app_worker_sync',
        'app_scheduler', 'app_integrity_guard', 'app_outbox_relay'
    ] LOOP
        IF EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = runtime_role)
           AND pg_catalog.has_column_privilege(runtime_role, 'public.campaigns', 'campaign_type', 'UPDATE') THEN
            RAISE EXCEPTION '0026 postcondition failed: % can UPDATE campaigns.campaign_type', runtime_role;
        END IF;
    END LOOP;
END;
$postcondition$;

COMMIT;
