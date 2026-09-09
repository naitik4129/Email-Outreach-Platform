-- Contacts/content draft corrected with the full five-file chain.
-- Reuses the 0001 runtime capabilities and fixed helper functions. Imports and
-- processing use app_worker_general; send/sync and OAuth have separate roles.
-- See docs/database/MIGRATION_REVIEW.md for permissions and backend milestones.
-- Prepared only: no migration execution was performed.

BEGIN;
SET LOCAL search_path = '';



-- ---------------------------------------------------------------------------
-- Tenancy-foundation extensions
-- ---------------------------------------------------------------------------

CREATE TABLE public.workspace_invitations (
    id uuid DEFAULT pg_catalog.gen_random_uuid() CONSTRAINT workspace_invitations_pkey PRIMARY KEY,
    workspace_id uuid NOT NULL,
    inviter_id uuid NOT NULL,
    invited_email text NOT NULL,
    role_code text NOT NULL,
    token_digest text NOT NULL,
    status text NOT NULL DEFAULT 'PENDING',
    expires_at timestamptz NOT NULL,
    accepted_membership_id uuid,
    accepted_at timestamptz,
    revoked_at timestamptz,
    version bigint NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    CONSTRAINT workspace_invitations_workspace_fkey FOREIGN KEY (workspace_id)
        REFERENCES public.workspaces (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT workspace_invitations_inviter_fkey FOREIGN KEY (inviter_id)
        REFERENCES public.profiles (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT workspace_invitations_membership_fkey FOREIGN KEY (workspace_id, accepted_membership_id)
        REFERENCES public.workspace_memberships (workspace_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT workspace_invitations_workspace_id_key UNIQUE (workspace_id, id),
    CONSTRAINT workspace_invitations_token_digest_key UNIQUE (token_digest),
    CONSTRAINT workspace_invitations_invited_email_check CHECK (
        pg_catalog.char_length(invited_email) BETWEEN 3 AND 320 AND invited_email ~ '@'
    ),
    CONSTRAINT workspace_invitations_invited_email_lower_check CHECK (
        invited_email = pg_catalog.lower(invited_email)
    ),
    CONSTRAINT workspace_invitations_role_check CHECK (
        role_code IN ('ADMIN', 'MANAGER', 'MEMBER', 'VIEWER')
    ),
    CONSTRAINT workspace_invitations_token_digest_check CHECK (token_digest ~ '^[0-9a-f]{64,128}$'),
    CONSTRAINT workspace_invitations_status_check CHECK (
        status IN ('PENDING', 'ACCEPTED', 'REVOKED', 'EXPIRED')
    ),
    CONSTRAINT workspace_invitations_acceptance_check CHECK (
        (status = 'ACCEPTED' AND accepted_membership_id IS NOT NULL AND accepted_at IS NOT NULL)
        OR (status <> 'ACCEPTED' AND accepted_membership_id IS NULL AND accepted_at IS NULL)
    ),
    CONSTRAINT workspace_invitations_revocation_check CHECK ((status = 'REVOKED') = (revoked_at IS NOT NULL)),
    CONSTRAINT workspace_invitations_version_check CHECK (version > 0),
    CONSTRAINT workspace_invitations_time_check CHECK (
    expires_at > created_at AND (accepted_at IS NULL OR (accepted_at >= created_at AND accepted_at < expires_at))
    AND (revoked_at IS NULL OR revoked_at >= created_at))
);

CREATE UNIQUE INDEX workspace_invitations_pending_identity_idx
    ON public.workspace_invitations (workspace_id, invited_email)
    WHERE status = 'PENDING';
CREATE INDEX workspace_invitations_workspace_status_expiry_idx
    ON public.workspace_invitations (workspace_id, status, expires_at);

CREATE TABLE public.command_receipts (
    id uuid DEFAULT pg_catalog.gen_random_uuid() CONSTRAINT command_receipts_pkey PRIMARY KEY,
    workspace_id uuid NOT NULL,
    actor_id uuid NOT NULL,
    operation text NOT NULL,
    request_key text NOT NULL,
    payload_hash text NOT NULL,
    resource_type text,
    resource_id uuid,
    status text NOT NULL DEFAULT 'PENDING',
    response_summary jsonb NOT NULL DEFAULT '{}'::jsonb,
    response_version bigint,
    expires_at timestamptz NOT NULL,
    version bigint NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    CONSTRAINT command_receipts_workspace_fkey FOREIGN KEY (workspace_id)
        REFERENCES public.workspaces (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT command_receipts_actor_fkey FOREIGN KEY (actor_id)
        REFERENCES public.profiles (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT command_receipts_workspace_id_key UNIQUE (workspace_id, id),
    CONSTRAINT command_receipts_identity_key UNIQUE (workspace_id, actor_id, operation, request_key),
    CONSTRAINT command_receipts_operation_check CHECK (pg_catalog.char_length(operation) BETWEEN 1 AND 100),
    CONSTRAINT command_receipts_request_key_check CHECK (pg_catalog.char_length(request_key) BETWEEN 1 AND 200),
    CONSTRAINT command_receipts_payload_hash_check CHECK (payload_hash ~ '^[0-9a-f]{32,128}$'),
    CONSTRAINT command_receipts_resource_type_check CHECK (
        resource_type IS NULL OR pg_catalog.char_length(resource_type) BETWEEN 1 AND 100
    ),
    CONSTRAINT command_receipts_status_check CHECK (status IN ('PENDING', 'COMPLETED', 'FAILED')),
    CONSTRAINT command_receipts_response_summary_check CHECK (
        pg_catalog.jsonb_typeof(response_summary) = 'object'
        AND pg_catalog.octet_length(pg_catalog.convert_to(response_summary::text, 'UTF8')) <= 8192
    ),
    CONSTRAINT command_receipts_version_check CHECK (version > 0),
    CONSTRAINT command_receipts_expiry_check CHECK (expires_at > created_at),
    CONSTRAINT command_receipts_resource_pair_check CHECK ((resource_type IS NULL) = (resource_id IS NULL)),
    CONSTRAINT command_receipts_response_version_check CHECK (response_version IS NULL OR response_version > 0),
    CONSTRAINT command_receipts_actor_identity_key UNIQUE (workspace_id, actor_id, id)
);

CREATE INDEX command_receipts_expiry_idx ON public.command_receipts (expires_at, id);

-- ---------------------------------------------------------------------------
-- Leads, lists and safety identity
-- ---------------------------------------------------------------------------

CREATE TABLE public.leads (
    id uuid DEFAULT pg_catalog.gen_random_uuid() CONSTRAINT leads_pkey PRIMARY KEY,
    workspace_id uuid NOT NULL,
    original_address text NOT NULL,
    canonical_address text NOT NULL,
    normalization_version integer NOT NULL DEFAULT 1,
    first_name text,
    last_name text,
    company text,
    title text,
    custom_fields jsonb NOT NULL DEFAULT '{}'::jsonb,
    status text NOT NULL DEFAULT 'ACTIVE',
    validation_status text NOT NULL DEFAULT 'UNKNOWN',
    validated_at timestamptz,
    contact_revision bigint NOT NULL DEFAULT 1,
    archived_at timestamptz,
    version bigint NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    CONSTRAINT leads_workspace_fkey FOREIGN KEY (workspace_id)
        REFERENCES public.workspaces (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT leads_workspace_id_key UNIQUE (workspace_id, id),
    CONSTRAINT leads_canonical_address_key UNIQUE (workspace_id, canonical_address, normalization_version),
    CONSTRAINT leads_original_address_check CHECK (
        pg_catalog.char_length(original_address) BETWEEN 3 AND 320 AND original_address ~ '@'
    ),
    CONSTRAINT leads_canonical_address_check CHECK (
        pg_catalog.char_length(canonical_address) BETWEEN 3 AND 320
        AND canonical_address = pg_catalog.lower(canonical_address)
    ),
    CONSTRAINT leads_normalization_version_check CHECK (normalization_version > 0),
    CONSTRAINT leads_first_name_check CHECK (first_name IS NULL OR pg_catalog.char_length(first_name) <= 200),
    CONSTRAINT leads_last_name_check CHECK (last_name IS NULL OR pg_catalog.char_length(last_name) <= 200),
    CONSTRAINT leads_company_check CHECK (company IS NULL OR pg_catalog.char_length(company) <= 200),
    CONSTRAINT leads_title_check CHECK (title IS NULL OR pg_catalog.char_length(title) <= 200),
    CONSTRAINT leads_custom_fields_check CHECK (
        pg_catalog.jsonb_typeof(custom_fields) = 'object'
        AND pg_catalog.octet_length(pg_catalog.convert_to(custom_fields::text, 'UTF8')) <= 16384
    ),
    CONSTRAINT leads_status_check CHECK (status IN ('ACTIVE', 'ARCHIVED')),
    CONSTRAINT leads_archive_check CHECK ((status = 'ARCHIVED') = (archived_at IS NOT NULL)),
    CONSTRAINT leads_validation_status_check CHECK (
        validation_status IN ('UNKNOWN', 'VALID', 'INVALID', 'RISKY', 'CATCH_ALL', 'DISPOSABLE')
    ),
    CONSTRAINT leads_contact_revision_check CHECK (contact_revision > 0),
    CONSTRAINT leads_version_check CHECK (version > 0),
    CONSTRAINT leads_canonical_shape_check CHECK (
        canonical_address ~ '^[!-~]+@[a-z0-9][a-z0-9.-]*[a-z0-9]$'
        AND canonical_address !~ '[[:space:]<>(),;:"\\]'
        AND pg_catalog.length(canonical_address) - pg_catalog.length(pg_catalog.replace(canonical_address, '@', '')) = 1
        AND normalization_version = 1)
);

CREATE INDEX leads_workspace_status_id_idx ON public.leads (workspace_id, status, id);

CREATE TABLE public.lead_lists (
    id uuid DEFAULT pg_catalog.gen_random_uuid() CONSTRAINT lead_lists_pkey PRIMARY KEY,
    workspace_id uuid NOT NULL,
    name text NOT NULL,
    archived_at timestamptz,
    membership_revision bigint NOT NULL DEFAULT 1,
    capture_count bigint NOT NULL DEFAULT 0,
    version bigint NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    CONSTRAINT lead_lists_workspace_fkey FOREIGN KEY (workspace_id)
        REFERENCES public.workspaces (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT lead_lists_workspace_id_key UNIQUE (workspace_id, id),
    CONSTRAINT lead_lists_name_check CHECK (
        pg_catalog.char_length(name) BETWEEN 1 AND 200 AND name ~ '[^[:space:]]'
    ),
    CONSTRAINT lead_lists_capture_count_check CHECK (capture_count >= 0),
    CONSTRAINT lead_lists_membership_revision_check CHECK (membership_revision > 0),
    CONSTRAINT lead_lists_version_check CHECK (version > 0)
);

CREATE INDEX lead_lists_workspace_archived_id_idx ON public.lead_lists (workspace_id, archived_at, id);

CREATE TABLE public.lead_list_memberships (
    workspace_id uuid NOT NULL,
    list_id uuid NOT NULL,
    lead_id uuid NOT NULL,
    added_by uuid,
    added_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    CONSTRAINT lead_list_memberships_pkey PRIMARY KEY (workspace_id, list_id, lead_id),
    CONSTRAINT lead_list_memberships_list_fkey FOREIGN KEY (workspace_id, list_id)
        REFERENCES public.lead_lists (workspace_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT lead_list_memberships_lead_fkey FOREIGN KEY (workspace_id, lead_id)
        REFERENCES public.leads (workspace_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT lead_list_memberships_actor_fkey FOREIGN KEY (added_by)
        REFERENCES public.profiles (id) ON UPDATE RESTRICT ON DELETE RESTRICT
);

CREATE INDEX lead_list_memberships_reverse_idx ON public.lead_list_memberships (workspace_id, lead_id, list_id);

CREATE TABLE public.recipient_addresses (
    id uuid DEFAULT pg_catalog.gen_random_uuid() CONSTRAINT recipient_addresses_pkey PRIMARY KEY,
    workspace_id uuid NOT NULL,
    canonical_address text NOT NULL,
    normalization_version integer NOT NULL DEFAULT 1,
    original_display text,
    version bigint NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    CONSTRAINT recipient_addresses_workspace_fkey FOREIGN KEY (workspace_id)
        REFERENCES public.workspaces (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT recipient_addresses_workspace_id_key UNIQUE (workspace_id, id),
    CONSTRAINT recipient_addresses_canonical_key UNIQUE (workspace_id, canonical_address, normalization_version),
    CONSTRAINT recipient_addresses_canonical_check CHECK (
        pg_catalog.char_length(canonical_address) BETWEEN 3 AND 320
        AND canonical_address = pg_catalog.lower(canonical_address)
    ),
    CONSTRAINT recipient_addresses_original_display_check CHECK (
        original_display IS NULL OR pg_catalog.char_length(original_display) <= 320
    ),
    CONSTRAINT recipient_addresses_normalization_version_check CHECK (normalization_version > 0),
    CONSTRAINT recipient_addresses_version_check CHECK (version > 0),
    CONSTRAINT recipient_addresses_canonical_shape_check CHECK (
        canonical_address ~ '^[!-~]+@[a-z0-9][a-z0-9.-]*[a-z0-9]$'
        AND canonical_address !~ '[[:space:]<>(),;:"\\]'
        AND pg_catalog.length(canonical_address) - pg_catalog.length(pg_catalog.replace(canonical_address, '@', '')) = 1
        AND normalization_version = 1)
);

CREATE TABLE public.suppressions (
    release_audit_id uuid,
    id uuid DEFAULT pg_catalog.gen_random_uuid() CONSTRAINT suppressions_pkey PRIMARY KEY,
    workspace_id uuid NOT NULL,
    address_id uuid NOT NULL,
    reason text NOT NULL,
    status text NOT NULL DEFAULT 'ACTIVE',
    first_observed_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    last_observed_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    released_at timestamptz,
    release_actor_id uuid,
    version bigint NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    CONSTRAINT suppressions_workspace_fkey FOREIGN KEY (workspace_id)
        REFERENCES public.workspaces (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT suppressions_address_fkey FOREIGN KEY (workspace_id, address_id)
        REFERENCES public.recipient_addresses (workspace_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT suppressions_release_actor_fkey FOREIGN KEY (release_actor_id)
        REFERENCES public.profiles (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT suppressions_workspace_id_key UNIQUE (workspace_id, id),
    CONSTRAINT suppressions_address_reason_key UNIQUE (workspace_id, address_id, reason),
    CONSTRAINT suppressions_reason_check CHECK (reason IN ('MANUAL', 'UNSUBSCRIBE', 'HARD_BOUNCE', 'COMPLAINT')),
    CONSTRAINT suppressions_status_check CHECK (status IN ('ACTIVE', 'RELEASED')),
    CONSTRAINT suppressions_release_check CHECK (
        (status = 'RELEASED' AND released_at IS NOT NULL AND release_actor_id IS NOT NULL)
        OR (status = 'ACTIVE' AND released_at IS NULL AND release_actor_id IS NULL)
    ),
    CONSTRAINT suppressions_observation_order_check CHECK (last_observed_at >= first_observed_at),
    CONSTRAINT suppressions_release_order_check CHECK (released_at IS NULL OR released_at >= first_observed_at),
    CONSTRAINT suppressions_version_check CHECK (version > 0),
    CONSTRAINT suppressions_release_audit_check CHECK ((status = 'RELEASED') = (release_audit_id IS NOT NULL))
);

-- Highest-value index in this migration: authoritative pre-send suppression
-- lookup checks only ACTIVE rows for a given address, per SUPPRESSION.md.
CREATE INDEX suppressions_active_lookup_idx
    ON public.suppressions (workspace_id, address_id)
    WHERE status = 'ACTIVE';

CREATE TABLE public.suppression_sources (
    id uuid DEFAULT pg_catalog.gen_random_uuid() CONSTRAINT suppression_sources_pkey PRIMARY KEY,
    workspace_id uuid NOT NULL,
    suppression_id uuid NOT NULL,
    source_kind text NOT NULL,
    source_key text NOT NULL,
    -- FK constraints for provider_receipt_id/domain_event_id are added by
    -- 0004_messages_events_inbox.sql once their target tables exist.
    provider_receipt_id uuid,
    domain_event_id uuid,
    actor_id uuid,
    observed_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    CONSTRAINT suppression_sources_workspace_fkey FOREIGN KEY (workspace_id)
        REFERENCES public.workspaces (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT suppression_sources_suppression_fkey FOREIGN KEY (workspace_id, suppression_id)
        REFERENCES public.suppressions (workspace_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT suppression_sources_actor_fkey FOREIGN KEY (actor_id)
        REFERENCES public.profiles (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT suppression_sources_identity_key UNIQUE (workspace_id, suppression_id, source_kind, source_key),
    CONSTRAINT suppression_sources_source_kind_check CHECK (
        source_kind IN ('MANUAL', 'UNSUBSCRIBE_TOKEN', 'PROVIDER_RECEIPT', 'DOMAIN_EVENT', 'IMPORT')
    ),
    CONSTRAINT suppression_sources_source_key_check CHECK (pg_catalog.char_length(source_key) BETWEEN 1 AND 200),
    CONSTRAINT suppression_sources_evidence_fkey_check CHECK (
        (source_kind = 'PROVIDER_RECEIPT') = (provider_receipt_id IS NOT NULL)
        AND (source_kind = 'DOMAIN_EVENT') = (domain_event_id IS NOT NULL)
    ),
    CONSTRAINT suppression_sources_evidence_check CHECK (
        pg_catalog.jsonb_typeof(evidence) = 'object'
        AND pg_catalog.octet_length(pg_catalog.convert_to(evidence::text, 'UTF8')) <= 4096
    ),
    CONSTRAINT suppression_sources_actor_shape_check CHECK (source_kind NOT IN ('MANUAL','IMPORT') OR actor_id IS NOT NULL)
);

CREATE INDEX suppression_sources_parent_time_idx
    ON public.suppression_sources (workspace_id, suppression_id, observed_at);

CREATE TABLE public.unsubscribe_tokens (
    id uuid DEFAULT pg_catalog.gen_random_uuid() CONSTRAINT unsubscribe_tokens_pkey PRIMARY KEY,
    workspace_id uuid NOT NULL,
    address_id uuid NOT NULL,
    -- FK constraint for message_id is added by 0004_messages_events_inbox.sql.
    message_id uuid,
    token_digest text NOT NULL,
    purpose text NOT NULL DEFAULT 'UNSUBSCRIBE',
    expires_at timestamptz NOT NULL,
    revoked_at timestamptz,
    version bigint NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    CONSTRAINT unsubscribe_tokens_workspace_fkey FOREIGN KEY (workspace_id)
        REFERENCES public.workspaces (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT unsubscribe_tokens_address_fkey FOREIGN KEY (workspace_id, address_id)
        REFERENCES public.recipient_addresses (workspace_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT unsubscribe_tokens_workspace_id_key UNIQUE (workspace_id, id),
    CONSTRAINT unsubscribe_tokens_digest_key UNIQUE (token_digest),
    CONSTRAINT unsubscribe_tokens_digest_check CHECK (token_digest ~ '^[0-9a-f]{64,128}$'),
    CONSTRAINT unsubscribe_tokens_purpose_check CHECK (purpose IN ('UNSUBSCRIBE')),
    CONSTRAINT unsubscribe_tokens_expiry_check CHECK (expires_at > created_at),
    CONSTRAINT unsubscribe_tokens_version_check CHECK (version > 0)
);

CREATE INDEX unsubscribe_tokens_address_idx ON public.unsubscribe_tokens (workspace_id, address_id);
CREATE INDEX unsubscribe_tokens_expiry_idx ON public.unsubscribe_tokens (expires_at);

CREATE TABLE public.platform_controls (
    id boolean NOT NULL DEFAULT true CONSTRAINT platform_controls_pkey PRIMARY KEY,
    sending_restricted boolean NOT NULL DEFAULT false,
    restriction_reason text,
    version bigint NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    CONSTRAINT platform_controls_singleton_check CHECK (id),
    CONSTRAINT platform_controls_restriction_check CHECK (
        NOT sending_restricted OR restriction_reason IS NOT NULL
    ),
    CONSTRAINT platform_controls_version_check CHECK (version > 0)
);

CREATE TABLE public.platform_suppressions (
    id uuid DEFAULT pg_catalog.gen_random_uuid() CONSTRAINT platform_suppressions_pkey PRIMARY KEY,
    canonical_address text NOT NULL,
    normalization_version integer NOT NULL DEFAULT 1,
    reason text NOT NULL DEFAULT 'PLATFORM_BLOCK',
    status text NOT NULL DEFAULT 'ACTIVE',
    operator_actor_id uuid NOT NULL,
    evidence_reference text,
    released_at timestamptz,
    release_actor_id uuid,
    version bigint NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    CONSTRAINT platform_suppressions_operator_fkey FOREIGN KEY (operator_actor_id)
        REFERENCES public.profiles (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT platform_suppressions_release_actor_fkey FOREIGN KEY (release_actor_id)
        REFERENCES public.profiles (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT platform_suppressions_identity_key UNIQUE (canonical_address, normalization_version, reason),
    CONSTRAINT platform_suppressions_canonical_check CHECK (
        pg_catalog.char_length(canonical_address) BETWEEN 3 AND 320
        AND canonical_address = pg_catalog.lower(canonical_address)
    ),
    CONSTRAINT platform_suppressions_reason_check CHECK (reason IN ('PLATFORM_BLOCK')),
    CONSTRAINT platform_suppressions_status_check CHECK (status IN ('ACTIVE', 'RELEASED')),
    CONSTRAINT platform_suppressions_release_check CHECK (
        (status = 'RELEASED' AND released_at IS NOT NULL AND release_actor_id IS NOT NULL)
        OR (status = 'ACTIVE' AND released_at IS NULL AND release_actor_id IS NULL)
    ),
    CONSTRAINT platform_suppressions_version_check CHECK (version > 0),
    CONSTRAINT platform_suppressions_canonical_shape_check CHECK (
        canonical_address ~ '^[!-~]+@[a-z0-9][a-z0-9.-]*[a-z0-9]$'
        AND canonical_address !~ '[[:space:]<>(),;:"\\]'
        AND pg_catalog.length(canonical_address) - pg_catalog.length(pg_catalog.replace(canonical_address, '@', '')) = 1
        AND normalization_version = 1)
);

CREATE INDEX platform_suppressions_active_idx
    ON public.platform_suppressions (canonical_address)
    WHERE status = 'ACTIVE';

-- ---------------------------------------------------------------------------
-- Templates and content snapshots
-- ---------------------------------------------------------------------------

CREATE TABLE public.templates (
    id uuid DEFAULT pg_catalog.gen_random_uuid() CONSTRAINT templates_pkey PRIMARY KEY,
    workspace_id uuid NOT NULL,
    name text NOT NULL,
    current_version_id uuid,
    mode text NOT NULL DEFAULT 'STANDARD',
    archived_at timestamptz,
    version bigint NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    CONSTRAINT templates_workspace_fkey FOREIGN KEY (workspace_id)
        REFERENCES public.workspaces (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT templates_workspace_id_key UNIQUE (workspace_id, id),
    CONSTRAINT templates_name_check CHECK (
        pg_catalog.char_length(name) BETWEEN 1 AND 200 AND name ~ '[^[:space:]]'
    ),
    CONSTRAINT templates_mode_check CHECK (mode IN ('STANDARD')),
    CONSTRAINT templates_version_check CHECK (version > 0)
);

CREATE INDEX templates_workspace_archived_id_idx ON public.templates (workspace_id, archived_at, id);

CREATE TABLE public.template_versions (
    id uuid DEFAULT pg_catalog.gen_random_uuid() CONSTRAINT template_versions_pkey PRIMARY KEY,
    workspace_id uuid NOT NULL,
    template_id uuid NOT NULL,
    revision integer NOT NULL,
    subject text NOT NULL,
    body_html text NOT NULL,
    variable_schema jsonb NOT NULL DEFAULT '{}'::jsonb,
    content_digest text NOT NULL,
    renderer_version integer NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    CONSTRAINT template_versions_workspace_fkey FOREIGN KEY (workspace_id)
        REFERENCES public.workspaces (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT template_versions_template_fkey FOREIGN KEY (workspace_id, template_id)
        REFERENCES public.templates (workspace_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT template_versions_workspace_id_key UNIQUE (workspace_id, id),
    CONSTRAINT template_versions_revision_key UNIQUE (workspace_id, template_id, revision),
    CONSTRAINT template_versions_template_id_key UNIQUE (workspace_id, template_id, id),
    CONSTRAINT template_versions_revision_check CHECK (revision > 0),
    CONSTRAINT template_versions_subject_check CHECK (pg_catalog.char_length(subject) BETWEEN 1 AND 500),
    CONSTRAINT template_versions_body_html_check CHECK (pg_catalog.char_length(body_html) <= 200000),
    CONSTRAINT template_versions_variable_schema_check CHECK (
        pg_catalog.jsonb_typeof(variable_schema) = 'object'
        AND pg_catalog.octet_length(pg_catalog.convert_to(variable_schema::text, 'UTF8')) <= 16384
    ),
    CONSTRAINT template_versions_content_digest_check CHECK (content_digest ~ '^[0-9a-f]{32,128}$'),
    CONSTRAINT template_versions_renderer_version_check CHECK (renderer_version > 0)
);

ALTER TABLE public.templates
    ADD CONSTRAINT templates_current_version_fkey FOREIGN KEY (workspace_id, id, current_version_id)
        REFERENCES public.template_versions (workspace_id, template_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT;

-- ---------------------------------------------------------------------------
-- Imports
-- ---------------------------------------------------------------------------

CREATE TABLE public.import_jobs (
    id uuid DEFAULT pg_catalog.gen_random_uuid() CONSTRAINT import_jobs_pkey PRIMARY KEY,
    workspace_id uuid NOT NULL,
    initiator_id uuid NOT NULL,
    storage_object_key text NOT NULL,
    storage_object_version text NOT NULL,
    storage_object_digest text NOT NULL,
    import_kind text NOT NULL,
    mapping jsonb NOT NULL DEFAULT '{}'::jsonb,
    list_id uuid,
    status text NOT NULL DEFAULT 'PENDING',
    row_cursor bigint NOT NULL DEFAULT 0,
    total_rows bigint,
    processed_rows bigint NOT NULL DEFAULT 0,
    accepted_rows bigint NOT NULL DEFAULT 0,
    duplicate_rows bigint NOT NULL DEFAULT 0,
    rejected_rows bigint NOT NULL DEFAULT 0,
    next_due_at timestamptz,
    lease_owner text,
    lease_generation bigint NOT NULL DEFAULT 1,
    lease_expires_at timestamptz,
    failure_summary text,
    version bigint NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    CONSTRAINT import_jobs_workspace_fkey FOREIGN KEY (workspace_id)
        REFERENCES public.workspaces (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT import_jobs_initiator_fkey FOREIGN KEY (initiator_id)
        REFERENCES public.profiles (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT import_jobs_list_fkey FOREIGN KEY (workspace_id, list_id)
        REFERENCES public.lead_lists (workspace_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT import_jobs_workspace_id_key UNIQUE (workspace_id, id),
    CONSTRAINT import_jobs_storage_key_check CHECK (pg_catalog.char_length(storage_object_key) BETWEEN 1 AND 1024),
    CONSTRAINT import_jobs_import_kind_check CHECK (import_kind IN ('LEADS', 'SUPPRESSION')),
    CONSTRAINT import_jobs_status_check CHECK (
        status IN ('PENDING', 'PROCESSING', 'COMPLETED', 'COMPLETED_WITH_ERRORS', 'FAILED')
    ),
    CONSTRAINT import_jobs_mapping_check CHECK (
        pg_catalog.jsonb_typeof(mapping) = 'object'
        AND pg_catalog.octet_length(pg_catalog.convert_to(mapping::text, 'UTF8')) <= 16384
    ),
    CONSTRAINT import_jobs_row_cursor_check CHECK (row_cursor >= 0),
    CONSTRAINT import_jobs_total_rows_check CHECK (total_rows IS NULL OR total_rows >= 0),
    CONSTRAINT import_jobs_processed_rows_check CHECK (
        processed_rows >= 0 AND (total_rows IS NULL OR processed_rows <= total_rows)
    ),
    CONSTRAINT import_jobs_result_rows_check CHECK (
        accepted_rows >= 0 AND duplicate_rows >= 0 AND rejected_rows >= 0
        AND accepted_rows + duplicate_rows + rejected_rows <= processed_rows
    ),
    CONSTRAINT import_jobs_lease_generation_check CHECK (lease_generation > 0),
    CONSTRAINT import_jobs_version_check CHECK (version > 0),
    CONSTRAINT import_jobs_storage_identity_check CHECK (
    pg_catalog.char_length(storage_object_version) BETWEEN 1 AND 1024 AND storage_object_digest ~ '^[0-9a-f]{64}$'),
    CONSTRAINT import_jobs_kind_shape_check CHECK (import_kind <> 'SUPPRESSION' OR list_id IS NULL),
    CONSTRAINT import_jobs_lease_shape_check CHECK ((lease_owner IS NULL) = (lease_expires_at IS NULL)),
    CONSTRAINT import_jobs_terminal_counts_check CHECK (status NOT IN ('COMPLETED','COMPLETED_WITH_ERRORS') OR
        (total_rows IS NOT NULL AND processed_rows = total_rows AND accepted_rows + duplicate_rows + rejected_rows = processed_rows))
);

CREATE INDEX import_jobs_workspace_created_idx ON public.import_jobs (workspace_id, created_at, id);
CREATE INDEX import_jobs_due_idx
    ON public.import_jobs (next_due_at, id)
    WHERE status IN ('PENDING', 'PROCESSING');

CREATE TABLE public.import_row_results (
    workspace_id uuid NOT NULL,
    import_id uuid NOT NULL,
    row_number bigint NOT NULL,
    status text NOT NULL,
    lead_id uuid,
    suppression_id uuid,
    validation_reason text,
    error_artifact_ref text,
    created_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    CONSTRAINT import_row_results_pkey PRIMARY KEY (workspace_id, import_id, row_number),
    CONSTRAINT import_row_results_import_fkey FOREIGN KEY (workspace_id, import_id)
        REFERENCES public.import_jobs (workspace_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT import_row_results_lead_fkey FOREIGN KEY (workspace_id, lead_id)
        REFERENCES public.leads (workspace_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT import_row_results_suppression_fkey FOREIGN KEY (workspace_id, suppression_id)
        REFERENCES public.suppressions (workspace_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT import_row_results_row_number_check CHECK (row_number > 0),
    CONSTRAINT import_row_results_status_check CHECK (status IN ('ACCEPTED', 'DUPLICATE', 'REJECTED')),
    CONSTRAINT import_row_results_validation_reason_check CHECK (
        validation_reason IS NULL OR pg_catalog.char_length(validation_reason) <= 500
    ),
    CONSTRAINT import_row_results_error_artifact_check CHECK (
        error_artifact_ref IS NULL OR pg_catalog.char_length(error_artifact_ref) <= 1024
    ),
    CONSTRAINT import_row_results_shape_check CHECK (
    (status = 'REJECTED' AND lead_id IS NULL AND suppression_id IS NULL AND validation_reason IS NOT NULL)
    OR (status IN ('ACCEPTED','DUPLICATE') AND pg_catalog.num_nonnulls(lead_id, suppression_id) = 1))
);

-- ---------------------------------------------------------------------------
-- Row level security and runtime capabilities

ALTER TABLE public.workspace_invitations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.workspace_invitations FORCE ROW LEVEL SECURITY;
ALTER TABLE public.command_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.command_receipts FORCE ROW LEVEL SECURITY;
ALTER TABLE public.leads ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.leads FORCE ROW LEVEL SECURITY;
ALTER TABLE public.lead_lists ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.lead_lists FORCE ROW LEVEL SECURITY;
ALTER TABLE public.lead_list_memberships ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.lead_list_memberships FORCE ROW LEVEL SECURITY;
ALTER TABLE public.recipient_addresses ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.recipient_addresses FORCE ROW LEVEL SECURITY;
ALTER TABLE public.suppressions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.suppressions FORCE ROW LEVEL SECURITY;
ALTER TABLE public.suppression_sources ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.suppression_sources FORCE ROW LEVEL SECURITY;
ALTER TABLE public.unsubscribe_tokens ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.unsubscribe_tokens FORCE ROW LEVEL SECURITY;
ALTER TABLE public.platform_controls ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.platform_controls FORCE ROW LEVEL SECURITY;
ALTER TABLE public.platform_suppressions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.platform_suppressions FORCE ROW LEVEL SECURITY;
ALTER TABLE public.templates ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.templates FORCE ROW LEVEL SECURITY;
ALTER TABLE public.template_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.template_versions FORCE ROW LEVEL SECURITY;
ALTER TABLE public.import_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.import_jobs FORCE ROW LEVEL SECURITY;
ALTER TABLE public.import_row_results ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.import_row_results FORCE ROW LEVEL SECURITY;

REVOKE ALL PRIVILEGES ON TABLE
    public.workspace_invitations, public.command_receipts, public.leads,
    public.lead_lists, public.lead_list_memberships, public.recipient_addresses,
    public.suppressions, public.suppression_sources, public.unsubscribe_tokens,
    public.platform_controls, public.platform_suppressions,
    public.templates, public.template_versions,
    public.import_jobs, public.import_row_results
    FROM PUBLIC, anon, authenticated, service_role;



CREATE TRIGGER workspace_invitations_touch_row
    BEFORE UPDATE ON public.workspace_invitations
    FOR EACH ROW EXECUTE FUNCTION public.app_touch_row();
CREATE TRIGGER command_receipts_touch_row
    BEFORE UPDATE ON public.command_receipts
    FOR EACH ROW EXECUTE FUNCTION public.app_touch_row();
CREATE TRIGGER leads_touch_row
    BEFORE UPDATE ON public.leads
    FOR EACH ROW EXECUTE FUNCTION public.app_touch_row();
CREATE TRIGGER lead_lists_touch_row
    BEFORE UPDATE ON public.lead_lists
    FOR EACH ROW EXECUTE FUNCTION public.app_touch_row();
CREATE TRIGGER recipient_addresses_touch_row
    BEFORE UPDATE ON public.recipient_addresses
    FOR EACH ROW EXECUTE FUNCTION public.app_touch_row();
CREATE TRIGGER suppressions_touch_row
    BEFORE UPDATE ON public.suppressions
    FOR EACH ROW EXECUTE FUNCTION public.app_touch_row();
CREATE TRIGGER unsubscribe_tokens_touch_row
    BEFORE UPDATE ON public.unsubscribe_tokens
    FOR EACH ROW EXECUTE FUNCTION public.app_touch_row();
CREATE TRIGGER platform_controls_touch_row
    BEFORE UPDATE ON public.platform_controls
    FOR EACH ROW EXECUTE FUNCTION public.app_touch_row();
CREATE TRIGGER platform_suppressions_touch_row
    BEFORE UPDATE ON public.platform_suppressions
    FOR EACH ROW EXECUTE FUNCTION public.app_touch_row();
CREATE TRIGGER templates_touch_row
    BEFORE UPDATE ON public.templates
    FOR EACH ROW EXECUTE FUNCTION public.app_touch_row();
CREATE TRIGGER import_jobs_touch_row
    BEFORE UPDATE ON public.import_jobs
    FOR EACH ROW EXECUTE FUNCTION public.app_touch_row();


DO $privilege_check$
BEGIN
    IF pg_catalog.has_database_privilege('app_worker_general', pg_catalog.current_database(), 'CREATE')
       OR EXISTS (
           SELECT 1 FROM pg_catalog.pg_namespace AS namespace
           WHERE namespace.nspname NOT LIKE 'pg_temp_%'
             AND namespace.nspname NOT LIKE 'pg_toast_temp_%'
             AND pg_catalog.has_schema_privilege('app_worker_general', namespace.oid, 'CREATE')
       ) THEN
        RAISE EXCEPTION '0002 requires app_worker_general without effective database/schema CREATE privileges';
    END IF;
    IF pg_catalog.pg_has_role('app_worker_general', 'app_foundation_reader', 'MEMBER') THEN
        RAISE EXCEPTION '0002 forbids worker membership in the foundation reader role';
    END IF;
END;
$privilege_check$;

CREATE FUNCTION public.app_guard_recipient_identity() RETURNS trigger
LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path = ''
AS $function$
BEGIN
    IF (NEW.workspace_id, NEW.canonical_address, NEW.normalization_version) IS DISTINCT FROM
       (OLD.workspace_id, OLD.canonical_address, OLD.normalization_version) THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Recipient safety identity is immutable';
    END IF;
    RETURN NEW;
END;
$function$;
REVOKE ALL ON FUNCTION public.app_guard_recipient_identity() FROM PUBLIC, anon, authenticated, service_role;

CREATE TRIGGER recipient_addresses_guard_identity BEFORE UPDATE ON public.recipient_addresses FOR EACH ROW EXECUTE FUNCTION public.app_guard_recipient_identity();

CREATE FUNCTION public.app_lead_contact_revision() RETURNS trigger
LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path = ''
AS $function$
BEGIN
    IF (NEW.original_address, NEW.canonical_address, NEW.normalization_version, NEW.first_name,
        NEW.last_name, NEW.company, NEW.title, NEW.custom_fields, NEW.status, NEW.validation_status)
       IS DISTINCT FROM
       (OLD.original_address, OLD.canonical_address, OLD.normalization_version, OLD.first_name,
        OLD.last_name, OLD.company, OLD.title, OLD.custom_fields, OLD.status, OLD.validation_status) THEN
        NEW.contact_revision := OLD.contact_revision + 1;
    ELSE NEW.contact_revision := OLD.contact_revision;
    END IF;
    RETURN NEW;
END;
$function$;
REVOKE ALL ON FUNCTION public.app_lead_contact_revision() FROM PUBLIC, anon, authenticated, service_role;

CREATE TRIGGER leads_contact_revision BEFORE UPDATE ON public.leads FOR EACH ROW EXECUTE FUNCTION public.app_lead_contact_revision();

CREATE FUNCTION public.app_guard_list_membership() RETURNS trigger
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = ''
AS $function$
DECLARE
    tenant uuid := COALESCE(NEW.workspace_id, OLD.workspace_id);
    list_uuid uuid := COALESCE(NEW.list_id, OLD.list_id);
    captures bigint;
BEGIN
    SELECT capture_count INTO STRICT captures FROM public.lead_lists
      WHERE workspace_id = tenant AND id = list_uuid FOR UPDATE;
    IF captures <> 0 THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'List membership is held by audience capture';
    END IF;
    IF TG_WHEN = 'AFTER' THEN
        UPDATE public.lead_lists SET membership_revision = membership_revision + 1
          WHERE workspace_id = tenant AND id = list_uuid;
    END IF;
    RETURN COALESCE(NEW, OLD);
END;
$function$;
REVOKE ALL ON FUNCTION public.app_guard_list_membership() FROM PUBLIC, anon, authenticated, service_role;
-- The migration identity creates triggers after ownership transfer; keep this
-- grant temporary so function execution is not opened to runtime/browser roles.
GRANT EXECUTE ON FUNCTION public.app_guard_list_membership() TO CURRENT_USER;

GRANT app_integrity_guard TO CURRENT_USER;
GRANT CREATE ON SCHEMA public TO app_integrity_guard;
ALTER FUNCTION public.app_guard_list_membership() OWNER TO app_integrity_guard;
REVOKE CREATE ON SCHEMA public FROM app_integrity_guard;

CREATE TRIGGER lead_list_memberships_capture_guard BEFORE INSERT OR DELETE ON public.lead_list_memberships FOR EACH ROW EXECUTE FUNCTION public.app_guard_list_membership();

CREATE TRIGGER lead_list_memberships_revision AFTER INSERT OR DELETE ON public.lead_list_memberships
FOR EACH ROW EXECUTE FUNCTION public.app_guard_list_membership();
REVOKE EXECUTE ON FUNCTION public.app_guard_list_membership() FROM CURRENT_USER;
REVOKE app_integrity_guard FROM CURRENT_USER;

CREATE POLICY leads_api_select ON public.leads
FOR SELECT TO app_api USING (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('product.read'));
GRANT SELECT ON public.leads TO app_api;

CREATE POLICY lead_lists_api_select ON public.lead_lists
FOR SELECT TO app_api USING (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('product.read'));
GRANT SELECT ON public.lead_lists TO app_api;

CREATE POLICY lead_list_memberships_api_select ON public.lead_list_memberships
FOR SELECT TO app_api USING (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('product.read'));
GRANT SELECT ON public.lead_list_memberships TO app_api;

CREATE POLICY recipient_addresses_api_select ON public.recipient_addresses
FOR SELECT TO app_api USING (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('product.read'));
GRANT SELECT ON public.recipient_addresses TO app_api;

CREATE POLICY suppressions_api_select ON public.suppressions
FOR SELECT TO app_api USING (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('product.read'));
GRANT SELECT ON public.suppressions TO app_api;

CREATE POLICY templates_api_select ON public.templates
FOR SELECT TO app_api USING (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('product.read'));
GRANT SELECT ON public.templates TO app_api;

CREATE POLICY template_versions_api_select ON public.template_versions
FOR SELECT TO app_api USING (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('product.read'));
GRANT SELECT ON public.template_versions TO app_api;

CREATE POLICY import_jobs_api_select ON public.import_jobs
FOR SELECT TO app_api USING (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('product.read'));
GRANT SELECT ON public.import_jobs TO app_api;

CREATE POLICY import_row_results_api_select ON public.import_row_results
FOR SELECT TO app_api USING (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('product.read'));
GRANT SELECT ON public.import_row_results TO app_api;

CREATE POLICY workspace_invitations_api_select ON public.workspace_invitations
FOR SELECT TO app_api USING (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('audit.read'));
GRANT SELECT (id, workspace_id, inviter_id, invited_email, role_code, status, expires_at, accepted_membership_id, accepted_at, revoked_at, version, created_at, updated_at) ON public.workspace_invitations TO app_api;

CREATE POLICY command_receipts_api_select ON public.command_receipts
FOR SELECT TO app_api USING (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('product.read') AND actor_id = public.app_current_user_id());
GRANT SELECT ON public.command_receipts TO app_api;

CREATE POLICY command_receipts_api_insert ON public.command_receipts
FOR INSERT TO app_api WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('product.read') AND actor_id = public.app_current_user_id());
GRANT INSERT (id, workspace_id, actor_id, operation, request_key, payload_hash, resource_type, resource_id, status, response_summary, response_version, expires_at) ON public.command_receipts TO app_api;

CREATE POLICY command_receipts_api_update ON public.command_receipts
FOR UPDATE TO app_api USING (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('product.read') AND actor_id = public.app_current_user_id()) WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('product.read') AND actor_id = public.app_current_user_id());
GRANT UPDATE (resource_type, resource_id, status, response_summary, response_version) ON public.command_receipts TO app_api;

CREATE POLICY leads_api_insert ON public.leads
FOR INSERT TO app_api WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('contacts.manage'));
GRANT INSERT (id, workspace_id, original_address, canonical_address, normalization_version, first_name, last_name, company, title, custom_fields, status, validation_status, validated_at, archived_at) ON public.leads TO app_api;

CREATE POLICY leads_api_update ON public.leads
FOR UPDATE TO app_api USING (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('contacts.manage')) WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('contacts.manage'));
GRANT UPDATE (original_address, canonical_address, normalization_version, first_name, last_name, company, title, custom_fields, status, validation_status, validated_at, archived_at) ON public.leads TO app_api;

CREATE POLICY lead_lists_api_insert ON public.lead_lists
FOR INSERT TO app_api WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('contacts.manage'));
GRANT INSERT (id, workspace_id, name, archived_at) ON public.lead_lists TO app_api;

CREATE POLICY lead_lists_api_update ON public.lead_lists
FOR UPDATE TO app_api USING (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('contacts.manage')) WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('contacts.manage'));
GRANT UPDATE (name, archived_at) ON public.lead_lists TO app_api;

CREATE POLICY templates_api_insert ON public.templates
FOR INSERT TO app_api WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('templates.manage'));
GRANT INSERT (id, workspace_id, name, current_version_id, mode, archived_at) ON public.templates TO app_api;

CREATE POLICY templates_api_update ON public.templates
FOR UPDATE TO app_api USING (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('templates.manage')) WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('templates.manage'));
GRANT UPDATE (name, current_version_id, mode, archived_at) ON public.templates TO app_api;

CREATE POLICY lead_list_memberships_api_insert ON public.lead_list_memberships
FOR INSERT TO app_api WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('contacts.manage') AND added_by = public.app_current_user_id());
GRANT INSERT (workspace_id, list_id, lead_id, added_by, added_at) ON public.lead_list_memberships TO app_api;

CREATE POLICY recipient_addresses_api_insert ON public.recipient_addresses
FOR INSERT TO app_api WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('contacts.manage'));
GRANT INSERT (id, workspace_id, canonical_address, normalization_version, original_display) ON public.recipient_addresses TO app_api;

CREATE POLICY template_versions_api_insert ON public.template_versions
FOR INSERT TO app_api WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('templates.manage'));
GRANT INSERT (id, workspace_id, template_id, revision, subject, body_html, variable_schema, content_digest, renderer_version) ON public.template_versions TO app_api;

CREATE POLICY import_jobs_api_insert ON public.import_jobs
FOR INSERT TO app_api WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('contacts.manage') AND initiator_id = public.app_current_user_id());
GRANT INSERT (id, workspace_id, initiator_id, storage_object_key, storage_object_version, storage_object_digest, import_kind, mapping, list_id, status, row_cursor, total_rows, processed_rows, accepted_rows, duplicate_rows, rejected_rows, next_due_at, lease_owner, lease_generation, lease_expires_at, failure_summary) ON public.import_jobs TO app_api;

CREATE POLICY lead_list_memberships_api_delete ON public.lead_list_memberships
FOR DELETE TO app_api USING (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('contacts.manage'));
GRANT DELETE ON public.lead_list_memberships TO app_api;

CREATE POLICY suppressions_api_insert ON public.suppressions
FOR INSERT TO app_api WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('suppression.add') AND reason = 'MANUAL' AND status = 'ACTIVE');
GRANT INSERT (release_audit_id, id, workspace_id, address_id, reason, status, first_observed_at, last_observed_at, released_at, release_actor_id) ON public.suppressions TO app_api;

CREATE POLICY suppressions_api_update ON public.suppressions
FOR UPDATE TO app_api USING (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('suppression.add') AND reason = 'MANUAL') WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('suppression.add') AND reason = 'MANUAL' AND (status = 'ACTIVE' OR (public.app_has_permission('suppression.release_manual') AND release_actor_id = public.app_current_user_id())));
GRANT UPDATE (status, last_observed_at, released_at, release_actor_id, release_audit_id) ON public.suppressions TO app_api;

CREATE POLICY suppression_sources_api_insert ON public.suppression_sources
FOR INSERT TO app_api WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('suppression.add') AND source_kind IN ('MANUAL','IMPORT') AND actor_id = public.app_current_user_id() AND EXISTS (SELECT 1 FROM public.suppressions s WHERE s.workspace_id = suppression_sources.workspace_id AND s.id = suppression_sources.suppression_id AND s.reason = 'MANUAL'));
GRANT INSERT (id, workspace_id, suppression_id, source_kind, source_key, provider_receipt_id, domain_event_id, actor_id, observed_at, evidence) ON public.suppression_sources TO app_api;

CREATE POLICY command_receipts_connection_select ON public.command_receipts
FOR SELECT TO app_connection USING (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('mailboxes.manage') AND actor_id = public.app_current_user_id());
GRANT SELECT ON public.command_receipts TO app_connection;

CREATE POLICY leads_worker_general_select ON public.leads
FOR SELECT TO app_worker_general USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.leads TO app_worker_general;

CREATE POLICY lead_lists_worker_general_select ON public.lead_lists
FOR SELECT TO app_worker_general USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.lead_lists TO app_worker_general;

CREATE POLICY lead_list_memberships_worker_general_select ON public.lead_list_memberships
FOR SELECT TO app_worker_general USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.lead_list_memberships TO app_worker_general;

CREATE POLICY recipient_addresses_worker_general_select ON public.recipient_addresses
FOR SELECT TO app_worker_general USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.recipient_addresses TO app_worker_general;

CREATE POLICY suppressions_worker_general_select ON public.suppressions
FOR SELECT TO app_worker_general USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.suppressions TO app_worker_general;

CREATE POLICY suppression_sources_worker_general_select ON public.suppression_sources
FOR SELECT TO app_worker_general USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.suppression_sources TO app_worker_general;

CREATE POLICY templates_worker_general_select ON public.templates
FOR SELECT TO app_worker_general USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.templates TO app_worker_general;

CREATE POLICY template_versions_worker_general_select ON public.template_versions
FOR SELECT TO app_worker_general USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.template_versions TO app_worker_general;

CREATE POLICY import_jobs_worker_general_select ON public.import_jobs
FOR SELECT TO app_worker_general USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.import_jobs TO app_worker_general;

CREATE POLICY import_row_results_worker_general_select ON public.import_row_results
FOR SELECT TO app_worker_general USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.import_row_results TO app_worker_general;

CREATE POLICY leads_worker_general_insert ON public.leads
FOR INSERT TO app_worker_general WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT INSERT (id, workspace_id, original_address, canonical_address, normalization_version, first_name, last_name, company, title, custom_fields, status, validation_status, validated_at, archived_at) ON public.leads TO app_worker_general;

CREATE POLICY lead_lists_worker_general_insert ON public.lead_lists
FOR INSERT TO app_worker_general WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT INSERT (id, workspace_id, name, archived_at) ON public.lead_lists TO app_worker_general;

CREATE POLICY lead_list_memberships_worker_general_insert ON public.lead_list_memberships
FOR INSERT TO app_worker_general WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT INSERT (workspace_id, list_id, lead_id, added_by, added_at) ON public.lead_list_memberships TO app_worker_general;

CREATE POLICY recipient_addresses_worker_general_insert ON public.recipient_addresses
FOR INSERT TO app_worker_general WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT INSERT (id, workspace_id, canonical_address, normalization_version, original_display) ON public.recipient_addresses TO app_worker_general;

CREATE POLICY suppressions_worker_general_insert ON public.suppressions
FOR INSERT TO app_worker_general WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT INSERT (release_audit_id, id, workspace_id, address_id, reason, status, first_observed_at, last_observed_at, released_at, release_actor_id) ON public.suppressions TO app_worker_general;

CREATE POLICY suppression_sources_worker_general_insert ON public.suppression_sources
FOR INSERT TO app_worker_general WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT INSERT (id, workspace_id, suppression_id, source_kind, source_key, provider_receipt_id, domain_event_id, actor_id, observed_at, evidence) ON public.suppression_sources TO app_worker_general;

CREATE POLICY import_row_results_worker_general_insert ON public.import_row_results
FOR INSERT TO app_worker_general WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT INSERT (workspace_id, import_id, row_number, status, lead_id, suppression_id, validation_reason, error_artifact_ref) ON public.import_row_results TO app_worker_general;

CREATE POLICY unsubscribe_tokens_worker_general_insert ON public.unsubscribe_tokens
FOR INSERT TO app_worker_general WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT INSERT (id, workspace_id, address_id, message_id, token_digest, purpose, expires_at, revoked_at) ON public.unsubscribe_tokens TO app_worker_general;

CREATE POLICY leads_worker_general_update ON public.leads
FOR UPDATE TO app_worker_general USING (workspace_id = (SELECT public.app_current_workspace_id())) WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT UPDATE (original_address, canonical_address, normalization_version, first_name, last_name, company, title, custom_fields, status, validation_status, validated_at, archived_at) ON public.leads TO app_worker_general;

CREATE POLICY lead_lists_worker_general_update ON public.lead_lists
FOR UPDATE TO app_worker_general USING (workspace_id = (SELECT public.app_current_workspace_id())) WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT UPDATE (name, archived_at) ON public.lead_lists TO app_worker_general;

CREATE POLICY suppressions_worker_general_update ON public.suppressions
FOR UPDATE TO app_worker_general USING (workspace_id = (SELECT public.app_current_workspace_id())) WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()) AND status = 'ACTIVE');
GRANT UPDATE (status, last_observed_at, released_at, release_actor_id, release_audit_id) ON public.suppressions TO app_worker_general;

CREATE POLICY import_jobs_worker_general_update ON public.import_jobs
FOR UPDATE TO app_worker_general USING (workspace_id = (SELECT public.app_current_workspace_id())) WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT UPDATE (status, row_cursor, total_rows, processed_rows, accepted_rows, duplicate_rows, rejected_rows, next_due_at, lease_owner, lease_generation, lease_expires_at, failure_summary) ON public.import_jobs TO app_worker_general;

CREATE POLICY recipient_addresses_worker_send_select ON public.recipient_addresses
FOR SELECT TO app_worker_send USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.recipient_addresses TO app_worker_send;

CREATE POLICY suppressions_worker_send_select ON public.suppressions
FOR SELECT TO app_worker_send USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.suppressions TO app_worker_send;

CREATE POLICY platform_controls_worker_send_select ON public.platform_controls
FOR SELECT TO app_worker_send USING (true);
GRANT SELECT ON public.platform_controls TO app_worker_send;

CREATE POLICY platform_suppressions_worker_send_select ON public.platform_suppressions
FOR SELECT TO app_worker_send USING (true);
GRANT SELECT ON public.platform_suppressions TO app_worker_send;

CREATE POLICY recipient_addresses_worker_sync_select ON public.recipient_addresses
FOR SELECT TO app_worker_sync USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.recipient_addresses TO app_worker_sync;

CREATE POLICY suppressions_worker_sync_select ON public.suppressions
FOR SELECT TO app_worker_sync USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.suppressions TO app_worker_sync;

CREATE POLICY platform_controls_worker_sync_select ON public.platform_controls
FOR SELECT TO app_worker_sync USING (true);
GRANT SELECT ON public.platform_controls TO app_worker_sync;

CREATE POLICY platform_suppressions_worker_sync_select ON public.platform_suppressions
FOR SELECT TO app_worker_sync USING (true);
GRANT SELECT ON public.platform_suppressions TO app_worker_sync;

CREATE POLICY recipient_addresses_scheduler_select ON public.recipient_addresses
FOR SELECT TO app_scheduler USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.recipient_addresses TO app_scheduler;

CREATE POLICY suppressions_scheduler_select ON public.suppressions
FOR SELECT TO app_scheduler USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.suppressions TO app_scheduler;

CREATE POLICY platform_controls_scheduler_select ON public.platform_controls
FOR SELECT TO app_scheduler USING (true);
GRANT SELECT ON public.platform_controls TO app_scheduler;

CREATE POLICY platform_suppressions_scheduler_select ON public.platform_suppressions
FOR SELECT TO app_scheduler USING (true);
GRANT SELECT ON public.platform_suppressions TO app_scheduler;

CREATE POLICY lead_lists_integrity_guard_select ON public.lead_lists
FOR SELECT TO app_integrity_guard USING (true);
GRANT SELECT ON public.lead_lists TO app_integrity_guard;

CREATE POLICY suppressions_integrity_guard_select ON public.suppressions
FOR SELECT TO app_integrity_guard USING (true);
GRANT SELECT ON public.suppressions TO app_integrity_guard;

CREATE POLICY recipient_addresses_integrity_guard_select ON public.recipient_addresses
FOR SELECT TO app_integrity_guard USING (true);
GRANT SELECT ON public.recipient_addresses TO app_integrity_guard;

CREATE POLICY lead_lists_integrity_guard_update ON public.lead_lists
FOR UPDATE TO app_integrity_guard USING (true) WITH CHECK (true);
GRANT UPDATE (membership_revision, capture_count) ON public.lead_lists TO app_integrity_guard;

CREATE FUNCTION public.app_initial_suppression() RETURNS trigger
LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path = ''
AS $function$
BEGIN
    IF NEW.status <> 'ACTIVE' THEN RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='New suppressions must be active'; END IF;
    RETURN NEW;
END;
$function$;
REVOKE ALL ON FUNCTION public.app_initial_suppression() FROM PUBLIC, anon, authenticated, service_role;

CREATE TRIGGER suppressions_initial_suppression BEFORE INSERT ON public.suppressions FOR EACH ROW EXECUTE FUNCTION public.app_initial_suppression();

ALTER TABLE public.suppression_sources ADD CONSTRAINT suppression_sources_source_identity_check CHECK ((source_kind <> 'PROVIDER_RECEIPT' OR source_key = provider_receipt_id::text) AND (source_kind <> 'DOMAIN_EVENT' OR source_key = domain_event_id::text));

ALTER TABLE public.unsubscribe_tokens ADD CONSTRAINT unsubscribe_tokens_origin_check CHECK (message_id IS NOT NULL);

ALTER TABLE public.import_jobs ADD CONSTRAINT import_jobs_processing_lease_check CHECK (status <> 'PROCESSING' OR (lease_owner IS NOT NULL AND next_due_at IS NOT NULL));

ALTER TABLE public.import_jobs ADD CONSTRAINT import_jobs_failure_reason_check CHECK (status <> 'FAILED' OR (failure_summary IS NOT NULL AND pg_catalog.length(failure_summary) > 0));

CREATE FUNCTION public.app_guard_import_result() RETURNS trigger
LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path = ''
AS $function$
DECLARE kind text;
BEGIN
    SELECT import_kind INTO STRICT kind FROM public.import_jobs WHERE workspace_id=NEW.workspace_id AND id=NEW.import_id;
    IF NEW.status <> 'REJECTED' AND ((kind='LEADS' AND NEW.lead_id IS NULL) OR (kind='SUPPRESSION' AND NEW.suppression_id IS NULL)) THEN
        RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Import result does not match import kind';
    END IF;
    RETURN NEW;
END;
$function$;
REVOKE ALL ON FUNCTION public.app_guard_import_result() FROM PUBLIC, anon, authenticated, service_role;

CREATE TRIGGER import_row_results_guard_import_result BEFORE INSERT ON public.import_row_results FOR EACH ROW EXECUTE FUNCTION public.app_guard_import_result();

GRANT SELECT ON public.recipient_addresses TO app_connection;
CREATE POLICY recipient_addresses_app_connection_additional_read ON public.recipient_addresses FOR SELECT TO app_connection USING (workspace_id=public.app_current_workspace_id() AND public.app_has_permission('mailboxes.manage'));

CREATE INDEX import_jobs_lease_recovery_idx ON public.import_jobs (workspace_id, lease_expires_at, id) WHERE lease_owner IS NOT NULL;

COMMIT;
