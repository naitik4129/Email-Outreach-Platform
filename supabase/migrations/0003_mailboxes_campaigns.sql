-- Mailbox/campaign draft corrected with the full five-file chain.
-- app_connection is current-user/membership scoped and owns protected connection
-- operations. Send/sync can access credentials; general processing cannot.
-- One provider account may be active in only one workspace. Campaign execution
-- requires Manager or above. See docs/database/MIGRATION_REVIEW.md.
-- Prepared only: no migration execution was performed.

BEGIN;
SET LOCAL search_path = '';

-- ---------------------------------------------------------------------------
-- Mailboxes and provider credentials
-- ---------------------------------------------------------------------------

CREATE TABLE public.mailboxes (
    connected_generation bigint,
    id uuid DEFAULT pg_catalog.gen_random_uuid() CONSTRAINT mailboxes_pkey PRIMARY KEY,
    workspace_id uuid NOT NULL,
    provider text NOT NULL,
    provider_account_id text,
    original_address text NOT NULL,
    sender_display_name text,
    signature_html text,
    connection_state text NOT NULL DEFAULT 'CONNECTING',
    health_state text NOT NULL DEFAULT 'UNKNOWN',
    policy_state text NOT NULL DEFAULT 'ENABLED',
    policy_reason text,
    circuit_state text NOT NULL DEFAULT 'CLOSED',
    sync_state text NOT NULL DEFAULT 'INITIALIZING',
    blocked_until timestamptz,
    pending_safety_count bigint NOT NULL DEFAULT 0,
    current_connection_generation bigint NOT NULL DEFAULT 1,
    config_version bigint NOT NULL DEFAULT 1,
    version bigint NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    CONSTRAINT mailboxes_workspace_fkey FOREIGN KEY (workspace_id)
        REFERENCES public.workspaces (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT mailboxes_workspace_id_key UNIQUE (workspace_id, id),
    CONSTRAINT mailboxes_provider_account_key UNIQUE (workspace_id, provider, provider_account_id),
    CONSTRAINT mailboxes_provider_check CHECK (provider IN ('GMAIL', 'MICROSOFT', 'SMTP')),
    CONSTRAINT mailboxes_provider_account_id_check CHECK (
        pg_catalog.char_length(provider_account_id) BETWEEN 1 AND 320
    ),
    CONSTRAINT mailboxes_original_address_check CHECK (
        pg_catalog.char_length(original_address) BETWEEN 3 AND 320 AND original_address ~ '@'
    ),
    CONSTRAINT mailboxes_sender_display_name_check CHECK (
        sender_display_name IS NULL OR pg_catalog.char_length(sender_display_name) <= 200
    ),
    CONSTRAINT mailboxes_signature_html_check CHECK (
        signature_html IS NULL OR pg_catalog.char_length(signature_html) <= 20000
    ),
    CONSTRAINT mailboxes_connection_state_check CHECK (
        connection_state IN ('CONNECTING', 'CONNECTED', 'RECONNECT_REQUIRED', 'DISCONNECTED')
    ),
    CONSTRAINT mailboxes_health_state_check CHECK (health_state IN ('UNKNOWN', 'HEALTHY', 'DEGRADED')),
    CONSTRAINT mailboxes_policy_state_check CHECK (policy_state IN ('ENABLED', 'RESTRICTED')),
    CONSTRAINT mailboxes_policy_reason_check CHECK ((policy_state = 'RESTRICTED') = (policy_reason IS NOT NULL)),
    CONSTRAINT mailboxes_circuit_state_check CHECK (circuit_state IN ('CLOSED', 'OPEN', 'HALF_OPEN')),
    CONSTRAINT mailboxes_sync_state_check CHECK (
        sync_state IN ('INITIALIZING', 'CURRENT', 'LAGGING', 'RESYNC_REQUIRED', 'UNAVAILABLE')
    ),
    CONSTRAINT mailboxes_pending_safety_count_check CHECK (pending_safety_count >= 0),
    CONSTRAINT mailboxes_connection_generation_check CHECK (current_connection_generation > 0),
    CONSTRAINT mailboxes_config_version_check CHECK (config_version > 0),
    CONSTRAINT mailboxes_version_check CHECK (version > 0),
    CONSTRAINT mailboxes_known_identity_check CHECK (connection_state = 'CONNECTING' OR provider_account_id IS NOT NULL),
    CONSTRAINT mailboxes_connected_generation_check CHECK (
        (connection_state = 'CONNECTED' AND connected_generation = current_connection_generation AND connected_generation IS NOT NULL)
        OR (connection_state <> 'CONNECTED' AND connected_generation IS NULL))
);

-- Resolved decision: a real provider account may be actively connected to at
-- most one workspace platform-wide. A disconnected mailbox frees the account.
CREATE UNIQUE INDEX mailboxes_provider_account_global_key
    ON public.mailboxes (provider, provider_account_id)
    WHERE connection_state <> 'DISCONNECTED';
CREATE INDEX mailboxes_workspace_policy_idx ON public.mailboxes (workspace_id, policy_state, id);
CREATE INDEX mailboxes_provider_blocked_idx ON public.mailboxes (provider, blocked_until, id);

CREATE TABLE public.mailbox_connections (
    destroyed_at timestamptz,
    id uuid DEFAULT pg_catalog.gen_random_uuid() CONSTRAINT mailbox_connections_pkey PRIMARY KEY,
    workspace_id uuid NOT NULL,
    mailbox_id uuid NOT NULL,
    generation bigint NOT NULL,
    credential_ciphertext bytea,
    encryption_key_id text,
    nonce bytea,
    auth_mechanism text NOT NULL,
    granted_scopes jsonb NOT NULL DEFAULT '[]'::jsonb,
    protected_config jsonb,
    expires_at timestamptz,
    refresh_lease_owner text,
    refresh_lease_generation bigint NOT NULL DEFAULT 1,
    refresh_lease_expires_at timestamptz,
    revoked_at timestamptz,
    version bigint NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    CONSTRAINT mailbox_connections_workspace_fkey FOREIGN KEY (workspace_id)
        REFERENCES public.workspaces (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT mailbox_connections_mailbox_fkey FOREIGN KEY (workspace_id, mailbox_id)
        REFERENCES public.mailboxes (workspace_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT mailbox_connections_workspace_id_key UNIQUE (workspace_id, id),
    CONSTRAINT mailbox_connections_generation_key UNIQUE (workspace_id, mailbox_id, generation),
    CONSTRAINT mailbox_connections_generation_check CHECK (generation > 0),
    CONSTRAINT mailbox_connections_auth_mechanism_check CHECK (
        auth_mechanism IN ('OAUTH', 'SMTP_PASSWORD')
    ),
    CONSTRAINT mailbox_connections_scopes_check CHECK (pg_catalog.jsonb_typeof(granted_scopes) = 'array'),
    CONSTRAINT mailbox_connections_config_check CHECK (
        protected_config IS NULL
        OR (
            pg_catalog.jsonb_typeof(protected_config) = 'object'
            AND pg_catalog.octet_length(pg_catalog.convert_to(protected_config::text, 'UTF8')) <= 16384
        )
    ),
    CONSTRAINT mailbox_connections_refresh_lease_generation_check CHECK (refresh_lease_generation > 0),
    CONSTRAINT mailbox_connections_version_check CHECK (version > 0),
    CONSTRAINT mailbox_connections_envelope_check CHECK (
    (destroyed_at IS NULL AND credential_ciphertext IS NOT NULL AND pg_catalog.octet_length(credential_ciphertext) BETWEEN 1 AND 65536
      AND encryption_key_id IS NOT NULL AND pg_catalog.char_length(encryption_key_id) BETWEEN 1 AND 200
      AND nonce IS NOT NULL AND pg_catalog.octet_length(nonce) BETWEEN 12 AND 32)
    OR (destroyed_at IS NOT NULL AND revoked_at IS NOT NULL AND credential_ciphertext IS NULL AND encryption_key_id IS NULL
      AND nonce IS NULL AND protected_config IS NULL)),
    CONSTRAINT mailbox_connections_lease_check CHECK ((refresh_lease_owner IS NULL) = (refresh_lease_expires_at IS NULL)),
    CONSTRAINT mailbox_connections_scopes_size_check CHECK (pg_catalog.octet_length(pg_catalog.convert_to(granted_scopes::text, 'UTF8')) <= 8192)
);

CREATE INDEX mailbox_connections_refresh_lease_idx
    ON public.mailbox_connections (refresh_lease_expires_at, id)
    WHERE revoked_at IS NULL;

CREATE TABLE public.oauth_flows (
    claim_owner text,
    claim_expires_at timestamptz,
    verifier_key_id text,
    verifier_nonce bytea,
    id uuid DEFAULT pg_catalog.gen_random_uuid() CONSTRAINT oauth_flows_pkey PRIMARY KEY,
    workspace_id uuid NOT NULL,
    actor_id uuid NOT NULL,
    provider text NOT NULL,
    state_digest text NOT NULL,
    encrypted_verifier bytea,
    return_path text NOT NULL,
    status text NOT NULL DEFAULT 'PENDING',
    claim_generation bigint NOT NULL DEFAULT 1,
    resulting_mailbox_id uuid,
    expires_at timestamptz NOT NULL,
    version bigint NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    CONSTRAINT oauth_flows_workspace_fkey FOREIGN KEY (workspace_id)
        REFERENCES public.workspaces (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT oauth_flows_actor_fkey FOREIGN KEY (actor_id)
        REFERENCES public.profiles (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT oauth_flows_mailbox_fkey FOREIGN KEY (workspace_id, resulting_mailbox_id)
        REFERENCES public.mailboxes (workspace_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT oauth_flows_workspace_id_key UNIQUE (workspace_id, id),
    CONSTRAINT oauth_flows_state_digest_key UNIQUE (state_digest),
    CONSTRAINT oauth_flows_state_digest_check CHECK (state_digest ~ '^[0-9a-f]{64,128}$'),
    CONSTRAINT oauth_flows_provider_check CHECK (provider IN ('GMAIL', 'MICROSOFT')),
    CONSTRAINT oauth_flows_return_path_check CHECK (pg_catalog.char_length(return_path) BETWEEN 1 AND 1024),
    CONSTRAINT oauth_flows_status_check CHECK (status IN ('PENDING', 'CLAIMED', 'COMPLETED', 'FAILED')),
    CONSTRAINT oauth_flows_claim_generation_check CHECK (claim_generation > 0),
    CONSTRAINT oauth_flows_version_check CHECK (version > 0),
    CONSTRAINT oauth_flows_expiry_check CHECK (expires_at > created_at),
    CONSTRAINT oauth_flows_claim_check CHECK ((status = 'CLAIMED') = (claim_owner IS NOT NULL AND claim_expires_at IS NOT NULL)
        AND (claim_owner IS NULL) = (claim_expires_at IS NULL)),
    CONSTRAINT oauth_flows_result_check CHECK ((status = 'COMPLETED') = (resulting_mailbox_id IS NOT NULL)),
    CONSTRAINT oauth_flows_verifier_check CHECK (
        (encrypted_verifier IS NULL AND verifier_key_id IS NULL AND verifier_nonce IS NULL)
        OR (encrypted_verifier IS NOT NULL AND pg_catalog.octet_length(encrypted_verifier) BETWEEN 1 AND 4096
            AND verifier_key_id IS NOT NULL AND pg_catalog.char_length(verifier_key_id) BETWEEN 1 AND 200
            AND verifier_nonce IS NOT NULL AND pg_catalog.octet_length(verifier_nonce) BETWEEN 12 AND 32)),
    CONSTRAINT oauth_flows_return_path_shape_check CHECK (return_path LIKE '/app/%' AND return_path !~ '[[:cntrl:]\\]')
);

CREATE INDEX oauth_flows_due_idx
    ON public.oauth_flows (expires_at, id)
    WHERE status IN ('PENDING', 'CLAIMED');

CREATE TABLE public.mailbox_sync_states (
    id uuid DEFAULT pg_catalog.gen_random_uuid() CONSTRAINT mailbox_sync_states_pkey PRIMARY KEY,
    workspace_id uuid NOT NULL,
    mailbox_id uuid NOT NULL,
    connection_generation bigint NOT NULL,
    sync_scope text NOT NULL,
    cursor_data text,
    lease_owner text,
    lease_generation bigint NOT NULL DEFAULT 1,
    lease_expires_at timestamptz,
    next_due_at timestamptz,
    last_complete_at timestamptz,
    status text NOT NULL DEFAULT 'INITIALIZING',
    subscription_expires_at timestamptz,
    failure_count integer NOT NULL DEFAULT 0,
    version bigint NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    CONSTRAINT mailbox_sync_states_workspace_fkey FOREIGN KEY (workspace_id)
        REFERENCES public.workspaces (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT mailbox_sync_states_mailbox_fkey FOREIGN KEY (workspace_id, mailbox_id)
        REFERENCES public.mailboxes (workspace_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT mailbox_sync_states_workspace_id_key UNIQUE (workspace_id, id),
    CONSTRAINT mailbox_sync_states_scope_key UNIQUE (workspace_id, mailbox_id, sync_scope),
    CONSTRAINT mailbox_sync_states_connection_generation_check CHECK (connection_generation > 0),
    CONSTRAINT mailbox_sync_states_lease_generation_check CHECK (lease_generation > 0),
    CONSTRAINT mailbox_sync_states_status_check CHECK (
        status IN ('INITIALIZING', 'CURRENT', 'LAGGING', 'RESYNC_REQUIRED', 'UNAVAILABLE')
    ),
    CONSTRAINT mailbox_sync_states_failure_count_check CHECK (failure_count >= 0),
    CONSTRAINT mailbox_sync_states_version_check CHECK (version > 0),
    CONSTRAINT mailbox_sync_states_connection_fkey FOREIGN KEY (workspace_id, mailbox_id, connection_generation)
    REFERENCES public.mailbox_connections (workspace_id, mailbox_id, generation) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT mailbox_sync_states_lease_check CHECK ((lease_owner IS NULL) = (lease_expires_at IS NULL)),
    CONSTRAINT mailbox_sync_states_scope_check CHECK (pg_catalog.char_length(sync_scope) BETWEEN 1 AND 512),
    CONSTRAINT mailbox_sync_states_cursor_check CHECK (cursor_data IS NULL OR pg_catalog.octet_length(pg_catalog.convert_to(cursor_data, 'UTF8')) <= 32768)
);

CREATE INDEX mailbox_sync_states_due_idx ON public.mailbox_sync_states (next_due_at, id);
CREATE INDEX mailbox_sync_states_lease_idx
    ON public.mailbox_sync_states (lease_expires_at, id)
    WHERE lease_owner IS NOT NULL;

-- ---------------------------------------------------------------------------
-- Campaigns, sequences and configuration
-- ---------------------------------------------------------------------------

CREATE TABLE public.campaigns (
    current_settings_id uuid,
    previous_status text,
    id uuid DEFAULT pg_catalog.gen_random_uuid() CONSTRAINT campaigns_pkey PRIMARY KEY,
    workspace_id uuid NOT NULL,
    name text NOT NULL,
    description text,
    creator_id uuid NOT NULL,
    status text NOT NULL DEFAULT 'DRAFT',
    start_at timestamptz,
    draft_sequence_id uuid,
    activated_sequence_id uuid,
    draft_audience_id uuid,
    activated_audience_id uuid,
    activation_id uuid,
    planning_status text NOT NULL DEFAULT 'PENDING',
    schedule_generation bigint NOT NULL DEFAULT 1,
    archived_at timestamptz,
    error_reason text,
    version bigint NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    CONSTRAINT campaigns_workspace_fkey FOREIGN KEY (workspace_id)
        REFERENCES public.workspaces (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT campaigns_creator_fkey FOREIGN KEY (creator_id)
        REFERENCES public.profiles (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT campaigns_workspace_id_key UNIQUE (workspace_id, id),
    -- Candidate key so ENROLL/RENDER planning jobs can validate against a
    -- specific activation without a forward reference to their own table.
    CONSTRAINT campaigns_activation_key UNIQUE (workspace_id, id, activation_id),
    CONSTRAINT campaigns_name_check CHECK (
        pg_catalog.char_length(name) BETWEEN 1 AND 200 AND name ~ '[^[:space:]]'
    ),
    CONSTRAINT campaigns_description_check CHECK (description IS NULL OR pg_catalog.char_length(description) <= 2000),
    CONSTRAINT campaigns_status_check CHECK (
        status IN ('DRAFT', 'SCHEDULED', 'RUNNING', 'PAUSED', 'ERROR', 'COMPLETED', 'ARCHIVED')
    ),
    CONSTRAINT campaigns_planning_status_check CHECK (planning_status IN ('PENDING', 'READY')),
    CONSTRAINT campaigns_schedule_generation_check CHECK (schedule_generation > 0),
    CONSTRAINT campaigns_archive_check CHECK ((status = 'ARCHIVED') = (archived_at IS NOT NULL)),
    CONSTRAINT campaigns_error_reason_check CHECK (status <> 'ERROR' OR error_reason IS NOT NULL),
    CONSTRAINT campaigns_version_check CHECK (version > 0),
    CONSTRAINT campaigns_activation_shape_check CHECK (
    (activation_id IS NULL AND activated_sequence_id IS NULL AND activated_audience_id IS NULL AND status IN ('DRAFT','ARCHIVED'))
    OR (activation_id IS NOT NULL AND activated_sequence_id IS NOT NULL AND activated_audience_id IS NOT NULL
        AND current_settings_id IS NOT NULL AND start_at IS NOT NULL AND status <> 'DRAFT')),
    CONSTRAINT campaigns_previous_status_check CHECK (previous_status IS NULL OR previous_status IN
        ('DRAFT','SCHEDULED','RUNNING','PAUSED','ERROR','COMPLETED')),
    CONSTRAINT campaigns_history_status_check CHECK (status NOT IN ('ERROR','ARCHIVED') OR previous_status IS NOT NULL)
);

CREATE INDEX campaigns_workspace_status_idx ON public.campaigns (workspace_id, status, id);
-- System due-activation scan (cross-tenant); reachable only through app_worker_general's
-- narrowly scoped discovery query, never a general app_api table scan.
CREATE INDEX campaigns_status_start_idx ON public.campaigns (status, start_at, id);

CREATE TABLE public.campaign_sequences (
    id uuid DEFAULT pg_catalog.gen_random_uuid() CONSTRAINT campaign_sequences_pkey PRIMARY KEY,
    workspace_id uuid NOT NULL,
    campaign_id uuid NOT NULL,
    revision integer NOT NULL,
    status text NOT NULL DEFAULT 'DRAFT',
    frozen_at timestamptz,
    content_digest text,
    version bigint NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    CONSTRAINT campaign_sequences_workspace_fkey FOREIGN KEY (workspace_id)
        REFERENCES public.workspaces (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT campaign_sequences_campaign_fkey FOREIGN KEY (workspace_id, campaign_id)
        REFERENCES public.campaigns (workspace_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT campaign_sequences_workspace_id_key UNIQUE (workspace_id, id),
    CONSTRAINT campaign_sequences_revision_key UNIQUE (workspace_id, campaign_id, revision),
    CONSTRAINT campaign_sequences_campaign_id_key UNIQUE (workspace_id, campaign_id, id),
    CONSTRAINT campaign_sequences_revision_check CHECK (revision > 0),
    CONSTRAINT campaign_sequences_status_check CHECK (status IN ('DRAFT', 'FROZEN')),
    CONSTRAINT campaign_sequences_frozen_check CHECK ((status = 'FROZEN') = (frozen_at IS NOT NULL)),
    CONSTRAINT campaign_sequences_version_check CHECK (version > 0)
);

CREATE TABLE public.sequence_steps (
    id uuid DEFAULT pg_catalog.gen_random_uuid() CONSTRAINT sequence_steps_pkey PRIMARY KEY,
    workspace_id uuid NOT NULL,
    sequence_id uuid NOT NULL,
    campaign_id uuid NOT NULL,
    position integer NOT NULL,
    kind text NOT NULL,
    email_subject text,
    email_body_html text,
    email_variable_schema jsonb,
    wait_duration_minutes integer,
    source_template_version_id uuid,
    version bigint NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    CONSTRAINT sequence_steps_workspace_fkey FOREIGN KEY (workspace_id)
        REFERENCES public.workspaces (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT sequence_steps_sequence_fkey FOREIGN KEY (workspace_id, campaign_id, sequence_id)
        REFERENCES public.campaign_sequences (workspace_id, campaign_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT sequence_steps_campaign_fkey FOREIGN KEY (workspace_id, campaign_id)
        REFERENCES public.campaigns (workspace_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT sequence_steps_template_version_fkey FOREIGN KEY (workspace_id, source_template_version_id)
        REFERENCES public.template_versions (workspace_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT sequence_steps_workspace_id_key UNIQUE (workspace_id, id),
    CONSTRAINT sequence_steps_position_key UNIQUE (workspace_id, sequence_id, position),
    CONSTRAINT sequence_steps_sequence_id_key UNIQUE (workspace_id, sequence_id, id),
    CONSTRAINT sequence_steps_position_check CHECK (position > 0),
    CONSTRAINT sequence_steps_kind_check CHECK (kind IN ('EMAIL', 'WAIT')),
    CONSTRAINT sequence_steps_payload_shape_check CHECK (
        (kind = 'EMAIL' AND email_subject IS NOT NULL AND email_body_html IS NOT NULL
            AND wait_duration_minutes IS NULL)
        OR (kind = 'WAIT' AND wait_duration_minutes IS NOT NULL AND wait_duration_minutes > 0
            AND email_subject IS NULL AND email_body_html IS NULL)
    ),
    CONSTRAINT sequence_steps_email_subject_check CHECK (
        email_subject IS NULL OR pg_catalog.char_length(email_subject) BETWEEN 1 AND 500
    ),
    CONSTRAINT sequence_steps_email_body_check CHECK (
        email_body_html IS NULL OR pg_catalog.char_length(email_body_html) <= 200000
    ),
    CONSTRAINT sequence_steps_variable_schema_check CHECK (
        email_variable_schema IS NULL
        OR (
            pg_catalog.jsonb_typeof(email_variable_schema) = 'object'
            AND pg_catalog.octet_length(pg_catalog.convert_to(email_variable_schema::text, 'UTF8')) <= 16384
        )
    ),
    CONSTRAINT sequence_steps_version_check CHECK (version > 0)
);

-- Alternation/start/end-with-Email validity is a multi-row structural rule
-- (CAMPAIGN_ENGINE.md) that a single-row CHECK cannot express; it is enforced
-- by the activation service, not this migration.


CREATE TABLE public.campaign_settings_versions (
    id uuid DEFAULT pg_catalog.gen_random_uuid() CONSTRAINT campaign_settings_versions_pkey PRIMARY KEY,
    workspace_id uuid NOT NULL,
    campaign_id uuid NOT NULL,
    revision integer NOT NULL,
    generation bigint NOT NULL DEFAULT 1,
    timezone text NOT NULL,
    weekday_set integer NOT NULL,
    window_start_local time NOT NULL,
    window_end_local time NOT NULL,
    lower_bound_start timestamptz,
    daily_limit integer,
    enabled_settings jsonb NOT NULL DEFAULT '{}'::jsonb,
    computation_version integer NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    CONSTRAINT campaign_settings_versions_workspace_fkey FOREIGN KEY (workspace_id)
        REFERENCES public.workspaces (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT campaign_settings_versions_campaign_fkey FOREIGN KEY (workspace_id, campaign_id)
        REFERENCES public.campaigns (workspace_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT campaign_settings_versions_workspace_id_key UNIQUE (workspace_id, id),
    CONSTRAINT campaign_settings_versions_revision_key UNIQUE (workspace_id, campaign_id, revision),
    CONSTRAINT campaign_settings_versions_revision_check CHECK (revision > 0),
    CONSTRAINT campaign_settings_versions_generation_check CHECK (generation > 0),
    CONSTRAINT campaign_settings_versions_timezone_check CHECK (pg_catalog.char_length(timezone) BETWEEN 1 AND 100),
    -- Half-open [start,end) same-day window per SCHEDULER.md; overnight windows unsupported in MVP.
    CONSTRAINT campaign_settings_versions_weekday_set_check CHECK (weekday_set BETWEEN 1 AND 127),
    CONSTRAINT campaign_settings_versions_window_check CHECK (window_start_local < window_end_local),
    CONSTRAINT campaign_settings_versions_daily_limit_check CHECK (daily_limit IS NULL OR daily_limit > 0),
    CONSTRAINT campaign_settings_versions_enabled_settings_check CHECK (
        pg_catalog.jsonb_typeof(enabled_settings) = 'object'
        AND pg_catalog.octet_length(pg_catalog.convert_to(enabled_settings::text, 'UTF8')) <= 16384
    ),
    CONSTRAINT campaign_settings_versions_computation_version_check CHECK (computation_version > 0),
    CONSTRAINT campaign_settings_versions_campaign_id_key UNIQUE (workspace_id, campaign_id, id)
);

CREATE TABLE public.campaign_mailboxes (
    workspace_id uuid NOT NULL,
    campaign_id uuid NOT NULL,
    mailbox_id uuid NOT NULL,
    active boolean NOT NULL DEFAULT true,
    config_revision bigint NOT NULL DEFAULT 1,
    allocation_position integer NOT NULL DEFAULT 0,
    created_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    CONSTRAINT campaign_mailboxes_pkey PRIMARY KEY (workspace_id, campaign_id, mailbox_id),
    CONSTRAINT campaign_mailboxes_campaign_fkey FOREIGN KEY (workspace_id, campaign_id)
        REFERENCES public.campaigns (workspace_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT campaign_mailboxes_mailbox_fkey FOREIGN KEY (workspace_id, mailbox_id)
        REFERENCES public.mailboxes (workspace_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT campaign_mailboxes_config_revision_check CHECK (config_revision > 0),
    CONSTRAINT campaign_mailboxes_allocation_position_check CHECK (allocation_position >= 0)
);

CREATE INDEX campaign_mailboxes_reverse_idx ON public.campaign_mailboxes (workspace_id, mailbox_id, campaign_id);

CREATE TABLE public.campaign_audiences (
    selection_manifest jsonb NOT NULL DEFAULT '{}'::jsonb,
    id uuid DEFAULT pg_catalog.gen_random_uuid() CONSTRAINT campaign_audiences_pkey PRIMARY KEY,
    workspace_id uuid NOT NULL,
    campaign_id uuid NOT NULL,
    revision integer NOT NULL,
    status text NOT NULL DEFAULT 'CAPTURING',
    source_manifest_digest text,
    started_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    completed_at timestamptz,
    capture_generation bigint NOT NULL DEFAULT 1,
    version bigint NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    CONSTRAINT campaign_audiences_workspace_fkey FOREIGN KEY (workspace_id)
        REFERENCES public.workspaces (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT campaign_audiences_campaign_fkey FOREIGN KEY (workspace_id, campaign_id)
        REFERENCES public.campaigns (workspace_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT campaign_audiences_workspace_id_key UNIQUE (workspace_id, id),
    CONSTRAINT campaign_audiences_revision_key UNIQUE (workspace_id, campaign_id, revision),
    CONSTRAINT campaign_audiences_campaign_id_key UNIQUE (workspace_id, campaign_id, id),
    CONSTRAINT campaign_audiences_revision_check CHECK (revision > 0),
    CONSTRAINT campaign_audiences_status_check CHECK (status IN ('CAPTURING', 'READY', 'FAILED', 'ABANDONED')),
    CONSTRAINT campaign_audiences_completion_check CHECK (
        (status = 'CAPTURING') = (completed_at IS NULL)
    ),
    CONSTRAINT campaign_audiences_capture_generation_check CHECK (capture_generation > 0),
    CONSTRAINT campaign_audiences_version_check CHECK (version > 0),
    CONSTRAINT campaign_audiences_selection_check CHECK (
    pg_catalog.jsonb_typeof(selection_manifest) = 'object'
    AND pg_catalog.octet_length(pg_catalog.convert_to(selection_manifest::text, 'UTF8')) <= 65536),
    CONSTRAINT campaign_audiences_ready_check CHECK (status <> 'READY' OR (source_manifest_digest IS NOT NULL AND source_manifest_digest ~ '^[0-9a-f]{64}$'))
);

CREATE UNIQUE INDEX campaign_audiences_active_capture_idx
    ON public.campaign_audiences (workspace_id, campaign_id)
    WHERE status = 'CAPTURING';

CREATE TABLE public.audience_capture_sources (
    workspace_id uuid NOT NULL,
    campaign_id uuid NOT NULL,
    audience_id uuid NOT NULL,
    list_id uuid NOT NULL,
    captured_list_revision bigint NOT NULL,
    gate_held boolean NOT NULL DEFAULT true,
    capture_generation bigint NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    released_at timestamptz,
    CONSTRAINT audience_capture_sources_pkey PRIMARY KEY (workspace_id, audience_id, list_id),
    CONSTRAINT audience_capture_sources_audience_fkey FOREIGN KEY (workspace_id, campaign_id, audience_id)
        REFERENCES public.campaign_audiences (workspace_id, campaign_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT audience_capture_sources_list_fkey FOREIGN KEY (workspace_id, list_id)
        REFERENCES public.lead_lists (workspace_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT audience_capture_sources_revision_check CHECK (captured_list_revision >= 0),
    CONSTRAINT audience_capture_sources_generation_check CHECK (capture_generation > 0),
    CONSTRAINT audience_capture_sources_release_check CHECK (gate_held = (released_at IS NULL))
);

CREATE INDEX audience_capture_sources_gate_idx
    ON public.audience_capture_sources (workspace_id, list_id, gate_held);

CREATE TABLE public.campaign_audience_members (
    id uuid DEFAULT pg_catalog.gen_random_uuid() CONSTRAINT campaign_audience_members_pkey PRIMARY KEY,
    workspace_id uuid NOT NULL,
    campaign_id uuid NOT NULL,
    audience_id uuid NOT NULL,
    lead_id uuid NOT NULL,
    address_id uuid NOT NULL,
    capture_ordinal bigint NOT NULL,
    contact_revision bigint NOT NULL,
    frozen_variables jsonb NOT NULL DEFAULT '{}'::jsonb,
    eligibility_status text NOT NULL DEFAULT 'ACCEPTED',
    exclusion_reason text,
    created_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    CONSTRAINT campaign_audience_members_audience_fkey FOREIGN KEY (workspace_id, campaign_id, audience_id)
        REFERENCES public.campaign_audiences (workspace_id, campaign_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT campaign_audience_members_lead_fkey FOREIGN KEY (workspace_id, lead_id)
        REFERENCES public.leads (workspace_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT campaign_audience_members_address_fkey FOREIGN KEY (workspace_id, address_id)
        REFERENCES public.recipient_addresses (workspace_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT campaign_audience_members_workspace_id_key UNIQUE (workspace_id, id),
    CONSTRAINT campaign_audience_members_audience_id_key UNIQUE (workspace_id, audience_id, id),
    CONSTRAINT campaign_audience_members_lead_key UNIQUE (workspace_id, audience_id, lead_id),
    CONSTRAINT campaign_audience_members_address_key UNIQUE (workspace_id, audience_id, address_id),
    CONSTRAINT campaign_audience_members_ordinal_check CHECK (capture_ordinal >= 0),
    CONSTRAINT campaign_audience_members_contact_revision_check CHECK (contact_revision > 0),
    CONSTRAINT campaign_audience_members_frozen_variables_check CHECK (
        pg_catalog.jsonb_typeof(frozen_variables) = 'object'
        AND pg_catalog.octet_length(pg_catalog.convert_to(frozen_variables::text, 'UTF8')) <= 16384
    ),
    CONSTRAINT campaign_audience_members_eligibility_check CHECK (eligibility_status IN ('ACCEPTED', 'EXCLUDED')),
    CONSTRAINT campaign_audience_members_exclusion_reason_check CHECK (
        (eligibility_status = 'EXCLUDED') = (exclusion_reason IS NOT NULL)
    ),
    CONSTRAINT campaign_audience_members_capture_identity_key
    UNIQUE (workspace_id, campaign_id, audience_id, id, lead_id, address_id),
    CONSTRAINT campaign_audience_members_ordinal_key UNIQUE (workspace_id, audience_id, capture_ordinal)
);

-- The ordinal candidate key also supports ordered capture traversal.

CREATE TABLE public.campaign_planning_jobs (
    id uuid DEFAULT pg_catalog.gen_random_uuid() CONSTRAINT campaign_planning_jobs_pkey PRIMARY KEY,
    workspace_id uuid NOT NULL,
    campaign_id uuid NOT NULL,
    audience_id uuid,
    activation_id uuid,
    sequence_id uuid,
    phase text NOT NULL,
    state text NOT NULL DEFAULT 'PENDING',
    cursor_data text,
    total_count bigint,
    processed_count bigint NOT NULL DEFAULT 0,
    lease_owner text,
    lease_generation bigint NOT NULL DEFAULT 1,
    lease_expires_at timestamptz,
    next_due_at timestamptz,
    error_reason text,
    version bigint NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    CONSTRAINT campaign_planning_jobs_workspace_fkey FOREIGN KEY (workspace_id)
        REFERENCES public.workspaces (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT campaign_planning_jobs_capture_fkey FOREIGN KEY (workspace_id, campaign_id, audience_id)
        REFERENCES public.campaign_audiences (workspace_id, campaign_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT campaign_planning_jobs_activation_fkey FOREIGN KEY (workspace_id, campaign_id, activation_id)
        REFERENCES public.campaigns (workspace_id, id, activation_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT campaign_planning_jobs_workspace_id_key UNIQUE (workspace_id, id),
    CONSTRAINT campaign_planning_jobs_phase_check CHECK (phase IN ('CAPTURE', 'ENROLL', 'RENDER')),
    CONSTRAINT campaign_planning_jobs_phase_shape_check CHECK (
        (phase = 'CAPTURE' AND audience_id IS NOT NULL AND activation_id IS NULL)
        OR (phase IN ('ENROLL', 'RENDER') AND activation_id IS NOT NULL AND audience_id IS NULL)
    ),
    CONSTRAINT campaign_planning_jobs_state_check CHECK (state IN ('PENDING', 'PROCESSING', 'READY', 'FAILED')),
    CONSTRAINT campaign_planning_jobs_total_count_check CHECK (total_count IS NULL OR total_count >= 0),
    CONSTRAINT campaign_planning_jobs_processed_count_check CHECK (processed_count >= 0),
    CONSTRAINT campaign_planning_jobs_lease_generation_check CHECK (lease_generation > 0),
    CONSTRAINT campaign_planning_jobs_version_check CHECK (version > 0),
    CONSTRAINT campaign_planning_jobs_sequence_fkey FOREIGN KEY (workspace_id, campaign_id, sequence_id)
    REFERENCES public.campaign_sequences (workspace_id, campaign_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT campaign_planning_jobs_sequence_shape_check CHECK ((phase = 'CAPTURE') = (sequence_id IS NULL)),
    CONSTRAINT campaign_planning_jobs_progress_check CHECK (total_count IS NULL OR processed_count <= total_count),
    CONSTRAINT campaign_planning_jobs_lease_check CHECK ((lease_owner IS NULL) = (lease_expires_at IS NULL))
);

CREATE UNIQUE INDEX campaign_planning_jobs_capture_identity_idx
    ON public.campaign_planning_jobs (workspace_id, audience_id, phase)
    WHERE phase = 'CAPTURE';
CREATE UNIQUE INDEX campaign_planning_jobs_activation_identity_idx
    ON public.campaign_planning_jobs (workspace_id, campaign_id, activation_id, phase)
    WHERE phase IN ('ENROLL', 'RENDER');
CREATE INDEX campaign_planning_jobs_due_idx ON public.campaign_planning_jobs (next_due_at, id);
CREATE INDEX campaign_planning_jobs_campaign_idx ON public.campaign_planning_jobs (workspace_id, campaign_id, id);

CREATE TABLE public.campaign_enrollments (
    id uuid DEFAULT pg_catalog.gen_random_uuid() CONSTRAINT campaign_enrollments_pkey PRIMARY KEY,
    workspace_id uuid NOT NULL,
    campaign_id uuid NOT NULL,
    audience_id uuid NOT NULL,
    audience_member_id uuid NOT NULL,
    sequence_id uuid NOT NULL,
    lead_id uuid NOT NULL,
    address_id uuid NOT NULL,
    frozen_destination text NOT NULL,
    frozen_variables jsonb NOT NULL DEFAULT '{}'::jsonb,
    assigned_mailbox_id uuid,
    state text NOT NULL DEFAULT 'ACTIVE',
    next_step_id uuid,
    next_sequence_position integer,
    hold_reason text,
    stop_reason text,
    first_acceptance_at timestamptz,
    last_acceptance_at timestamptz,
    version bigint NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    CONSTRAINT campaign_enrollments_workspace_fkey FOREIGN KEY (workspace_id)
        REFERENCES public.workspaces (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT campaign_enrollments_campaign_fkey FOREIGN KEY (workspace_id, campaign_id)
        REFERENCES public.campaigns (workspace_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT campaign_enrollments_audience_member_fkey FOREIGN KEY (workspace_id, campaign_id, audience_id, audience_member_id, lead_id, address_id)
        REFERENCES public.campaign_audience_members (workspace_id, campaign_id, audience_id, id, lead_id, address_id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT campaign_enrollments_sequence_fkey FOREIGN KEY (workspace_id, campaign_id, sequence_id)
        REFERENCES public.campaign_sequences (workspace_id, campaign_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT campaign_enrollments_lead_fkey FOREIGN KEY (workspace_id, lead_id)
        REFERENCES public.leads (workspace_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT campaign_enrollments_address_fkey FOREIGN KEY (workspace_id, address_id)
        REFERENCES public.recipient_addresses (workspace_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT campaign_enrollments_mailbox_fkey FOREIGN KEY (workspace_id, assigned_mailbox_id)
        REFERENCES public.mailboxes (workspace_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT campaign_enrollments_next_step_fkey FOREIGN KEY (workspace_id, sequence_id, next_step_id)
        REFERENCES public.sequence_steps (workspace_id, sequence_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT campaign_enrollments_workspace_id_key UNIQUE (workspace_id, id),
    CONSTRAINT campaign_enrollments_lead_key UNIQUE (workspace_id, campaign_id, lead_id),
    CONSTRAINT campaign_enrollments_address_key UNIQUE (workspace_id, campaign_id, address_id),
    -- Candidate key so messages (0004) can validate a step belongs to this
    -- enrollment's own sequence via one composite foreign key.
    CONSTRAINT campaign_enrollments_sequence_identity_key UNIQUE (workspace_id, campaign_id, sequence_id, id),
    CONSTRAINT campaign_enrollments_frozen_destination_check CHECK (
        pg_catalog.char_length(frozen_destination) BETWEEN 3 AND 320
    ),
    CONSTRAINT campaign_enrollments_frozen_variables_check CHECK (
        pg_catalog.jsonb_typeof(frozen_variables) = 'object'
        AND pg_catalog.octet_length(pg_catalog.convert_to(frozen_variables::text, 'UTF8')) <= 16384
    ),
    CONSTRAINT campaign_enrollments_state_check CHECK (state IN ('ACTIVE', 'COMPLETED', 'STOPPED', 'FAILED')),
    CONSTRAINT campaign_enrollments_stop_reason_check CHECK ((state = 'STOPPED') = (stop_reason IS NOT NULL)),
    CONSTRAINT campaign_enrollments_terminal_step_check CHECK (state = 'ACTIVE' OR next_step_id IS NULL),
    CONSTRAINT campaign_enrollments_acceptance_order_check CHECK (
        first_acceptance_at IS NULL OR last_acceptance_at IS NULL OR last_acceptance_at >= first_acceptance_at
    ),
    CONSTRAINT campaign_enrollments_version_check CHECK (version > 0),
    CONSTRAINT campaign_enrollments_recipient_key UNIQUE (workspace_id, id, address_id),
    CONSTRAINT campaign_enrollments_mailbox_assignment_fkey FOREIGN KEY (workspace_id, campaign_id, assigned_mailbox_id)
        REFERENCES public.campaign_mailboxes (workspace_id, campaign_id, mailbox_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT campaign_enrollments_acceptance_pair_check CHECK ((first_acceptance_at IS NULL) = (last_acceptance_at IS NULL))
);

CREATE INDEX campaign_enrollments_progress_idx ON public.campaign_enrollments (workspace_id, campaign_id, state, id);
CREATE INDEX campaign_enrollments_suppression_fanout_idx
    ON public.campaign_enrollments (workspace_id, address_id, state, id);

CREATE TABLE public.controlled_send_authorizations (
    requester_user_id uuid NOT NULL,
    id uuid DEFAULT pg_catalog.gen_random_uuid() CONSTRAINT controlled_send_authorizations_pkey PRIMARY KEY,
    workspace_id uuid NOT NULL,
    requester_membership_id uuid NOT NULL,
    mailbox_id uuid NOT NULL,
    address_id uuid NOT NULL,
    command_receipt_id uuid NOT NULL,
    purpose text NOT NULL DEFAULT 'CONTROLLED_TEST',
    expires_at timestamptz NOT NULL,
    recipient_approval_evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
    revoked_at timestamptz,
    version bigint NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    CONSTRAINT controlled_send_authorizations_workspace_fkey FOREIGN KEY (workspace_id)
        REFERENCES public.workspaces (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT controlled_send_authorizations_membership_fkey FOREIGN KEY (workspace_id, requester_membership_id)
        REFERENCES public.workspace_memberships (workspace_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT controlled_send_authorizations_mailbox_fkey FOREIGN KEY (workspace_id, mailbox_id)
        REFERENCES public.mailboxes (workspace_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT controlled_send_authorizations_address_fkey FOREIGN KEY (workspace_id, address_id)
        REFERENCES public.recipient_addresses (workspace_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT controlled_send_authorizations_receipt_fkey FOREIGN KEY (workspace_id, command_receipt_id)
        REFERENCES public.command_receipts (workspace_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT controlled_send_authorizations_workspace_id_key UNIQUE (workspace_id, id),
    CONSTRAINT controlled_send_authorizations_receipt_key UNIQUE (workspace_id, command_receipt_id),
    CONSTRAINT controlled_send_authorizations_purpose_check CHECK (purpose IN ('CONTROLLED_TEST')),
    CONSTRAINT controlled_send_authorizations_expiry_check CHECK (expires_at > created_at),
    CONSTRAINT controlled_send_authorizations_evidence_check CHECK (
        pg_catalog.jsonb_typeof(recipient_approval_evidence) = 'object'
        AND pg_catalog.octet_length(pg_catalog.convert_to(recipient_approval_evidence::text, 'UTF8')) <= 4096
    ),
    CONSTRAINT controlled_send_authorizations_version_check CHECK (version > 0),
    CONSTRAINT controlled_send_authorizations_requester_fkey
    FOREIGN KEY (workspace_id, requester_user_id, requester_membership_id)
    REFERENCES public.workspace_memberships (workspace_id, user_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT controlled_send_authorizations_actor_receipt_fkey FOREIGN KEY (workspace_id, requester_user_id, command_receipt_id)
    REFERENCES public.command_receipts (workspace_id, actor_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT controlled_send_authorizations_target_key UNIQUE (workspace_id, id, mailbox_id, address_id)
);

CREATE INDEX controlled_send_authorizations_requester_idx
    ON public.controlled_send_authorizations (workspace_id, requester_membership_id);
CREATE INDEX controlled_send_authorizations_due_idx
    ON public.controlled_send_authorizations (expires_at)
    WHERE revoked_at IS NULL;

ALTER TABLE public.campaigns
    ADD CONSTRAINT campaigns_draft_sequence_fkey FOREIGN KEY (workspace_id, id, draft_sequence_id)
        REFERENCES public.campaign_sequences (workspace_id, campaign_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    ADD CONSTRAINT campaigns_activated_sequence_fkey FOREIGN KEY (workspace_id, id, activated_sequence_id)
        REFERENCES public.campaign_sequences (workspace_id, campaign_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    ADD CONSTRAINT campaigns_draft_audience_fkey FOREIGN KEY (workspace_id, id, draft_audience_id)
        REFERENCES public.campaign_audiences (workspace_id, campaign_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    ADD CONSTRAINT campaigns_activated_audience_fkey FOREIGN KEY (workspace_id, id, activated_audience_id)
        REFERENCES public.campaign_audiences (workspace_id, campaign_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT;

-- ---------------------------------------------------------------------------
-- Row level security and runtime capabilities

ALTER TABLE public.mailboxes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.mailboxes FORCE ROW LEVEL SECURITY;
ALTER TABLE public.mailbox_connections ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.mailbox_connections FORCE ROW LEVEL SECURITY;
ALTER TABLE public.oauth_flows ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.oauth_flows FORCE ROW LEVEL SECURITY;
ALTER TABLE public.mailbox_sync_states ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.mailbox_sync_states FORCE ROW LEVEL SECURITY;
ALTER TABLE public.campaigns ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.campaigns FORCE ROW LEVEL SECURITY;
ALTER TABLE public.campaign_sequences ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.campaign_sequences FORCE ROW LEVEL SECURITY;
ALTER TABLE public.sequence_steps ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.sequence_steps FORCE ROW LEVEL SECURITY;
ALTER TABLE public.campaign_settings_versions ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.campaign_settings_versions FORCE ROW LEVEL SECURITY;
ALTER TABLE public.campaign_mailboxes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.campaign_mailboxes FORCE ROW LEVEL SECURITY;
ALTER TABLE public.campaign_audiences ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.campaign_audiences FORCE ROW LEVEL SECURITY;
ALTER TABLE public.audience_capture_sources ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.audience_capture_sources FORCE ROW LEVEL SECURITY;
ALTER TABLE public.campaign_audience_members ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.campaign_audience_members FORCE ROW LEVEL SECURITY;
ALTER TABLE public.campaign_planning_jobs ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.campaign_planning_jobs FORCE ROW LEVEL SECURITY;
ALTER TABLE public.campaign_enrollments ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.campaign_enrollments FORCE ROW LEVEL SECURITY;
ALTER TABLE public.controlled_send_authorizations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.controlled_send_authorizations FORCE ROW LEVEL SECURITY;

REVOKE ALL PRIVILEGES ON TABLE
    public.mailboxes, public.mailbox_connections, public.oauth_flows, public.mailbox_sync_states,
    public.campaigns, public.campaign_sequences, public.sequence_steps, public.campaign_settings_versions,
    public.campaign_mailboxes, public.campaign_audiences, public.audience_capture_sources,
    public.campaign_audience_members, public.campaign_planning_jobs, public.campaign_enrollments,
    public.controlled_send_authorizations
    FROM PUBLIC, anon, authenticated, service_role;


CREATE TRIGGER mailboxes_touch_row
    BEFORE UPDATE ON public.mailboxes FOR EACH ROW EXECUTE FUNCTION public.app_touch_row();
CREATE TRIGGER mailbox_connections_touch_row
    BEFORE UPDATE ON public.mailbox_connections FOR EACH ROW EXECUTE FUNCTION public.app_touch_row();
CREATE TRIGGER oauth_flows_touch_row
    BEFORE UPDATE ON public.oauth_flows FOR EACH ROW EXECUTE FUNCTION public.app_touch_row();
CREATE TRIGGER mailbox_sync_states_touch_row
    BEFORE UPDATE ON public.mailbox_sync_states FOR EACH ROW EXECUTE FUNCTION public.app_touch_row();
CREATE TRIGGER campaigns_touch_row
    BEFORE UPDATE ON public.campaigns FOR EACH ROW EXECUTE FUNCTION public.app_touch_row();
CREATE TRIGGER campaign_sequences_touch_row
    BEFORE UPDATE ON public.campaign_sequences FOR EACH ROW EXECUTE FUNCTION public.app_touch_row();
CREATE TRIGGER sequence_steps_touch_row
    BEFORE UPDATE ON public.sequence_steps FOR EACH ROW EXECUTE FUNCTION public.app_touch_row();
CREATE TRIGGER campaign_audiences_touch_row
    BEFORE UPDATE ON public.campaign_audiences FOR EACH ROW EXECUTE FUNCTION public.app_touch_row();
CREATE TRIGGER campaign_planning_jobs_touch_row
    BEFORE UPDATE ON public.campaign_planning_jobs FOR EACH ROW EXECUTE FUNCTION public.app_touch_row();
CREATE TRIGGER campaign_enrollments_touch_row
    BEFORE UPDATE ON public.campaign_enrollments FOR EACH ROW EXECUTE FUNCTION public.app_touch_row();
CREATE TRIGGER controlled_send_authorizations_touch_row
    BEFORE UPDATE ON public.controlled_send_authorizations FOR EACH ROW EXECUTE FUNCTION public.app_touch_row();

ALTER TABLE public.campaigns ADD CONSTRAINT campaigns_current_settings_fkey
FOREIGN KEY (workspace_id, id, current_settings_id) REFERENCES public.campaign_settings_versions (workspace_id, campaign_id, id)
ON UPDATE RESTRICT ON DELETE RESTRICT;

ALTER TABLE public.mailboxes ADD CONSTRAINT mailboxes_current_connection_fkey
FOREIGN KEY (workspace_id, id, connected_generation) REFERENCES public.mailbox_connections (workspace_id, mailbox_id, generation)
ON UPDATE RESTRICT ON DELETE RESTRICT;

CREATE FUNCTION public.app_guard_frozen_sequence_steps() RETURNS trigger
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
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Frozen sequence steps are immutable';
    END IF;
    RETURN COALESCE(NEW, OLD);
END;
$function$;
REVOKE ALL ON FUNCTION public.app_guard_frozen_sequence_steps() FROM PUBLIC, anon, authenticated, service_role;

-- The migration identity creates triggers after ownership transfer; keep these
-- grants temporary so function execution is not opened to runtime/browser roles.
GRANT EXECUTE ON FUNCTION public.app_guard_frozen_sequence_steps() TO CURRENT_USER;
GRANT app_integrity_guard TO CURRENT_USER;
GRANT CREATE ON SCHEMA public TO app_integrity_guard;
ALTER FUNCTION public.app_guard_frozen_sequence_steps() OWNER TO app_integrity_guard;
REVOKE CREATE ON SCHEMA public FROM app_integrity_guard;

CREATE TRIGGER sequence_steps_guard_frozen BEFORE INSERT OR UPDATE OR DELETE ON public.sequence_steps FOR EACH ROW EXECUTE FUNCTION public.app_guard_frozen_sequence_steps();
REVOKE EXECUTE ON FUNCTION public.app_guard_frozen_sequence_steps() FROM CURRENT_USER;
REVOKE app_integrity_guard FROM CURRENT_USER;

CREATE FUNCTION public.app_guard_sequence() RETURNS trigger
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = ''
AS $function$
DECLARE parent_status text;
BEGIN
    SELECT status INTO STRICT parent_status FROM public.campaigns
        WHERE workspace_id = NEW.workspace_id AND id = NEW.campaign_id FOR UPDATE;
    IF parent_status <> 'DRAFT' OR (TG_OP = 'UPDATE' AND OLD.status = 'FROZEN') THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Activated sequence is immutable';
    END IF;
    IF NEW.status = 'FROZEN' AND (NEW.content_digest IS NULL OR NEW.content_digest !~ '^[0-9a-f]{64}$') THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Frozen sequence requires content identity';
    END IF;
    RETURN NEW;
END;
$function$;
REVOKE ALL ON FUNCTION public.app_guard_sequence() FROM PUBLIC, anon, authenticated, service_role;

GRANT EXECUTE ON FUNCTION public.app_guard_sequence() TO CURRENT_USER;
GRANT app_integrity_guard TO CURRENT_USER;
GRANT CREATE ON SCHEMA public TO app_integrity_guard;
ALTER FUNCTION public.app_guard_sequence() OWNER TO app_integrity_guard;
REVOKE CREATE ON SCHEMA public FROM app_integrity_guard;

CREATE TRIGGER campaign_sequences_guard_frozen BEFORE INSERT OR UPDATE ON public.campaign_sequences FOR EACH ROW EXECUTE FUNCTION public.app_guard_sequence();
REVOKE EXECUTE ON FUNCTION public.app_guard_sequence() FROM CURRENT_USER;
REVOKE app_integrity_guard FROM CURRENT_USER;

CREATE FUNCTION public.app_guard_campaign_snapshot() RETURNS trigger
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = ''
AS $function$
DECLARE selected_status text;
BEGIN
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
REVOKE ALL ON FUNCTION public.app_guard_campaign_snapshot() FROM PUBLIC, anon, authenticated, service_role;

GRANT EXECUTE ON FUNCTION public.app_guard_campaign_snapshot() TO CURRENT_USER;
GRANT app_integrity_guard TO CURRENT_USER;
GRANT CREATE ON SCHEMA public TO app_integrity_guard;
ALTER FUNCTION public.app_guard_campaign_snapshot() OWNER TO app_integrity_guard;
REVOKE CREATE ON SCHEMA public FROM app_integrity_guard;

CREATE TRIGGER campaigns_snapshot_guard BEFORE INSERT OR UPDATE ON public.campaigns FOR EACH ROW EXECUTE FUNCTION public.app_guard_campaign_snapshot();
REVOKE EXECUTE ON FUNCTION public.app_guard_campaign_snapshot() FROM CURRENT_USER;
REVOKE app_integrity_guard FROM CURRENT_USER;

CREATE FUNCTION public.app_guard_campaign_config() RETURNS trigger
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = ''
AS $function$
DECLARE
    tenant uuid := COALESCE(NEW.workspace_id, OLD.workspace_id);
    campaign_uuid uuid := COALESCE(NEW.campaign_id, OLD.campaign_id);
    parent_status text;
BEGIN
    SELECT status INTO STRICT parent_status FROM public.campaigns
      WHERE workspace_id = tenant AND id = campaign_uuid FOR UPDATE;
    IF parent_status <> 'DRAFT' AND NOT (TG_TABLE_NAME = 'campaign_settings_versions' AND parent_status = 'PAUSED') THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Campaign configuration is frozen in this state';
    END IF;
    RETURN COALESCE(NEW, OLD);
END;
$function$;
REVOKE ALL ON FUNCTION public.app_guard_campaign_config() FROM PUBLIC, anon, authenticated, service_role;

GRANT EXECUTE ON FUNCTION public.app_guard_campaign_config() TO CURRENT_USER;
GRANT app_integrity_guard TO CURRENT_USER;
GRANT CREATE ON SCHEMA public TO app_integrity_guard;
ALTER FUNCTION public.app_guard_campaign_config() OWNER TO app_integrity_guard;
REVOKE CREATE ON SCHEMA public FROM app_integrity_guard;

CREATE TRIGGER campaign_settings_versions_parent_guard BEFORE INSERT ON public.campaign_settings_versions FOR EACH ROW EXECUTE FUNCTION public.app_guard_campaign_config();

CREATE TRIGGER campaign_mailboxes_parent_guard BEFORE INSERT OR UPDATE OR DELETE ON public.campaign_mailboxes FOR EACH ROW EXECUTE FUNCTION public.app_guard_campaign_config();
REVOKE EXECUTE ON FUNCTION public.app_guard_campaign_config() FROM CURRENT_USER;
REVOKE app_integrity_guard FROM CURRENT_USER;

CREATE FUNCTION public.app_guard_audience_member() RETURNS trigger
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = ''
AS $function$
DECLARE parent_status text;
BEGIN
    SELECT status INTO STRICT parent_status FROM public.campaign_audiences
      WHERE workspace_id = NEW.workspace_id AND campaign_id = NEW.campaign_id AND id = NEW.audience_id FOR UPDATE;
    IF parent_status <> 'CAPTURING' THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Completed audience is immutable';
    END IF;
    RETURN NEW;
END;
$function$;
REVOKE ALL ON FUNCTION public.app_guard_audience_member() FROM PUBLIC, anon, authenticated, service_role;

GRANT EXECUTE ON FUNCTION public.app_guard_audience_member() TO CURRENT_USER;
GRANT app_integrity_guard TO CURRENT_USER;
GRANT CREATE ON SCHEMA public TO app_integrity_guard;
ALTER FUNCTION public.app_guard_audience_member() OWNER TO app_integrity_guard;
REVOKE CREATE ON SCHEMA public FROM app_integrity_guard;

CREATE TRIGGER campaign_audience_members_capture_guard BEFORE INSERT ON public.campaign_audience_members FOR EACH ROW EXECUTE FUNCTION public.app_guard_audience_member();
REVOKE EXECUTE ON FUNCTION public.app_guard_audience_member() FROM CURRENT_USER;
REVOKE app_integrity_guard FROM CURRENT_USER;

CREATE FUNCTION public.app_guard_audience() RETURNS trigger
LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path = ''
AS $function$
BEGIN
    IF OLD.status <> 'CAPTURING' OR
       (NEW.selection_manifest, NEW.campaign_id, NEW.revision) IS DISTINCT FROM (OLD.selection_manifest, OLD.campaign_id, OLD.revision) THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Audience selection or completed snapshot is immutable';
    END IF;
    RETURN NEW;
END;
$function$;
REVOKE ALL ON FUNCTION public.app_guard_audience() FROM PUBLIC, anon, authenticated, service_role;

CREATE TRIGGER campaign_audiences_snapshot_guard BEFORE UPDATE ON public.campaign_audiences FOR EACH ROW EXECUTE FUNCTION public.app_guard_audience();

CREATE FUNCTION public.app_capture_list_counter() RETURNS trigger
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = ''
AS $function$
DECLARE delta integer;
BEGIN
    IF TG_OP = 'UPDATE' AND (NEW.workspace_id, NEW.audience_id, NEW.list_id, NEW.captured_list_revision, NEW.capture_generation)
       IS DISTINCT FROM (OLD.workspace_id, OLD.audience_id, OLD.list_id, OLD.captured_list_revision, OLD.capture_generation) THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Capture source identity is immutable';
    END IF;
    IF TG_OP = 'UPDATE' AND NOT OLD.gate_held AND NEW.gate_held THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Released capture cannot reacquire its gate';
    END IF;
    delta := CASE WHEN NEW.gate_held THEN 1 ELSE 0 END - CASE WHEN TG_OP = 'UPDATE' AND OLD.gate_held THEN 1 ELSE 0 END;
    PERFORM 1 FROM public.lead_lists WHERE workspace_id = NEW.workspace_id AND id = NEW.list_id FOR UPDATE;
    IF TG_OP = 'INSERT' AND NOT EXISTS (SELECT 1 FROM public.lead_lists WHERE workspace_id = NEW.workspace_id
        AND id = NEW.list_id AND membership_revision = NEW.captured_list_revision) THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'List revision changed before capture';
    END IF;
    IF delta <> 0 THEN UPDATE public.lead_lists SET capture_count = capture_count + delta
        WHERE workspace_id = NEW.workspace_id AND id = NEW.list_id; END IF;
    RETURN NEW;
END;
$function$;
REVOKE ALL ON FUNCTION public.app_capture_list_counter() FROM PUBLIC, anon, authenticated, service_role;

GRANT EXECUTE ON FUNCTION public.app_capture_list_counter() TO CURRENT_USER;
GRANT app_integrity_guard TO CURRENT_USER;
GRANT CREATE ON SCHEMA public TO app_integrity_guard;
ALTER FUNCTION public.app_capture_list_counter() OWNER TO app_integrity_guard;
REVOKE CREATE ON SCHEMA public FROM app_integrity_guard;

CREATE TRIGGER audience_capture_sources_counter AFTER INSERT OR UPDATE ON public.audience_capture_sources
FOR EACH ROW EXECUTE FUNCTION public.app_capture_list_counter();
REVOKE EXECUTE ON FUNCTION public.app_capture_list_counter() FROM CURRENT_USER;
REVOKE app_integrity_guard FROM CURRENT_USER;

CREATE POLICY mailboxes_api_select ON public.mailboxes
FOR SELECT TO app_api USING (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('product.read'));
GRANT SELECT ON public.mailboxes TO app_api;

CREATE POLICY campaigns_api_select ON public.campaigns
FOR SELECT TO app_api USING (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('product.read'));
GRANT SELECT ON public.campaigns TO app_api;

CREATE POLICY campaign_sequences_api_select ON public.campaign_sequences
FOR SELECT TO app_api USING (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('product.read'));
GRANT SELECT ON public.campaign_sequences TO app_api;

CREATE POLICY sequence_steps_api_select ON public.sequence_steps
FOR SELECT TO app_api USING (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('product.read'));
GRANT SELECT ON public.sequence_steps TO app_api;

CREATE POLICY campaign_settings_versions_api_select ON public.campaign_settings_versions
FOR SELECT TO app_api USING (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('product.read'));
GRANT SELECT ON public.campaign_settings_versions TO app_api;

CREATE POLICY campaign_mailboxes_api_select ON public.campaign_mailboxes
FOR SELECT TO app_api USING (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('product.read'));
GRANT SELECT ON public.campaign_mailboxes TO app_api;

CREATE POLICY campaign_audiences_api_select ON public.campaign_audiences
FOR SELECT TO app_api USING (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('product.read'));
GRANT SELECT ON public.campaign_audiences TO app_api;

CREATE POLICY campaign_audience_members_api_select ON public.campaign_audience_members
FOR SELECT TO app_api USING (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('product.read'));
GRANT SELECT ON public.campaign_audience_members TO app_api;

CREATE POLICY campaign_enrollments_api_select ON public.campaign_enrollments
FOR SELECT TO app_api USING (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('product.read'));
GRANT SELECT ON public.campaign_enrollments TO app_api;

CREATE POLICY campaigns_api_insert ON public.campaigns
FOR INSERT TO app_api WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('campaigns.draft') AND status = 'DRAFT' AND creator_id = public.app_current_user_id());
GRANT INSERT (current_settings_id, previous_status, id, workspace_id, name, description, creator_id, status, start_at, draft_sequence_id, activated_sequence_id, draft_audience_id, activated_audience_id, activation_id, planning_status, schedule_generation, archived_at, error_reason) ON public.campaigns TO app_api;

CREATE POLICY campaigns_api_update ON public.campaigns
FOR UPDATE TO app_api USING (workspace_id = (SELECT public.app_current_workspace_id()) AND ((public.app_has_permission('campaigns.draft') AND status = 'DRAFT') OR public.app_has_permission('campaigns.execute'))) WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()) AND ((public.app_has_permission('campaigns.draft') AND status IN ('DRAFT','ARCHIVED') AND activation_id IS NULL) OR public.app_has_permission('campaigns.execute')));
GRANT UPDATE (name, description, status, start_at, draft_sequence_id, draft_audience_id, activated_sequence_id, activated_audience_id, activation_id, current_settings_id, planning_status, schedule_generation, archived_at, error_reason) ON public.campaigns TO app_api;

CREATE POLICY campaign_sequences_api_insert ON public.campaign_sequences
FOR INSERT TO app_api WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('campaigns.draft'));
GRANT INSERT (id, workspace_id, campaign_id, revision, status, frozen_at, content_digest) ON public.campaign_sequences TO app_api;

CREATE POLICY campaign_sequences_api_update ON public.campaign_sequences
FOR UPDATE TO app_api USING (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('campaigns.draft')) WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('campaigns.draft'));
GRANT UPDATE (status, frozen_at, content_digest) ON public.campaign_sequences TO app_api;

CREATE POLICY sequence_steps_api_insert ON public.sequence_steps
FOR INSERT TO app_api WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('campaigns.draft'));
GRANT INSERT (id, workspace_id, sequence_id, campaign_id, position, kind, email_subject, email_body_html, email_variable_schema, wait_duration_minutes, source_template_version_id) ON public.sequence_steps TO app_api;

CREATE POLICY sequence_steps_api_update ON public.sequence_steps
FOR UPDATE TO app_api USING (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('campaigns.draft')) WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('campaigns.draft'));
GRANT UPDATE (position, kind, email_subject, email_body_html, email_variable_schema, wait_duration_minutes, source_template_version_id) ON public.sequence_steps TO app_api;

CREATE POLICY sequence_steps_api_delete ON public.sequence_steps
FOR DELETE TO app_api USING (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('campaigns.draft'));
GRANT DELETE ON public.sequence_steps TO app_api;

CREATE POLICY campaign_settings_versions_api_insert ON public.campaign_settings_versions
FOR INSERT TO app_api WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('campaigns.draft') AND EXISTS (SELECT 1 FROM public.campaigns c WHERE c.workspace_id=campaign_settings_versions.workspace_id AND c.id=campaign_settings_versions.campaign_id AND (c.status='DRAFT' OR (c.status='PAUSED' AND public.app_has_permission('campaigns.execute')))));
GRANT INSERT (id, workspace_id, campaign_id, revision, generation, timezone, weekday_set, window_start_local, window_end_local, lower_bound_start, daily_limit, enabled_settings, computation_version) ON public.campaign_settings_versions TO app_api;

CREATE POLICY campaign_mailboxes_api_insert ON public.campaign_mailboxes
FOR INSERT TO app_api WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('campaigns.draft'));
GRANT INSERT (workspace_id, campaign_id, mailbox_id, active, config_revision, allocation_position) ON public.campaign_mailboxes TO app_api;

CREATE POLICY campaign_audiences_api_insert ON public.campaign_audiences
FOR INSERT TO app_api WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('campaigns.draft') AND status = 'CAPTURING');
GRANT INSERT (selection_manifest, id, workspace_id, campaign_id, revision, status, source_manifest_digest, started_at, completed_at, capture_generation) ON public.campaign_audiences TO app_api;

CREATE POLICY audience_capture_sources_api_insert ON public.audience_capture_sources
FOR INSERT TO app_api WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('campaigns.draft'));
GRANT INSERT (workspace_id, campaign_id, audience_id, list_id, captured_list_revision, gate_held, capture_generation, released_at) ON public.audience_capture_sources TO app_api;

CREATE POLICY campaign_planning_jobs_api_insert ON public.campaign_planning_jobs
FOR INSERT TO app_api WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('campaigns.draft'));
GRANT INSERT (id, workspace_id, campaign_id, audience_id, activation_id, sequence_id, phase, state, cursor_data, total_count, processed_count, lease_owner, lease_generation, lease_expires_at, next_due_at, error_reason) ON public.campaign_planning_jobs TO app_api;

CREATE POLICY campaign_mailboxes_api_delete ON public.campaign_mailboxes
FOR DELETE TO app_api USING (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('campaigns.draft'));
GRANT DELETE ON public.campaign_mailboxes TO app_api;

CREATE POLICY audience_capture_sources_api_select ON public.audience_capture_sources
FOR SELECT TO app_api USING (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('product.read'));
GRANT SELECT ON public.audience_capture_sources TO app_api;

CREATE POLICY campaign_planning_jobs_api_select ON public.campaign_planning_jobs
FOR SELECT TO app_api USING (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('product.read'));
GRANT SELECT ON public.campaign_planning_jobs TO app_api;

CREATE POLICY mailboxes_api_update ON public.mailboxes
FOR UPDATE TO app_api USING (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('mailboxes.manage')) WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('mailboxes.manage'));
GRANT UPDATE (sender_display_name, signature_html) ON public.mailboxes TO app_api;

CREATE POLICY mailboxes_connection_select ON public.mailboxes
FOR SELECT TO app_connection USING (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('mailboxes.manage'));
GRANT SELECT ON public.mailboxes TO app_connection;

CREATE POLICY mailboxes_connection_insert ON public.mailboxes
FOR INSERT TO app_connection WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('mailboxes.manage'));
GRANT INSERT (connected_generation, id, workspace_id, provider, provider_account_id, original_address, sender_display_name, signature_html, connection_state, health_state, policy_state, policy_reason, circuit_state, sync_state, blocked_until, current_connection_generation, config_version) ON public.mailboxes TO app_connection;

CREATE POLICY mailboxes_connection_update ON public.mailboxes
FOR UPDATE TO app_connection USING (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('mailboxes.manage')) WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('mailboxes.manage'));
GRANT UPDATE (provider_account_id, original_address, connection_state, current_connection_generation, connected_generation, health_state, sync_state, config_version) ON public.mailboxes TO app_connection;

CREATE POLICY mailbox_connections_connection_select ON public.mailbox_connections
FOR SELECT TO app_connection USING (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('mailboxes.manage'));
GRANT SELECT ON public.mailbox_connections TO app_connection;

CREATE POLICY mailbox_connections_connection_insert ON public.mailbox_connections
FOR INSERT TO app_connection WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('mailboxes.manage'));
GRANT INSERT (destroyed_at, id, workspace_id, mailbox_id, generation, credential_ciphertext, encryption_key_id, nonce, auth_mechanism, granted_scopes, protected_config, expires_at, refresh_lease_owner, refresh_lease_generation, refresh_lease_expires_at, revoked_at) ON public.mailbox_connections TO app_connection;

CREATE POLICY mailbox_connections_connection_update ON public.mailbox_connections
FOR UPDATE TO app_connection USING (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('mailboxes.manage')) WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('mailboxes.manage'));
GRANT UPDATE (revoked_at, destroyed_at, credential_ciphertext, encryption_key_id, nonce, protected_config, refresh_lease_owner, refresh_lease_generation, refresh_lease_expires_at) ON public.mailbox_connections TO app_connection;

CREATE POLICY oauth_flows_connection_select ON public.oauth_flows
FOR SELECT TO app_connection USING (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('mailboxes.manage') AND actor_id = public.app_current_user_id());
GRANT SELECT ON public.oauth_flows TO app_connection;

CREATE POLICY oauth_flows_connection_insert ON public.oauth_flows
FOR INSERT TO app_connection WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('mailboxes.manage') AND actor_id = public.app_current_user_id());
GRANT INSERT (claim_owner, claim_expires_at, verifier_key_id, verifier_nonce, id, workspace_id, actor_id, provider, state_digest, encrypted_verifier, return_path, status, claim_generation, resulting_mailbox_id, expires_at) ON public.oauth_flows TO app_connection;

CREATE POLICY oauth_flows_connection_update ON public.oauth_flows
FOR UPDATE TO app_connection USING (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('mailboxes.manage') AND actor_id = public.app_current_user_id()) WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('mailboxes.manage') AND actor_id = public.app_current_user_id());
GRANT UPDATE (status, claim_generation, claim_owner, claim_expires_at, resulting_mailbox_id, encrypted_verifier, verifier_key_id, verifier_nonce) ON public.oauth_flows TO app_connection;

CREATE POLICY controlled_send_authorizations_api_select ON public.controlled_send_authorizations
FOR SELECT TO app_api USING (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('campaigns.execute') AND requester_user_id = public.app_current_user_id());
GRANT SELECT (id, workspace_id, requester_user_id, requester_membership_id, mailbox_id,
 address_id, command_receipt_id, purpose, expires_at, revoked_at, version, created_at, updated_at)
ON public.controlled_send_authorizations TO app_api;

CREATE POLICY controlled_send_authorizations_api_insert ON public.controlled_send_authorizations
FOR INSERT TO app_api WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('campaigns.execute') AND requester_user_id = public.app_current_user_id());
GRANT INSERT (requester_user_id, id, workspace_id, requester_membership_id, mailbox_id, address_id, command_receipt_id, purpose, expires_at, recipient_approval_evidence, revoked_at) ON public.controlled_send_authorizations TO app_api;

CREATE POLICY controlled_send_authorizations_api_update ON public.controlled_send_authorizations
FOR UPDATE TO app_api USING (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('campaigns.execute') AND requester_user_id = public.app_current_user_id()) WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('campaigns.execute') AND requester_user_id = public.app_current_user_id());
GRANT UPDATE (revoked_at) ON public.controlled_send_authorizations TO app_api;

CREATE POLICY mailboxes_worker_general_select ON public.mailboxes
FOR SELECT TO app_worker_general USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.mailboxes TO app_worker_general;

CREATE POLICY campaigns_worker_general_select ON public.campaigns
FOR SELECT TO app_worker_general USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.campaigns TO app_worker_general;

CREATE POLICY campaign_sequences_worker_general_select ON public.campaign_sequences
FOR SELECT TO app_worker_general USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.campaign_sequences TO app_worker_general;

CREATE POLICY sequence_steps_worker_general_select ON public.sequence_steps
FOR SELECT TO app_worker_general USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.sequence_steps TO app_worker_general;

CREATE POLICY campaign_settings_versions_worker_general_select ON public.campaign_settings_versions
FOR SELECT TO app_worker_general USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.campaign_settings_versions TO app_worker_general;

CREATE POLICY campaign_mailboxes_worker_general_select ON public.campaign_mailboxes
FOR SELECT TO app_worker_general USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.campaign_mailboxes TO app_worker_general;

CREATE POLICY campaign_audiences_worker_general_select ON public.campaign_audiences
FOR SELECT TO app_worker_general USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.campaign_audiences TO app_worker_general;

CREATE POLICY audience_capture_sources_worker_general_select ON public.audience_capture_sources
FOR SELECT TO app_worker_general USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.audience_capture_sources TO app_worker_general;

CREATE POLICY campaign_audience_members_worker_general_select ON public.campaign_audience_members
FOR SELECT TO app_worker_general USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.campaign_audience_members TO app_worker_general;

CREATE POLICY campaign_planning_jobs_worker_general_select ON public.campaign_planning_jobs
FOR SELECT TO app_worker_general USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.campaign_planning_jobs TO app_worker_general;

CREATE POLICY campaign_enrollments_worker_general_select ON public.campaign_enrollments
FOR SELECT TO app_worker_general USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.campaign_enrollments TO app_worker_general;

CREATE POLICY controlled_send_authorizations_worker_general_select ON public.controlled_send_authorizations
FOR SELECT TO app_worker_general USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.controlled_send_authorizations TO app_worker_general;

CREATE POLICY campaign_audience_members_worker_general_insert ON public.campaign_audience_members
FOR INSERT TO app_worker_general WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT INSERT (id, workspace_id, campaign_id, audience_id, lead_id, address_id, capture_ordinal, contact_revision, frozen_variables, eligibility_status, exclusion_reason) ON public.campaign_audience_members TO app_worker_general;

CREATE POLICY campaign_planning_jobs_worker_general_insert ON public.campaign_planning_jobs
FOR INSERT TO app_worker_general WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT INSERT (id, workspace_id, campaign_id, audience_id, activation_id, sequence_id, phase, state, cursor_data, total_count, processed_count, lease_owner, lease_generation, lease_expires_at, next_due_at, error_reason) ON public.campaign_planning_jobs TO app_worker_general;

CREATE POLICY campaign_enrollments_worker_general_insert ON public.campaign_enrollments
FOR INSERT TO app_worker_general WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT INSERT (id, workspace_id, campaign_id, audience_id, audience_member_id, sequence_id, lead_id, address_id, frozen_destination, frozen_variables, assigned_mailbox_id, state, next_step_id, next_sequence_position, hold_reason, stop_reason, first_acceptance_at, last_acceptance_at) ON public.campaign_enrollments TO app_worker_general;

CREATE POLICY audience_capture_sources_worker_general_insert ON public.audience_capture_sources
FOR INSERT TO app_worker_general WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT INSERT (workspace_id, campaign_id, audience_id, list_id, captured_list_revision, gate_held, capture_generation, released_at) ON public.audience_capture_sources TO app_worker_general;

CREATE POLICY campaigns_worker_general_update ON public.campaigns
FOR UPDATE TO app_worker_general USING (workspace_id = (SELECT public.app_current_workspace_id())) WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT UPDATE (status, planning_status, error_reason) ON public.campaigns TO app_worker_general;

CREATE POLICY campaign_audiences_worker_general_update ON public.campaign_audiences
FOR UPDATE TO app_worker_general USING (workspace_id = (SELECT public.app_current_workspace_id())) WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT UPDATE (status, source_manifest_digest, completed_at) ON public.campaign_audiences TO app_worker_general;

CREATE POLICY audience_capture_sources_worker_general_update ON public.audience_capture_sources
FOR UPDATE TO app_worker_general USING (workspace_id = (SELECT public.app_current_workspace_id())) WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT UPDATE (gate_held, released_at) ON public.audience_capture_sources TO app_worker_general;

CREATE POLICY campaign_planning_jobs_worker_general_update ON public.campaign_planning_jobs
FOR UPDATE TO app_worker_general USING (workspace_id = (SELECT public.app_current_workspace_id())) WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT UPDATE (state, cursor_data, total_count, processed_count, lease_owner, lease_generation, lease_expires_at, next_due_at, error_reason) ON public.campaign_planning_jobs TO app_worker_general;

CREATE POLICY campaign_enrollments_worker_general_update ON public.campaign_enrollments
FOR UPDATE TO app_worker_general USING (workspace_id = (SELECT public.app_current_workspace_id())) WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT UPDATE (assigned_mailbox_id, state, next_step_id, next_sequence_position, hold_reason, stop_reason, first_acceptance_at, last_acceptance_at) ON public.campaign_enrollments TO app_worker_general;

CREATE POLICY mailboxes_worker_send_select ON public.mailboxes
FOR SELECT TO app_worker_send USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.mailboxes TO app_worker_send;

CREATE POLICY campaigns_worker_send_select ON public.campaigns
FOR SELECT TO app_worker_send USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.campaigns TO app_worker_send;

CREATE POLICY campaign_settings_versions_worker_send_select ON public.campaign_settings_versions
FOR SELECT TO app_worker_send USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.campaign_settings_versions TO app_worker_send;

CREATE POLICY campaign_enrollments_worker_send_select ON public.campaign_enrollments
FOR SELECT TO app_worker_send USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.campaign_enrollments TO app_worker_send;

CREATE POLICY controlled_send_authorizations_worker_send_select ON public.controlled_send_authorizations
FOR SELECT TO app_worker_send USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.controlled_send_authorizations TO app_worker_send;

CREATE POLICY mailboxes_worker_sync_select ON public.mailboxes
FOR SELECT TO app_worker_sync USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.mailboxes TO app_worker_sync;

CREATE POLICY campaigns_worker_sync_select ON public.campaigns
FOR SELECT TO app_worker_sync USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.campaigns TO app_worker_sync;

CREATE POLICY campaign_settings_versions_worker_sync_select ON public.campaign_settings_versions
FOR SELECT TO app_worker_sync USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.campaign_settings_versions TO app_worker_sync;

CREATE POLICY campaign_enrollments_worker_sync_select ON public.campaign_enrollments
FOR SELECT TO app_worker_sync USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.campaign_enrollments TO app_worker_sync;

CREATE POLICY controlled_send_authorizations_worker_sync_select ON public.controlled_send_authorizations
FOR SELECT TO app_worker_sync USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.controlled_send_authorizations TO app_worker_sync;

CREATE POLICY mailboxes_scheduler_select ON public.mailboxes
FOR SELECT TO app_scheduler USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.mailboxes TO app_scheduler;

CREATE POLICY campaigns_scheduler_select ON public.campaigns
FOR SELECT TO app_scheduler USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.campaigns TO app_scheduler;

CREATE POLICY campaign_settings_versions_scheduler_select ON public.campaign_settings_versions
FOR SELECT TO app_scheduler USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.campaign_settings_versions TO app_scheduler;

CREATE POLICY campaign_enrollments_scheduler_select ON public.campaign_enrollments
FOR SELECT TO app_scheduler USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.campaign_enrollments TO app_scheduler;

CREATE POLICY controlled_send_authorizations_scheduler_select ON public.controlled_send_authorizations
FOR SELECT TO app_scheduler USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.controlled_send_authorizations TO app_scheduler;

CREATE POLICY mailbox_connections_worker_send_select ON public.mailbox_connections
FOR SELECT TO app_worker_send USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.mailbox_connections TO app_worker_send;

CREATE POLICY mailbox_connections_worker_send_insert ON public.mailbox_connections
FOR INSERT TO app_worker_send WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT INSERT (destroyed_at, id, workspace_id, mailbox_id, generation, credential_ciphertext, encryption_key_id, nonce, auth_mechanism, granted_scopes, protected_config, expires_at, refresh_lease_owner, refresh_lease_generation, refresh_lease_expires_at, revoked_at) ON public.mailbox_connections TO app_worker_send;

CREATE POLICY mailbox_connections_worker_send_update ON public.mailbox_connections
FOR UPDATE TO app_worker_send USING (workspace_id = (SELECT public.app_current_workspace_id())) WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT UPDATE (refresh_lease_owner, refresh_lease_generation, refresh_lease_expires_at, revoked_at, destroyed_at, credential_ciphertext, encryption_key_id, nonce, protected_config) ON public.mailbox_connections TO app_worker_send;

CREATE POLICY mailboxes_worker_send_update ON public.mailboxes
FOR UPDATE TO app_worker_send USING (workspace_id = (SELECT public.app_current_workspace_id())) WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT UPDATE (connection_state, connected_generation, current_connection_generation, health_state, circuit_state, sync_state, blocked_until) ON public.mailboxes TO app_worker_send;

CREATE POLICY mailbox_connections_worker_sync_select ON public.mailbox_connections
FOR SELECT TO app_worker_sync USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.mailbox_connections TO app_worker_sync;

CREATE POLICY mailbox_connections_worker_sync_insert ON public.mailbox_connections
FOR INSERT TO app_worker_sync WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT INSERT (destroyed_at, id, workspace_id, mailbox_id, generation, credential_ciphertext, encryption_key_id, nonce, auth_mechanism, granted_scopes, protected_config, expires_at, refresh_lease_owner, refresh_lease_generation, refresh_lease_expires_at, revoked_at) ON public.mailbox_connections TO app_worker_sync;

CREATE POLICY mailbox_connections_worker_sync_update ON public.mailbox_connections
FOR UPDATE TO app_worker_sync USING (workspace_id = (SELECT public.app_current_workspace_id())) WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT UPDATE (refresh_lease_owner, refresh_lease_generation, refresh_lease_expires_at, revoked_at, destroyed_at, credential_ciphertext, encryption_key_id, nonce, protected_config) ON public.mailbox_connections TO app_worker_sync;

CREATE POLICY mailboxes_worker_sync_update ON public.mailboxes
FOR UPDATE TO app_worker_sync USING (workspace_id = (SELECT public.app_current_workspace_id())) WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT UPDATE (connection_state, connected_generation, current_connection_generation, health_state, circuit_state, sync_state, blocked_until) ON public.mailboxes TO app_worker_sync;

CREATE POLICY campaigns_scheduler_update ON public.campaigns
FOR UPDATE TO app_scheduler USING (workspace_id = (SELECT public.app_current_workspace_id())) WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT UPDATE (status, planning_status, error_reason) ON public.campaigns TO app_scheduler;

CREATE POLICY mailbox_sync_states_worker_sync_select ON public.mailbox_sync_states
FOR SELECT TO app_worker_sync USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.mailbox_sync_states TO app_worker_sync;

CREATE POLICY mailbox_sync_states_worker_sync_insert ON public.mailbox_sync_states
FOR INSERT TO app_worker_sync WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT INSERT (id, workspace_id, mailbox_id, connection_generation, sync_scope, cursor_data, lease_owner, lease_generation, lease_expires_at, next_due_at, last_complete_at, status, subscription_expires_at, failure_count) ON public.mailbox_sync_states TO app_worker_sync;

CREATE POLICY mailbox_sync_states_worker_sync_update ON public.mailbox_sync_states
FOR UPDATE TO app_worker_sync USING (workspace_id = (SELECT public.app_current_workspace_id())) WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT UPDATE (cursor_data, lease_owner, lease_generation, lease_expires_at, next_due_at, last_complete_at, status, subscription_expires_at, failure_count) ON public.mailbox_sync_states TO app_worker_sync;

CREATE POLICY campaigns_integrity_guard_select ON public.campaigns
FOR SELECT TO app_integrity_guard USING (true);
GRANT SELECT ON public.campaigns TO app_integrity_guard;

CREATE POLICY campaign_sequences_integrity_guard_select ON public.campaign_sequences
FOR SELECT TO app_integrity_guard USING (true);
GRANT SELECT ON public.campaign_sequences TO app_integrity_guard;

CREATE POLICY campaign_audiences_integrity_guard_select ON public.campaign_audiences
FOR SELECT TO app_integrity_guard USING (true);
GRANT SELECT ON public.campaign_audiences TO app_integrity_guard;

CREATE POLICY campaign_enrollments_integrity_guard_select ON public.campaign_enrollments
FOR SELECT TO app_integrity_guard USING (true);
GRANT SELECT ON public.campaign_enrollments TO app_integrity_guard;

CREATE POLICY campaigns_integrity_guard_update ON public.campaigns
FOR UPDATE TO app_integrity_guard USING (true) WITH CHECK (true);
GRANT UPDATE (status) ON public.campaigns TO app_integrity_guard;

CREATE POLICY campaign_sequences_integrity_guard_update ON public.campaign_sequences
FOR UPDATE TO app_integrity_guard USING (true) WITH CHECK (true);
GRANT UPDATE (status) ON public.campaign_sequences TO app_integrity_guard;

CREATE POLICY campaign_audiences_integrity_guard_update ON public.campaign_audiences
FOR UPDATE TO app_integrity_guard USING (true) WITH CHECK (true);
GRANT UPDATE (status) ON public.campaign_audiences TO app_integrity_guard;

CREATE FUNCTION public.app_guard_parent_identity() RETURNS trigger
LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path = ''
AS $function$
BEGIN
    IF (pg_catalog.to_jsonb(NEW)->'workspace_id',pg_catalog.to_jsonb(NEW)->'campaign_id',pg_catalog.to_jsonb(NEW)->'sequence_id') IS DISTINCT FROM
       (pg_catalog.to_jsonb(OLD)->'workspace_id',pg_catalog.to_jsonb(OLD)->'campaign_id',pg_catalog.to_jsonb(OLD)->'sequence_id') THEN
        RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Aggregate parent identity is immutable';
    END IF;
    RETURN NEW;
END;
$function$;
REVOKE ALL ON FUNCTION public.app_guard_parent_identity() FROM PUBLIC, anon, authenticated, service_role;

CREATE TRIGGER campaign_sequences_guard_parent_identity BEFORE UPDATE ON public.campaign_sequences FOR EACH ROW EXECUTE FUNCTION public.app_guard_parent_identity();

CREATE TRIGGER sequence_steps_guard_parent_identity BEFORE UPDATE ON public.sequence_steps FOR EACH ROW EXECUTE FUNCTION public.app_guard_parent_identity();

CREATE TRIGGER campaign_audiences_guard_parent_identity BEFORE UPDATE ON public.campaign_audiences FOR EACH ROW EXECUTE FUNCTION public.app_guard_parent_identity();

CREATE TRIGGER campaign_enrollments_guard_parent_identity BEFORE UPDATE ON public.campaign_enrollments FOR EACH ROW EXECUTE FUNCTION public.app_guard_parent_identity();

CREATE FUNCTION public.app_guard_connection_history() RETURNS trigger
LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path = ''
AS $function$
BEGIN
    IF (NEW.workspace_id,NEW.mailbox_id,NEW.generation,NEW.auth_mechanism,NEW.granted_scopes,NEW.expires_at) IS DISTINCT FROM
       (OLD.workspace_id,OLD.mailbox_id,OLD.generation,OLD.auth_mechanism,OLD.granted_scopes,OLD.expires_at)
       OR (OLD.revoked_at IS NOT NULL AND NEW.revoked_at IS DISTINCT FROM OLD.revoked_at)
       OR (OLD.destroyed_at IS NOT NULL AND NEW.destroyed_at IS DISTINCT FROM OLD.destroyed_at)
       OR (NEW.destroyed_at IS NULL AND (NEW.credential_ciphertext,NEW.encryption_key_id,NEW.nonce,NEW.protected_config)
         IS DISTINCT FROM (OLD.credential_ciphertext,OLD.encryption_key_id,OLD.nonce,OLD.protected_config)) THEN
        RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Rotate into a new generation; retained credential history is immutable';
    END IF;
    RETURN NEW;
END;
$function$;
REVOKE ALL ON FUNCTION public.app_guard_connection_history() FROM PUBLIC, anon, authenticated, service_role;

CREATE TRIGGER mailbox_connections_guard_connection_history BEFORE UPDATE ON public.mailbox_connections FOR EACH ROW EXECUTE FUNCTION public.app_guard_connection_history();

CREATE FUNCTION public.app_guard_mailbox_generation() RETURNS trigger
LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path = ''
AS $function$
BEGIN
    IF (NEW.workspace_id,NEW.provider) IS DISTINCT FROM (OLD.workspace_id,OLD.provider)
       OR (OLD.provider_account_id IS NOT NULL AND NEW.provider_account_id IS DISTINCT FROM OLD.provider_account_id)
       OR NEW.current_connection_generation < OLD.current_connection_generation
       OR (OLD.connection_state='CONNECTED' AND NEW.connection_state <> 'CONNECTED' AND NEW.current_connection_generation <= OLD.current_connection_generation)
       OR (NEW.connected_generation IS DISTINCT FROM OLD.connected_generation AND NEW.connected_generation IS NOT NULL
           AND NEW.current_connection_generation <= OLD.current_connection_generation) THEN
        RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Connection identity is immutable and generation changes must fence stale workers';
    END IF;
    RETURN NEW;
END;
$function$;
REVOKE ALL ON FUNCTION public.app_guard_mailbox_generation() FROM PUBLIC, anon, authenticated, service_role;

CREATE TRIGGER mailboxes_guard_mailbox_generation BEFORE UPDATE ON public.mailboxes FOR EACH ROW EXECUTE FUNCTION public.app_guard_mailbox_generation();

ALTER TABLE public.mailbox_connections ADD CONSTRAINT mailbox_connections_destruction_time_check CHECK (destroyed_at IS NULL OR (destroyed_at >= revoked_at AND destroyed_at >= created_at));

ALTER TABLE public.oauth_flows ADD CONSTRAINT oauth_flows_claim_time_check CHECK (claim_expires_at IS NULL OR (claim_expires_at > created_at AND claim_expires_at <= expires_at));

ALTER TABLE public.campaign_planning_jobs ADD CONSTRAINT campaign_planning_jobs_processing_lease_check CHECK (state <> 'PROCESSING' OR (lease_owner IS NOT NULL AND next_due_at IS NOT NULL));

ALTER TABLE public.campaign_audiences ADD CONSTRAINT campaign_audiences_selection_version_check CHECK (selection_manifest ? 'version' AND selection_manifest->'version' = '1'::jsonb AND selection_manifest ? 'lists' AND pg_catalog.jsonb_typeof(selection_manifest->'lists')='array' AND selection_manifest ? 'leads' AND pg_catalog.jsonb_typeof(selection_manifest->'leads')='array');

ALTER TABLE public.controlled_send_authorizations ADD CONSTRAINT controlled_send_authorizations_approval_nonempty_check CHECK (recipient_approval_evidence <> '{}'::jsonb);

CREATE FUNCTION public.app_guard_controlled_requester() RETURNS trigger
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = ''
AS $function$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM public.workspace_memberships m WHERE m.workspace_id=NEW.workspace_id
       AND m.id=NEW.requester_membership_id AND m.user_id=NEW.requester_user_id AND m.status='ACTIVE'
       AND m.role_code IN ('MANAGER','ADMIN','OWNER')) THEN
        RAISE EXCEPTION USING ERRCODE='42501', MESSAGE='Controlled test requires current executing membership';
    END IF;
    RETURN NEW;
END;
$function$;
REVOKE ALL ON FUNCTION public.app_guard_controlled_requester() FROM PUBLIC, anon, authenticated, service_role;

GRANT EXECUTE ON FUNCTION public.app_guard_controlled_requester() TO CURRENT_USER;
GRANT app_integrity_guard TO CURRENT_USER;
GRANT CREATE ON SCHEMA public TO app_integrity_guard;
ALTER FUNCTION public.app_guard_controlled_requester() OWNER TO app_integrity_guard;
REVOKE CREATE ON SCHEMA public FROM app_integrity_guard;

CREATE TRIGGER controlled_send_authorizations_guard_controlled_requester BEFORE INSERT ON public.controlled_send_authorizations FOR EACH ROW EXECUTE FUNCTION public.app_guard_controlled_requester();
REVOKE EXECUTE ON FUNCTION public.app_guard_controlled_requester() FROM CURRENT_USER;
REVOKE app_integrity_guard FROM CURRENT_USER;

GRANT SELECT ON public.mailboxes TO app_integrity_guard;
CREATE POLICY mailboxes_app_integrity_guard_additional_read ON public.mailboxes FOR SELECT TO app_integrity_guard USING (true);

GRANT UPDATE (pending_safety_count) ON public.mailboxes TO app_integrity_guard;
CREATE POLICY mailboxes_integrity_counter ON public.mailboxes FOR UPDATE TO app_integrity_guard USING (true) WITH CHECK (true);

GRANT SELECT ON public.campaign_sequences TO app_worker_send;
CREATE POLICY campaign_sequences_app_worker_send_additional_read ON public.campaign_sequences FOR SELECT TO app_worker_send USING (workspace_id = (SELECT public.app_current_workspace_id()));

GRANT SELECT ON public.sequence_steps TO app_worker_send;
CREATE POLICY sequence_steps_app_worker_send_additional_read ON public.sequence_steps FOR SELECT TO app_worker_send USING (workspace_id = (SELECT public.app_current_workspace_id()));

CREATE INDEX oauth_flows_lease_recovery_idx ON public.oauth_flows (workspace_id, actor_id, claim_expires_at, id) WHERE status = 'CLAIMED';

CREATE INDEX mailbox_connections_lease_recovery_idx ON public.mailbox_connections (workspace_id, refresh_lease_expires_at, id) WHERE refresh_lease_owner IS NOT NULL;

CREATE INDEX mailbox_sync_states_lease_recovery_idx ON public.mailbox_sync_states (workspace_id, lease_expires_at, id) WHERE lease_owner IS NOT NULL;

CREATE INDEX campaign_planning_jobs_lease_recovery_idx ON public.campaign_planning_jobs (workspace_id, lease_expires_at, id) WHERE lease_owner IS NOT NULL;

-- Captured addresses and revisions must come from the same observed lead;
-- subsequent lead edits do not rewrite the immutable audience row.
CREATE FUNCTION public.app_guard_capture_identity() RETURNS trigger
LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path = ''
AS $function$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM public.leads l JOIN public.recipient_addresses a
       ON a.workspace_id=l.workspace_id AND a.canonical_address=l.canonical_address
          AND a.normalization_version=l.normalization_version
       WHERE l.workspace_id=NEW.workspace_id AND l.id=NEW.lead_id
         AND a.id=NEW.address_id AND l.contact_revision=NEW.contact_revision) THEN
        RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Capture must bind the observed lead revision to its canonical recipient';
    END IF;
    RETURN NEW;
END;
$function$;
REVOKE ALL ON FUNCTION public.app_guard_capture_identity() FROM PUBLIC, anon, authenticated, service_role;
CREATE TRIGGER campaign_audience_members_identity_guard BEFORE INSERT ON public.campaign_audience_members
FOR EACH ROW EXECUTE FUNCTION public.app_guard_capture_identity();

CREATE FUNCTION public.app_guard_enrollment_snapshot() RETURNS trigger
LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path = ''
AS $function$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM public.campaigns c
       JOIN public.campaign_audience_members a ON a.workspace_id=c.workspace_id
         AND a.campaign_id=c.id AND a.audience_id=c.activated_audience_id
       JOIN public.recipient_addresses r ON r.workspace_id=a.workspace_id AND r.id=a.address_id
       WHERE c.workspace_id=NEW.workspace_id AND c.id=NEW.campaign_id
         AND c.activated_sequence_id=NEW.sequence_id AND c.activated_audience_id=NEW.audience_id
         AND a.id=NEW.audience_member_id AND a.eligibility_status='ACCEPTED'
         AND NEW.frozen_destination=r.canonical_address AND NEW.frozen_variables=a.frozen_variables) THEN
        RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Enrollment must use the activated audience and frozen recipient snapshot';
    END IF;
    RETURN NEW;
END;
$function$;
REVOKE ALL ON FUNCTION public.app_guard_enrollment_snapshot() FROM PUBLIC, anon, authenticated, service_role;
CREATE TRIGGER campaign_enrollments_snapshot_guard BEFORE INSERT ON public.campaign_enrollments
FOR EACH ROW EXECUTE FUNCTION public.app_guard_enrollment_snapshot();

CREATE FUNCTION public.app_guard_capture_source() RETURNS trigger
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = ''
AS $function$
DECLARE parent_status text;
BEGIN
    SELECT status INTO STRICT parent_status FROM public.campaign_audiences
      WHERE workspace_id=NEW.workspace_id AND campaign_id=NEW.campaign_id AND id=NEW.audience_id FOR UPDATE;
    IF parent_status <> 'CAPTURING' OR NOT NEW.gate_held THEN
        RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Capture sources must acquire gates while capture is open';
    END IF;
    RETURN NEW;
END;
$function$;
REVOKE ALL ON FUNCTION public.app_guard_capture_source() FROM PUBLIC, anon, authenticated, service_role;
GRANT EXECUTE ON FUNCTION public.app_guard_capture_source() TO CURRENT_USER;
GRANT app_integrity_guard TO CURRENT_USER;
GRANT CREATE ON SCHEMA public TO app_integrity_guard;
ALTER FUNCTION public.app_guard_capture_source() OWNER TO app_integrity_guard;
REVOKE CREATE ON SCHEMA public FROM app_integrity_guard;
CREATE TRIGGER audience_capture_sources_parent_guard BEFORE INSERT ON public.audience_capture_sources
FOR EACH ROW EXECUTE FUNCTION public.app_guard_capture_source();
REVOKE EXECUTE ON FUNCTION public.app_guard_capture_source() FROM CURRENT_USER;
REVOKE app_integrity_guard FROM CURRENT_USER;

CREATE FUNCTION public.app_require_capture_release() RETURNS trigger
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = ''
AS $function$
BEGIN
    IF EXISTS (SELECT 1 FROM public.campaign_audiences a JOIN public.audience_capture_sources s
      ON s.workspace_id=a.workspace_id AND s.audience_id=a.id
      WHERE a.workspace_id=NEW.workspace_id AND a.id=NEW.id AND a.status <> 'CAPTURING' AND s.gate_held) THEN
        RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Completed or failed capture must release every list gate in the same transaction';
    END IF;
    RETURN NULL;
END;
$function$;
REVOKE ALL ON FUNCTION public.app_require_capture_release() FROM PUBLIC, anon, authenticated, service_role;
GRANT SELECT ON public.audience_capture_sources TO app_integrity_guard;
CREATE POLICY audience_capture_sources_integrity_select ON public.audience_capture_sources
FOR SELECT TO app_integrity_guard USING (true);
GRANT EXECUTE ON FUNCTION public.app_require_capture_release() TO CURRENT_USER;
GRANT app_integrity_guard TO CURRENT_USER;
GRANT CREATE ON SCHEMA public TO app_integrity_guard;
ALTER FUNCTION public.app_require_capture_release() OWNER TO app_integrity_guard;
REVOKE CREATE ON SCHEMA public FROM app_integrity_guard;
CREATE CONSTRAINT TRIGGER campaign_audiences_release_required AFTER UPDATE ON public.campaign_audiences
DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION public.app_require_capture_release();
REVOKE EXECUTE ON FUNCTION public.app_require_capture_release() FROM CURRENT_USER;
REVOKE app_integrity_guard FROM CURRENT_USER;

CREATE FUNCTION public.app_guard_planning_activation() RETURNS trigger
LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path = ''
AS $function$
BEGIN
    IF NEW.phase <> 'CAPTURE' AND NOT EXISTS (SELECT 1 FROM public.campaigns c
      WHERE c.workspace_id=NEW.workspace_id AND c.id=NEW.campaign_id AND c.activation_id=NEW.activation_id
        AND c.activated_audience_id=NEW.audience_id AND c.activated_sequence_id=NEW.sequence_id) THEN
        RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Planning must use the current immutable activation';
    END IF;
    RETURN NEW;
END;
$function$;
REVOKE ALL ON FUNCTION public.app_guard_planning_activation() FROM PUBLIC, anon, authenticated, service_role;
CREATE TRIGGER campaign_planning_jobs_activation_guard BEFORE INSERT ON public.campaign_planning_jobs
FOR EACH ROW EXECUTE FUNCTION public.app_guard_planning_activation();

COMMIT;
