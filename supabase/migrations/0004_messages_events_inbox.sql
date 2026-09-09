-- Messages/evidence/inbox draft corrected with the full five-file chain.
-- Completes earlier token/evidence references and supplies durable work transport.
-- See docs/database/MIGRATION_REVIEW.md for static review and recovery obligations.
-- Prepared only: no migration execution was performed.

BEGIN;
SET LOCAL search_path = '';

ALTER TABLE public.campaign_enrollments
    ADD CONSTRAINT campaign_enrollments_campaign_id_key UNIQUE (workspace_id, campaign_id, id);

-- ---------------------------------------------------------------------------
-- Conversations
-- ---------------------------------------------------------------------------

CREATE TABLE public.conversations (
    id uuid DEFAULT pg_catalog.gen_random_uuid() CONSTRAINT conversations_pkey PRIMARY KEY,
    workspace_id uuid NOT NULL,
    mailbox_id uuid NOT NULL,
    provider_thread_id text,
    local_anchor_id text,
    campaign_summary_id uuid,
    created_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    latest_activity_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    archived_at timestamptz,
    version bigint NOT NULL DEFAULT 1,
    updated_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    CONSTRAINT conversations_workspace_fkey FOREIGN KEY (workspace_id)
        REFERENCES public.workspaces (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT conversations_mailbox_fkey FOREIGN KEY (workspace_id, mailbox_id)
        REFERENCES public.mailboxes (workspace_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT conversations_campaign_fkey FOREIGN KEY (workspace_id, campaign_summary_id)
        REFERENCES public.campaigns (workspace_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT conversations_workspace_id_key UNIQUE (workspace_id, id),
    CONSTRAINT conversations_mailbox_id_key UNIQUE (workspace_id, mailbox_id, id),
    CONSTRAINT conversations_version_check CHECK (version > 0)
);

CREATE UNIQUE INDEX conversations_provider_thread_key
    ON public.conversations (workspace_id, mailbox_id, provider_thread_id)
    WHERE provider_thread_id IS NOT NULL;
CREATE UNIQUE INDEX conversations_local_anchor_key
    ON public.conversations (workspace_id, mailbox_id, local_anchor_id)
    WHERE provider_thread_id IS NULL AND local_anchor_id IS NOT NULL;
CREATE INDEX conversations_inbox_idx ON public.conversations (workspace_id, latest_activity_at, id);

-- ---------------------------------------------------------------------------
-- Messages and send attempts
-- ---------------------------------------------------------------------------

CREATE TABLE public.messages (
    frozen_destination text,
    frozen_sender_address text,
    frozen_sender_name text,
    terminal_reason text,
    rendered_at timestamptz,
    id uuid DEFAULT pg_catalog.gen_random_uuid() CONSTRAINT messages_pkey PRIMARY KEY,
    workspace_id uuid NOT NULL,
    purpose text NOT NULL DEFAULT 'CAMPAIGN',
    campaign_id uuid,
    enrollment_id uuid,
    sequence_id uuid,
    step_id uuid,
    controlled_send_authorization_id uuid,
    mailbox_id uuid NOT NULL,
    address_id uuid NOT NULL,
    -- Foreign key to conversations is added below, once that table exists.
    conversation_id uuid,
    rfc_message_id text,
    content_subject text,
    content_body_html text,
    content_digest text,
    renderer_version integer NOT NULL DEFAULT 1,
    due_at timestamptz,
    anchor_at timestamptz,
    schedule_generation bigint NOT NULL DEFAULT 1,
    status text NOT NULL DEFAULT 'PLANNED',
    dispatch_origin text,
    dispatch_generation bigint NOT NULL DEFAULT 1,
    claim_expires_at timestamptz,
    retry_count integer NOT NULL DEFAULT 0,
    retry_budget integer NOT NULL DEFAULT 3,
    next_retry_at timestamptz,
    accepted_at timestamptz,
    hold_reason text,
    provider_message_id text,
    version bigint NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    CONSTRAINT messages_workspace_fkey FOREIGN KEY (workspace_id)
        REFERENCES public.workspaces (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT messages_mailbox_fkey FOREIGN KEY (workspace_id, mailbox_id)
        REFERENCES public.mailboxes (workspace_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT messages_address_fkey FOREIGN KEY (workspace_id, address_id)
        REFERENCES public.recipient_addresses (workspace_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT messages_authorization_fkey FOREIGN KEY (workspace_id, controlled_send_authorization_id, mailbox_id, address_id)
        REFERENCES public.controlled_send_authorizations (workspace_id, id, mailbox_id, address_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT messages_campaign_fkey FOREIGN KEY (workspace_id, campaign_id)
        REFERENCES public.campaigns (workspace_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    -- Ensures the referenced step actually belongs to the enrollment's own sequence.
    CONSTRAINT messages_enrollment_fkey FOREIGN KEY (workspace_id, campaign_id, sequence_id, enrollment_id)
        REFERENCES public.campaign_enrollments (workspace_id, campaign_id, sequence_id, id)
        ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT messages_step_fkey FOREIGN KEY (workspace_id, sequence_id, step_id)
        REFERENCES public.sequence_steps (workspace_id, sequence_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT messages_workspace_id_key UNIQUE (workspace_id, id),
    CONSTRAINT messages_mailbox_id_key UNIQUE (workspace_id, mailbox_id, id),
    CONSTRAINT messages_purpose_check CHECK (purpose IN ('CAMPAIGN', 'CONTROLLED_TEST')),
    CONSTRAINT messages_payload_shape_check CHECK (
        (purpose = 'CAMPAIGN' AND campaign_id IS NOT NULL AND enrollment_id IS NOT NULL
            AND sequence_id IS NOT NULL AND step_id IS NOT NULL AND controlled_send_authorization_id IS NULL)
        OR (purpose = 'CONTROLLED_TEST' AND controlled_send_authorization_id IS NOT NULL
            AND campaign_id IS NULL AND enrollment_id IS NULL AND sequence_id IS NULL AND step_id IS NULL)
    ),
    CONSTRAINT messages_content_subject_check CHECK (pg_catalog.char_length(content_subject) BETWEEN 1 AND 500),
    CONSTRAINT messages_content_body_check CHECK (pg_catalog.char_length(content_body_html) <= 200000),
    CONSTRAINT messages_content_digest_check CHECK (content_digest ~ '^[0-9a-f]{32,128}$'),
    CONSTRAINT messages_renderer_version_check CHECK (renderer_version > 0),
    CONSTRAINT messages_status_check CHECK (
        status IN (
            'PLANNED', 'SCHEDULED', 'QUEUED', 'SENDING', 'RETRY_SCHEDULED',
            'UNKNOWN_OUTCOME', 'SENT', 'FAILED', 'SKIPPED', 'CANCELLED'
        )
    ),
    CONSTRAINT messages_accepted_check CHECK (status <> 'SENT' OR accepted_at IS NOT NULL),
    CONSTRAINT messages_schedule_generation_check CHECK (schedule_generation > 0),
    CONSTRAINT messages_dispatch_generation_check CHECK (dispatch_generation > 0),
    CONSTRAINT messages_retry_count_check CHECK (retry_count >= 0),
    CONSTRAINT messages_retry_budget_check CHECK (retry_budget >= 0),
    CONSTRAINT messages_version_check CHECK (version > 0),
    CONSTRAINT messages_enrollment_recipient_fkey FOREIGN KEY (workspace_id, enrollment_id, address_id)
    REFERENCES public.campaign_enrollments (workspace_id, id, address_id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT messages_recipient_id_key UNIQUE (workspace_id, address_id, id),
    CONSTRAINT messages_outreach_identity_key UNIQUE (workspace_id, mailbox_id, id, campaign_id, enrollment_id),
    CONSTRAINT messages_render_shape_check CHECK (
    (rendered_at IS NULL AND content_subject IS NULL AND content_body_html IS NULL AND content_digest IS NULL
        AND frozen_destination IS NULL AND frozen_sender_address IS NULL AND frozen_sender_name IS NULL
        AND status IN ('PLANNED','SKIPPED','CANCELLED','FAILED'))
    OR (rendered_at IS NOT NULL AND content_subject IS NOT NULL AND content_body_html IS NOT NULL AND content_digest IS NOT NULL
        AND frozen_destination IS NOT NULL AND frozen_sender_address IS NOT NULL)),
    CONSTRAINT messages_envelope_shape_check CHECK (
        (frozen_destination IS NULL OR (pg_catalog.char_length(frozen_destination) BETWEEN 3 AND 320 AND frozen_destination !~ '[[:cntrl:]]'))
        AND (frozen_sender_address IS NULL OR (pg_catalog.char_length(frozen_sender_address) BETWEEN 3 AND 320 AND frozen_sender_address !~ '[[:cntrl:]]'))
        AND (frozen_sender_name IS NULL OR (pg_catalog.char_length(frozen_sender_name) <= 200 AND frozen_sender_name !~ '[[:cntrl:]]'))),
    CONSTRAINT messages_due_state_check CHECK (status NOT IN ('SCHEDULED','RETRY_SCHEDULED','QUEUED','SENDING','UNKNOWN_OUTCOME','SENT') OR (due_at IS NOT NULL AND anchor_at IS NOT NULL)),
    CONSTRAINT messages_dispatch_state_check CHECK (status <> 'QUEUED' OR
        (dispatch_origin IS NOT NULL AND dispatch_origin IN ('SCHEDULED','RETRY_SCHEDULED') AND claim_expires_at IS NOT NULL)),
    CONSTRAINT messages_dispatch_origin_check CHECK (dispatch_origin IS NULL OR dispatch_origin IN ('SCHEDULED','RETRY_SCHEDULED')),
    CONSTRAINT messages_retry_state_check CHECK (retry_count <= retry_budget AND (status <> 'RETRY_SCHEDULED' OR (next_retry_at IS NOT NULL AND due_at >= next_retry_at))),
    CONSTRAINT messages_acceptance_state_check CHECK ((status = 'SENT') = (accepted_at IS NOT NULL)),
    CONSTRAINT messages_terminal_reason_check CHECK ((status IN ('FAILED','SKIPPED','CANCELLED')) = (terminal_reason IS NOT NULL)),
    CONSTRAINT messages_reason_size_check CHECK (terminal_reason IS NULL OR pg_catalog.char_length(terminal_reason) BETWEEN 1 AND 500)
);

-- Scheduler due-work query (SCHEDULER.md): SCHEDULED/RETRY_SCHEDULED messages
-- with due_at <= now. This is the highest-value index in the whole schema.
CREATE INDEX messages_due_idx
    ON public.messages (due_at, id)
    WHERE status IN ('SCHEDULED', 'RETRY_SCHEDULED');
CREATE INDEX messages_claim_expiry_idx
    ON public.messages (claim_expires_at, id)
    WHERE status = 'QUEUED';
CREATE INDEX messages_workspace_campaign_idx ON public.messages (workspace_id, campaign_id, id);
CREATE INDEX messages_mailbox_rfc_idx
    ON public.messages (workspace_id, mailbox_id, rfc_message_id)
    WHERE rfc_message_id IS NOT NULL;
CREATE UNIQUE INDEX messages_enrollment_step_key
    ON public.messages (workspace_id, enrollment_id, step_id)
    WHERE purpose = 'CAMPAIGN';
CREATE UNIQUE INDEX messages_authorization_key
    ON public.messages (workspace_id, controlled_send_authorization_id)
    WHERE purpose = 'CONTROLLED_TEST';
CREATE UNIQUE INDEX messages_provider_message_key
    ON public.messages (workspace_id, mailbox_id, provider_message_id)
    WHERE provider_message_id IS NOT NULL;

CREATE TABLE public.message_attempts (
    id uuid DEFAULT pg_catalog.gen_random_uuid() CONSTRAINT message_attempts_pkey PRIMARY KEY,
    workspace_id uuid NOT NULL,
    message_id uuid NOT NULL,
    mailbox_id uuid NOT NULL,
    ordinal integer NOT NULL,
    invocation_owner text NOT NULL,
    credential_generation bigint NOT NULL,
    authorization_deadline timestamptz NOT NULL,
    dispatch_generation bigint NOT NULL,
    evidence_state text NOT NULL DEFAULT 'PREPARED',
    started_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    completed_at timestamptz,
    provider_request_id text,
    provider_message_ref text,
    provider_thread_ref text,
    error_category text,
    error_code text,
    reconciliation_metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    CONSTRAINT message_attempts_workspace_fkey FOREIGN KEY (workspace_id)
        REFERENCES public.workspaces (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT message_attempts_message_fkey FOREIGN KEY (workspace_id, mailbox_id, message_id)
        REFERENCES public.messages (workspace_id, mailbox_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT message_attempts_mailbox_fkey FOREIGN KEY (workspace_id, mailbox_id)
        REFERENCES public.mailboxes (workspace_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT message_attempts_workspace_id_key UNIQUE (workspace_id, id),
    CONSTRAINT message_attempts_ordinal_key UNIQUE (workspace_id, message_id, ordinal),
    CONSTRAINT message_attempts_ordinal_check CHECK (ordinal > 0),
    CONSTRAINT message_attempts_credential_generation_check CHECK (credential_generation > 0),
    CONSTRAINT message_attempts_dispatch_generation_check CHECK (dispatch_generation > 0),
    CONSTRAINT message_attempts_evidence_state_check CHECK (
        evidence_state IN ('PREPARED', 'ACCEPTED', 'REJECTED', 'UNKNOWN', 'NOT_INVOKED')
    ),
    CONSTRAINT message_attempts_error_category_check CHECK (
        error_category IS NULL OR error_category IN (
            'RATE_LIMIT', 'TEMPORARY_PROVIDER_ERROR', 'AUTH_FAILURE', 'PERMANENT_RECIPIENT_FAILURE',
            'POLICY_REJECTION', 'NETWORK_ERROR', 'CONFIGURATION_FAILURE'
        )
    ),
    CONSTRAINT message_attempts_reconciliation_metadata_check CHECK (
        pg_catalog.jsonb_typeof(reconciliation_metadata) = 'object'
        AND pg_catalog.octet_length(pg_catalog.convert_to(reconciliation_metadata::text, 'UTF8')) <= 4096
    ),
    CONSTRAINT message_attempts_connection_fkey FOREIGN KEY (workspace_id, mailbox_id, credential_generation)
    REFERENCES public.mailbox_connections (workspace_id, mailbox_id, generation) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT message_attempts_invocation_check CHECK (pg_catalog.char_length(invocation_owner) BETWEEN 1 AND 200 AND authorization_deadline > started_at),
    CONSTRAINT message_attempts_completed_check CHECK (
        (evidence_state IN ('PREPARED','UNKNOWN') AND completed_at IS NULL)
        OR (evidence_state IN ('ACCEPTED','REJECTED','NOT_INVOKED') AND completed_at IS NOT NULL AND completed_at >= started_at))
);

-- At most one unresolved attempt per message (MESSAGE_STATE_MACHINE.md).
CREATE UNIQUE INDEX message_attempts_unresolved_idx
    ON public.message_attempts (workspace_id, message_id)
    WHERE evidence_state IN ('PREPARED', 'UNKNOWN');
CREATE INDEX message_attempts_deadline_idx ON public.message_attempts (authorization_deadline);
CREATE INDEX message_attempts_provider_request_idx
    ON public.message_attempts (workspace_id, mailbox_id, provider_request_id)
    WHERE provider_request_id IS NOT NULL;

CREATE TABLE public.attempt_evidence (
    id uuid DEFAULT pg_catalog.gen_random_uuid() CONSTRAINT attempt_evidence_pkey PRIMARY KEY,
    workspace_id uuid NOT NULL,
    attempt_id uuid NOT NULL,
    source_kind text NOT NULL,
    source_key text NOT NULL,
    observed_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    occurred_at timestamptz,
    classification text NOT NULL,
    payload_ref text,
    created_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    CONSTRAINT attempt_evidence_workspace_fkey FOREIGN KEY (workspace_id)
        REFERENCES public.workspaces (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT attempt_evidence_attempt_fkey FOREIGN KEY (workspace_id, attempt_id)
        REFERENCES public.message_attempts (workspace_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT attempt_evidence_identity_key UNIQUE (workspace_id, attempt_id, source_kind, source_key),
    CONSTRAINT attempt_evidence_source_kind_check CHECK (pg_catalog.char_length(source_kind) BETWEEN 1 AND 100),
    CONSTRAINT attempt_evidence_source_key_check CHECK (pg_catalog.char_length(source_key) BETWEEN 1 AND 200),
    CONSTRAINT attempt_evidence_classification_check CHECK (pg_catalog.char_length(classification) BETWEEN 1 AND 100),
    CONSTRAINT attempt_evidence_payload_ref_check CHECK (payload_ref IS NULL OR pg_catalog.char_length(payload_ref) <= 1024)
);

CREATE INDEX attempt_evidence_parent_time_idx ON public.attempt_evidence (workspace_id, attempt_id, observed_at);

-- ---------------------------------------------------------------------------
-- Inbound evidence and outcomes
-- ---------------------------------------------------------------------------

CREATE TABLE public.inbound_messages (
    imap_folder text,
    id uuid DEFAULT pg_catalog.gen_random_uuid() CONSTRAINT inbound_messages_pkey PRIMARY KEY,
    workspace_id uuid NOT NULL,
    mailbox_id uuid NOT NULL,
    conversation_id uuid NOT NULL,
    connection_generation bigint NOT NULL,
    provider_message_id text NOT NULL,
    imap_uidvalidity bigint,
    imap_uid bigint,
    rfc_message_id text,
    in_reply_to text,
    references_header text,
    participants jsonb NOT NULL DEFAULT '{}'::jsonb,
    subject text,
    content_text text,
    received_at timestamptz NOT NULL,
    observed_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    direction text NOT NULL DEFAULT 'INBOUND',
    classification text,
    association_status text NOT NULL DEFAULT 'UNRESOLVED',
    version bigint NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    CONSTRAINT inbound_messages_workspace_fkey FOREIGN KEY (workspace_id)
        REFERENCES public.workspaces (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT inbound_messages_mailbox_fkey FOREIGN KEY (workspace_id, mailbox_id)
        REFERENCES public.mailboxes (workspace_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT inbound_messages_conversation_fkey FOREIGN KEY (workspace_id, mailbox_id, conversation_id)
        REFERENCES public.conversations (workspace_id, mailbox_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT inbound_messages_workspace_id_key UNIQUE (workspace_id, id),
    CONSTRAINT inbound_messages_mailbox_id_key UNIQUE (workspace_id, mailbox_id, id),
    CONSTRAINT inbound_messages_provider_message_key UNIQUE (workspace_id, mailbox_id, provider_message_id),
    CONSTRAINT inbound_messages_participants_check CHECK (
        pg_catalog.jsonb_typeof(participants) = 'object'
        AND pg_catalog.octet_length(pg_catalog.convert_to(participants::text, 'UTF8')) <= 8192
    ),
    CONSTRAINT inbound_messages_subject_check CHECK (subject IS NULL OR pg_catalog.char_length(subject) <= 500),
    CONSTRAINT inbound_messages_content_text_check CHECK (
        content_text IS NULL OR pg_catalog.char_length(content_text) <= 50000
    ),
    CONSTRAINT inbound_messages_direction_check CHECK (direction IN ('INBOUND')),
    CONSTRAINT inbound_messages_association_status_check CHECK (association_status IN ('UNRESOLVED', 'MATCHED')),
    CONSTRAINT inbound_messages_connection_generation_check CHECK (connection_generation > 0),
    CONSTRAINT inbound_messages_version_check CHECK (version > 0),
    CONSTRAINT inbound_messages_imap_identity_check CHECK (
    (imap_folder IS NULL AND imap_uidvalidity IS NULL AND imap_uid IS NULL)
    OR (imap_folder IS NOT NULL AND pg_catalog.char_length(imap_folder) BETWEEN 1 AND 512 AND imap_uidvalidity IS NOT NULL
        AND imap_uidvalidity > 0 AND imap_uid IS NOT NULL AND imap_uid > 0)),
    CONSTRAINT inbound_messages_provider_identity_check CHECK (pg_catalog.char_length(provider_message_id) BETWEEN 1 AND 1024),
    CONSTRAINT inbound_messages_header_size_check CHECK (
        (references_header IS NULL OR pg_catalog.octet_length(pg_catalog.convert_to(references_header, 'UTF8')) <= 16384)
        AND (in_reply_to IS NULL OR pg_catalog.octet_length(pg_catalog.convert_to(in_reply_to, 'UTF8')) <= 4096))
);

CREATE INDEX inbound_messages_mailbox_received_idx ON public.inbound_messages (workspace_id, mailbox_id, received_at);
CREATE INDEX inbound_messages_unresolved_idx
    ON public.inbound_messages (workspace_id, observed_at)
    WHERE association_status = 'UNRESOLVED';
CREATE INDEX inbound_messages_rfc_idx
    ON public.inbound_messages (workspace_id, rfc_message_id)
    WHERE rfc_message_id IS NOT NULL;

CREATE TABLE public.inbound_outreach_links (
    id uuid DEFAULT pg_catalog.gen_random_uuid() CONSTRAINT inbound_outreach_links_pkey PRIMARY KEY,
    workspace_id uuid NOT NULL,
    mailbox_id uuid NOT NULL,
    inbound_message_id uuid NOT NULL,
    outbound_message_id uuid NOT NULL,
    campaign_id uuid,
    enrollment_id uuid,
    evidence_type text NOT NULL,
    confidence text NOT NULL,
    status text NOT NULL DEFAULT 'CANDIDATE',
    matched_at timestamptz,
    version bigint NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    CONSTRAINT inbound_outreach_links_workspace_fkey FOREIGN KEY (workspace_id)
        REFERENCES public.workspaces (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT inbound_outreach_links_inbound_fkey FOREIGN KEY (workspace_id, mailbox_id, inbound_message_id)
        REFERENCES public.inbound_messages (workspace_id, mailbox_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT inbound_outreach_links_outbound_fkey FOREIGN KEY (workspace_id, mailbox_id, outbound_message_id)
        REFERENCES public.messages (workspace_id, mailbox_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT inbound_outreach_links_enrollment_fkey FOREIGN KEY (workspace_id, campaign_id, enrollment_id)
        REFERENCES public.campaign_enrollments (workspace_id, campaign_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT inbound_outreach_links_pair_key UNIQUE (workspace_id, inbound_message_id, outbound_message_id),
    CONSTRAINT inbound_outreach_links_evidence_type_check CHECK (pg_catalog.char_length(evidence_type) BETWEEN 1 AND 100),
    CONSTRAINT inbound_outreach_links_confidence_check CHECK (confidence IN ('LOW', 'MEDIUM', 'HIGH')),
    CONSTRAINT inbound_outreach_links_status_check CHECK (status IN ('CANDIDATE', 'CONFIRMED', 'REJECTED')),
    CONSTRAINT inbound_outreach_links_matched_check CHECK ((status = 'CONFIRMED') = (matched_at IS NOT NULL)),
    CONSTRAINT inbound_outreach_links_campaign_pair_check CHECK ((campaign_id IS NULL) = (enrollment_id IS NULL)),
    CONSTRAINT inbound_outreach_links_version_check CHECK (version > 0),
    CONSTRAINT inbound_outreach_links_attribution_fkey
    FOREIGN KEY (workspace_id, mailbox_id, outbound_message_id, campaign_id, enrollment_id)
    REFERENCES public.messages (workspace_id, mailbox_id, id, campaign_id, enrollment_id) ON UPDATE RESTRICT ON DELETE RESTRICT
);

CREATE INDEX inbound_outreach_links_enrollment_idx ON public.inbound_outreach_links (workspace_id, enrollment_id);
CREATE INDEX inbound_outreach_links_inbound_idx ON public.inbound_outreach_links (workspace_id, inbound_message_id);

CREATE TABLE public.recipient_outcomes (
    id uuid DEFAULT pg_catalog.gen_random_uuid() CONSTRAINT recipient_outcomes_pkey PRIMARY KEY,
    workspace_id uuid NOT NULL,
    enrollment_id uuid NOT NULL,
    kind text NOT NULL,
    source_key text NOT NULL,
    occurred_at timestamptz NOT NULL,
    observed_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    inbound_message_id uuid,
    -- Foreign key to domain_events is added below, once that table exists.
    domain_event_id uuid,
    created_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    CONSTRAINT recipient_outcomes_workspace_fkey FOREIGN KEY (workspace_id)
        REFERENCES public.workspaces (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT recipient_outcomes_enrollment_fkey FOREIGN KEY (workspace_id, enrollment_id)
        REFERENCES public.campaign_enrollments (workspace_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT recipient_outcomes_inbound_fkey FOREIGN KEY (workspace_id, inbound_message_id)
        REFERENCES public.inbound_messages (workspace_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT recipient_outcomes_identity_key UNIQUE (workspace_id, enrollment_id, kind, source_key),
    CONSTRAINT recipient_outcomes_kind_check CHECK (kind IN ('REPLIED', 'UNSUBSCRIBED', 'HARD_BOUNCE', 'COMPLAINT')),
    CONSTRAINT recipient_outcomes_source_key_check CHECK (pg_catalog.char_length(source_key) BETWEEN 1 AND 200)
);

-- ---------------------------------------------------------------------------
-- Durable events, work and safety holds
-- ---------------------------------------------------------------------------

CREATE TABLE public.provider_receipts (
    lease_expires_at timestamptz,
    payload_digest text,
    id uuid DEFAULT pg_catalog.gen_random_uuid() CONSTRAINT provider_receipts_pkey PRIMARY KEY,
    workspace_id uuid NOT NULL,
    mailbox_id uuid,
    provider text NOT NULL,
    scope_kind text NOT NULL DEFAULT 'MAILBOX',
    event_identity text NOT NULL,
    verified_at timestamptz,
    received_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    source_schema text NOT NULL,
    receipt_status text NOT NULL DEFAULT 'RECEIVED',
    lease_owner text,
    lease_generation bigint NOT NULL DEFAULT 1,
    retry_count integer NOT NULL DEFAULT 0,
    next_due_at timestamptz,
    safe_error text,
    payload_ref text,
    version bigint NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    CONSTRAINT provider_receipts_workspace_fkey FOREIGN KEY (workspace_id)
        REFERENCES public.workspaces (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT provider_receipts_mailbox_fkey FOREIGN KEY (workspace_id, mailbox_id)
        REFERENCES public.mailboxes (workspace_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT provider_receipts_workspace_id_key UNIQUE (workspace_id, id),
    CONSTRAINT provider_receipts_scope_kind_check CHECK (scope_kind IN ('MAILBOX', 'WORKSPACE')),
    CONSTRAINT provider_receipts_scope_shape_check CHECK ((scope_kind = 'MAILBOX') = (mailbox_id IS NOT NULL)),
    CONSTRAINT provider_receipts_provider_check CHECK (provider IN ('GMAIL', 'MICROSOFT', 'SMTP')),
    CONSTRAINT provider_receipts_source_schema_check CHECK (pg_catalog.char_length(source_schema) BETWEEN 1 AND 100),
    CONSTRAINT provider_receipts_status_check CHECK (
        receipt_status IN ('RECEIVED', 'PROCESSING', 'PROCESSED', 'RETRY', 'FAILED')
    ),
    CONSTRAINT provider_receipts_lease_generation_check CHECK (lease_generation > 0),
    CONSTRAINT provider_receipts_retry_count_check CHECK (retry_count >= 0),
    CONSTRAINT provider_receipts_version_check CHECK (version > 0),
    CONSTRAINT provider_receipts_lease_shape_check CHECK (
    (lease_owner IS NULL) = (lease_expires_at IS NULL) AND (receipt_status <> 'PROCESSING' OR lease_owner IS NOT NULL)),
    CONSTRAINT provider_receipts_identity_check CHECK (pg_catalog.char_length(event_identity) BETWEEN 1 AND 1024),
    CONSTRAINT provider_receipts_payload_check CHECK ((payload_ref IS NULL AND payload_digest IS NULL) OR
        (payload_ref IS NOT NULL AND pg_catalog.char_length(payload_ref) BETWEEN 1 AND 1024 AND payload_digest IS NOT NULL AND payload_digest ~ '^[0-9a-f]{64}$'))
);

CREATE UNIQUE INDEX provider_receipts_mailbox_identity_key
    ON public.provider_receipts (workspace_id, mailbox_id, provider, event_identity)
    WHERE scope_kind = 'MAILBOX';
CREATE UNIQUE INDEX provider_receipts_workspace_identity_key
    ON public.provider_receipts (workspace_id, provider, event_identity)
    WHERE scope_kind = 'WORKSPACE';
CREATE INDEX provider_receipts_due_idx
    ON public.provider_receipts (next_due_at, id)
    WHERE receipt_status IN ('RECEIVED', 'RETRY');

CREATE TABLE public.safety_holds (
    id uuid DEFAULT pg_catalog.gen_random_uuid() CONSTRAINT safety_holds_pkey PRIMARY KEY,
    workspace_id uuid NOT NULL,
    source_receipt_id uuid,
    source_work_identity text NOT NULL,
    target_kind text NOT NULL,
    target_mailbox_id uuid,
    status text NOT NULL DEFAULT 'ACTIVE',
    reason text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    resolved_at timestamptz,
    version bigint NOT NULL DEFAULT 1,
    updated_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    CONSTRAINT safety_holds_workspace_fkey FOREIGN KEY (workspace_id)
        REFERENCES public.workspaces (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT safety_holds_receipt_fkey FOREIGN KEY (workspace_id, source_receipt_id)
        REFERENCES public.provider_receipts (workspace_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT safety_holds_mailbox_fkey FOREIGN KEY (workspace_id, target_mailbox_id)
        REFERENCES public.mailboxes (workspace_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT safety_holds_source_identity_key UNIQUE (workspace_id, source_work_identity),
    CONSTRAINT safety_holds_target_kind_check CHECK (target_kind IN ('WORKSPACE', 'MAILBOX')),
    CONSTRAINT safety_holds_target_shape_check CHECK ((target_kind = 'MAILBOX') = (target_mailbox_id IS NOT NULL)),
    CONSTRAINT safety_holds_status_check CHECK (status IN ('ACTIVE', 'RESOLVED')),
    CONSTRAINT safety_holds_resolution_check CHECK ((status = 'RESOLVED') = (resolved_at IS NOT NULL)),
    CONSTRAINT safety_holds_reason_check CHECK (pg_catalog.char_length(reason) BETWEEN 1 AND 500),
    CONSTRAINT safety_holds_version_check CHECK (version > 0)
);

CREATE INDEX safety_holds_active_target_idx
    ON public.safety_holds (workspace_id, target_kind, target_mailbox_id)
    WHERE status = 'ACTIVE';

CREATE TABLE public.domain_events (
    id uuid DEFAULT pg_catalog.gen_random_uuid() CONSTRAINT domain_events_pkey PRIMARY KEY,
    workspace_id uuid NOT NULL,
    event_type text NOT NULL,
    schema_version integer NOT NULL DEFAULT 1,
    aggregate_type text NOT NULL,
    aggregate_id uuid NOT NULL,
    aggregate_version bigint,
    semantic_key text NOT NULL,
    occurred_at timestamptz NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    correlation_id uuid,
    payload jsonb NOT NULL DEFAULT '{}'::jsonb,
    CONSTRAINT domain_events_workspace_fkey FOREIGN KEY (workspace_id)
        REFERENCES public.workspaces (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT domain_events_workspace_id_key UNIQUE (workspace_id, id),
    CONSTRAINT domain_events_semantic_key UNIQUE (workspace_id, event_type, semantic_key),
    CONSTRAINT domain_events_event_type_check CHECK (pg_catalog.char_length(event_type) BETWEEN 1 AND 100),
    CONSTRAINT domain_events_schema_version_check CHECK (schema_version > 0),
    CONSTRAINT domain_events_aggregate_type_check CHECK (pg_catalog.char_length(aggregate_type) BETWEEN 1 AND 100),
    CONSTRAINT domain_events_semantic_key_field_check CHECK (pg_catalog.char_length(semantic_key) BETWEEN 1 AND 200),
    CONSTRAINT domain_events_payload_check CHECK (
        pg_catalog.jsonb_typeof(payload) = 'object'
        AND pg_catalog.octet_length(pg_catalog.convert_to(payload::text, 'UTF8')) <= 8192
    )
);

CREATE UNIQUE INDEX domain_events_aggregate_version_key
    ON public.domain_events (workspace_id, aggregate_type, aggregate_id, aggregate_version, event_type)
    WHERE aggregate_version IS NOT NULL;
CREATE INDEX domain_events_aggregate_idx ON public.domain_events (workspace_id, aggregate_type, aggregate_id, recorded_at);
CREATE INDEX domain_events_recorded_idx ON public.domain_events (recorded_at);

CREATE TABLE public.outbox_work (
    id uuid DEFAULT pg_catalog.gen_random_uuid() CONSTRAINT outbox_work_pkey PRIMARY KEY,
    workspace_id uuid NOT NULL,
    kind text NOT NULL,
    schema_version integer NOT NULL DEFAULT 1,
    resource_id uuid NOT NULL,
    resource_type text NOT NULL,
    event_id uuid,
    semantic_key text NOT NULL,
    correlation_id uuid,
    created_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    available_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    superseded boolean NOT NULL DEFAULT false,
    superseded_at timestamptz,
    CONSTRAINT outbox_work_workspace_fkey FOREIGN KEY (workspace_id)
        REFERENCES public.workspaces (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT outbox_work_event_fkey FOREIGN KEY (workspace_id, event_id)
        REFERENCES public.domain_events (workspace_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT outbox_work_workspace_id_key UNIQUE (workspace_id, id),
    CONSTRAINT outbox_work_semantic_key UNIQUE (workspace_id, kind, semantic_key),
    CONSTRAINT outbox_work_kind_check CHECK (pg_catalog.char_length(kind) BETWEEN 1 AND 100),
    CONSTRAINT outbox_work_resource_type_check CHECK (pg_catalog.char_length(resource_type) BETWEEN 1 AND 100),
    CONSTRAINT outbox_work_schema_version_check CHECK (schema_version > 0),
    CONSTRAINT outbox_work_superseded_check CHECK (superseded = (superseded_at IS NOT NULL))
);

CREATE INDEX outbox_work_available_idx
    ON public.outbox_work (workspace_id, available_at)
    WHERE NOT superseded;

CREATE TABLE public.outbox_deliveries (
    id uuid DEFAULT pg_catalog.gen_random_uuid() CONSTRAINT outbox_deliveries_pkey PRIMARY KEY,
    workspace_id uuid NOT NULL,
    work_id uuid NOT NULL,
    consumer text NOT NULL,
    state text NOT NULL DEFAULT 'PENDING',
    lease_owner text,
    lease_generation bigint NOT NULL DEFAULT 1,
    lease_expires_at timestamptz,
    next_attempt_at timestamptz DEFAULT pg_catalog.transaction_timestamp(),
    attempts integer NOT NULL DEFAULT 0,
    safe_error text,
    published_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    CONSTRAINT outbox_deliveries_workspace_fkey FOREIGN KEY (workspace_id)
        REFERENCES public.workspaces (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT outbox_deliveries_work_fkey FOREIGN KEY (workspace_id, work_id)
        REFERENCES public.outbox_work (workspace_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT outbox_deliveries_consumer_key UNIQUE (workspace_id, work_id, consumer),
    CONSTRAINT outbox_deliveries_consumer_check CHECK (pg_catalog.char_length(consumer) BETWEEN 1 AND 100),
    CONSTRAINT outbox_deliveries_state_check CHECK (
        state IN ('PENDING', 'LEASED', 'PUBLISHED', 'RETRY', 'COMPLETED', 'FAILED', 'SUPERSEDED')
    ),
    CONSTRAINT outbox_deliveries_lease_generation_check CHECK (lease_generation > 0),
    CONSTRAINT outbox_deliveries_attempts_check CHECK (attempts >= 0),
    CONSTRAINT outbox_deliveries_lease_shape_check CHECK (
    (lease_owner IS NULL) = (lease_expires_at IS NULL) AND (state <> 'LEASED' OR lease_owner IS NOT NULL)),
    CONSTRAINT outbox_deliveries_publication_check CHECK (state <> 'PUBLISHED' OR published_at IS NOT NULL),
    CONSTRAINT outbox_deliveries_due_check CHECK (state NOT IN ('PENDING','RETRY') OR next_attempt_at IS NOT NULL)
);

-- Relay poll query for unpublished work (EVENT_SYSTEM.md).
CREATE INDEX outbox_deliveries_unpublished_idx
    ON public.outbox_deliveries (next_attempt_at, id)
    WHERE state IN ('PENDING', 'RETRY');

CREATE TABLE public.consumer_receipts (
    workspace_id uuid NOT NULL,
    consumer text NOT NULL,
    work_id uuid NOT NULL,
    effect_version bigint NOT NULL,
    completed_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    result_ref text,
    CONSTRAINT consumer_receipts_pkey PRIMARY KEY (workspace_id, consumer, work_id, effect_version),
    CONSTRAINT consumer_receipts_work_fkey FOREIGN KEY (workspace_id, work_id)
        REFERENCES public.outbox_work (workspace_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT consumer_receipts_effect_version_check CHECK (effect_version > 0)
);

CREATE INDEX consumer_receipts_completed_idx ON public.consumer_receipts (completed_at);

-- ---------------------------------------------------------------------------
-- Deferred foreign keys (targets did not exist until this migration)
-- ---------------------------------------------------------------------------

ALTER TABLE public.messages
    ADD CONSTRAINT messages_conversation_fkey FOREIGN KEY (workspace_id, mailbox_id, conversation_id)
        REFERENCES public.conversations (workspace_id, mailbox_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT;
ALTER TABLE public.suppression_sources
    ADD CONSTRAINT suppression_sources_provider_receipt_fkey FOREIGN KEY (workspace_id, provider_receipt_id)
        REFERENCES public.provider_receipts (workspace_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    ADD CONSTRAINT suppression_sources_domain_event_fkey FOREIGN KEY (workspace_id, domain_event_id)
        REFERENCES public.domain_events (workspace_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT;
ALTER TABLE public.unsubscribe_tokens
    ADD CONSTRAINT unsubscribe_tokens_message_fkey FOREIGN KEY (workspace_id, address_id, message_id)
        REFERENCES public.messages (workspace_id, address_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT;
ALTER TABLE public.recipient_outcomes
    ADD CONSTRAINT recipient_outcomes_domain_event_fkey FOREIGN KEY (workspace_id, domain_event_id)
        REFERENCES public.domain_events (workspace_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT;

-- ---------------------------------------------------------------------------
-- Row level security and runtime capabilities

ALTER TABLE public.conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.conversations FORCE ROW LEVEL SECURITY;
ALTER TABLE public.messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.messages FORCE ROW LEVEL SECURITY;
ALTER TABLE public.message_attempts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.message_attempts FORCE ROW LEVEL SECURITY;
ALTER TABLE public.attempt_evidence ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.attempt_evidence FORCE ROW LEVEL SECURITY;
ALTER TABLE public.inbound_messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.inbound_messages FORCE ROW LEVEL SECURITY;
ALTER TABLE public.inbound_outreach_links ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.inbound_outreach_links FORCE ROW LEVEL SECURITY;
ALTER TABLE public.recipient_outcomes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.recipient_outcomes FORCE ROW LEVEL SECURITY;
ALTER TABLE public.provider_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.provider_receipts FORCE ROW LEVEL SECURITY;
ALTER TABLE public.safety_holds ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.safety_holds FORCE ROW LEVEL SECURITY;
ALTER TABLE public.domain_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.domain_events FORCE ROW LEVEL SECURITY;
ALTER TABLE public.outbox_work ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.outbox_work FORCE ROW LEVEL SECURITY;
ALTER TABLE public.outbox_deliveries ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.outbox_deliveries FORCE ROW LEVEL SECURITY;
ALTER TABLE public.consumer_receipts ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.consumer_receipts FORCE ROW LEVEL SECURITY;

REVOKE ALL PRIVILEGES ON TABLE
    public.conversations, public.messages, public.message_attempts, public.attempt_evidence,
    public.inbound_messages, public.inbound_outreach_links, public.recipient_outcomes,
    public.provider_receipts, public.safety_holds, public.domain_events,
    public.outbox_work, public.outbox_deliveries, public.consumer_receipts
    FROM PUBLIC, anon, authenticated, service_role;


CREATE TRIGGER conversations_touch_row
    BEFORE UPDATE ON public.conversations FOR EACH ROW EXECUTE FUNCTION public.app_touch_row();
CREATE TRIGGER messages_touch_row
    BEFORE UPDATE ON public.messages FOR EACH ROW EXECUTE FUNCTION public.app_touch_row();
CREATE TRIGGER inbound_messages_touch_row
    BEFORE UPDATE ON public.inbound_messages FOR EACH ROW EXECUTE FUNCTION public.app_touch_row();
CREATE TRIGGER inbound_outreach_links_touch_row
    BEFORE UPDATE ON public.inbound_outreach_links FOR EACH ROW EXECUTE FUNCTION public.app_touch_row();
CREATE TRIGGER provider_receipts_touch_row
    BEFORE UPDATE ON public.provider_receipts FOR EACH ROW EXECUTE FUNCTION public.app_touch_row();
CREATE TRIGGER safety_holds_touch_row
    BEFORE UPDATE ON public.safety_holds FOR EACH ROW EXECUTE FUNCTION public.app_touch_row();

CREATE UNIQUE INDEX inbound_messages_imap_identity_key ON public.inbound_messages
(workspace_id, mailbox_id, imap_folder, imap_uidvalidity, imap_uid) WHERE imap_uid IS NOT NULL;

CREATE FUNCTION public.app_guard_message_snapshot() RETURNS trigger
LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path = ''
AS $function$
BEGIN
    IF OLD.rendered_at IS NOT NULL AND
       (NEW.content_subject, NEW.content_body_html, NEW.content_digest, NEW.renderer_version, NEW.rendered_at,
        NEW.frozen_destination, NEW.frozen_sender_address, NEW.frozen_sender_name)
       IS DISTINCT FROM
       (OLD.content_subject, OLD.content_body_html, OLD.content_digest, OLD.renderer_version, OLD.rendered_at,
        OLD.frozen_destination, OLD.frozen_sender_address, OLD.frozen_sender_name) THEN
        RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Rendered message snapshot is immutable';
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
REVOKE ALL ON FUNCTION public.app_guard_message_snapshot() FROM PUBLIC, anon, authenticated, service_role;

CREATE TRIGGER messages_snapshot_guard BEFORE UPDATE ON public.messages FOR EACH ROW EXECUTE FUNCTION public.app_guard_message_snapshot();

CREATE POLICY conversations_api_select ON public.conversations
FOR SELECT TO app_api USING (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('product.read'));
GRANT SELECT ON public.conversations TO app_api;

CREATE POLICY messages_api_select ON public.messages
FOR SELECT TO app_api USING (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('product.read'));
GRANT SELECT ON public.messages TO app_api;

CREATE POLICY inbound_messages_api_select ON public.inbound_messages
FOR SELECT TO app_api USING (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('product.read'));
GRANT SELECT ON public.inbound_messages TO app_api;

CREATE POLICY inbound_outreach_links_api_select ON public.inbound_outreach_links
FOR SELECT TO app_api USING (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('product.read'));
GRANT SELECT ON public.inbound_outreach_links TO app_api;

CREATE POLICY recipient_outcomes_api_select ON public.recipient_outcomes
FOR SELECT TO app_api USING (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('product.read'));
GRANT SELECT ON public.recipient_outcomes TO app_api;

CREATE POLICY conversations_api_update ON public.conversations
FOR UPDATE TO app_api USING (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('inbox.manage')) WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('inbox.manage'));
GRANT UPDATE (archived_at) ON public.conversations TO app_api;

CREATE POLICY conversations_worker_general_select ON public.conversations
FOR SELECT TO app_worker_general USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.conversations TO app_worker_general;

CREATE POLICY messages_worker_general_select ON public.messages
FOR SELECT TO app_worker_general USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.messages TO app_worker_general;

CREATE POLICY message_attempts_worker_general_select ON public.message_attempts
FOR SELECT TO app_worker_general USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.message_attempts TO app_worker_general;

CREATE POLICY attempt_evidence_worker_general_select ON public.attempt_evidence
FOR SELECT TO app_worker_general USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.attempt_evidence TO app_worker_general;

CREATE POLICY inbound_messages_worker_general_select ON public.inbound_messages
FOR SELECT TO app_worker_general USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.inbound_messages TO app_worker_general;

CREATE POLICY inbound_outreach_links_worker_general_select ON public.inbound_outreach_links
FOR SELECT TO app_worker_general USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.inbound_outreach_links TO app_worker_general;

CREATE POLICY recipient_outcomes_worker_general_select ON public.recipient_outcomes
FOR SELECT TO app_worker_general USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.recipient_outcomes TO app_worker_general;

CREATE POLICY provider_receipts_worker_general_select ON public.provider_receipts
FOR SELECT TO app_worker_general USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.provider_receipts TO app_worker_general;

CREATE POLICY safety_holds_worker_general_select ON public.safety_holds
FOR SELECT TO app_worker_general USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.safety_holds TO app_worker_general;

CREATE POLICY domain_events_worker_general_select ON public.domain_events
FOR SELECT TO app_worker_general USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.domain_events TO app_worker_general;

CREATE POLICY outbox_work_worker_general_select ON public.outbox_work
FOR SELECT TO app_worker_general USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.outbox_work TO app_worker_general;

CREATE POLICY consumer_receipts_worker_general_select ON public.consumer_receipts
FOR SELECT TO app_worker_general USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.consumer_receipts TO app_worker_general;

CREATE POLICY conversations_worker_general_insert ON public.conversations
FOR INSERT TO app_worker_general WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT INSERT (id, workspace_id, mailbox_id, provider_thread_id, local_anchor_id, campaign_summary_id, latest_activity_at, archived_at) ON public.conversations TO app_worker_general;

CREATE POLICY messages_worker_general_insert ON public.messages
FOR INSERT TO app_worker_general WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT INSERT (frozen_destination, frozen_sender_address, frozen_sender_name, terminal_reason, rendered_at, id, workspace_id, purpose, campaign_id, enrollment_id, sequence_id, step_id, controlled_send_authorization_id, mailbox_id, address_id, conversation_id, rfc_message_id, content_subject, content_body_html, content_digest, renderer_version, due_at, anchor_at, schedule_generation, status, dispatch_origin, dispatch_generation, claim_expires_at, retry_count, retry_budget, next_retry_at, accepted_at, hold_reason, provider_message_id) ON public.messages TO app_worker_general;

CREATE POLICY inbound_outreach_links_worker_general_insert ON public.inbound_outreach_links
FOR INSERT TO app_worker_general WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT INSERT (id, workspace_id, mailbox_id, inbound_message_id, outbound_message_id, campaign_id, enrollment_id, evidence_type, confidence, status, matched_at) ON public.inbound_outreach_links TO app_worker_general;

CREATE POLICY recipient_outcomes_worker_general_insert ON public.recipient_outcomes
FOR INSERT TO app_worker_general WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT INSERT (id, workspace_id, enrollment_id, kind, source_key, occurred_at, observed_at, inbound_message_id, domain_event_id) ON public.recipient_outcomes TO app_worker_general;

CREATE POLICY safety_holds_worker_general_insert ON public.safety_holds
FOR INSERT TO app_worker_general WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT INSERT (id, workspace_id, source_receipt_id, source_work_identity, target_kind, target_mailbox_id, status, reason, resolved_at) ON public.safety_holds TO app_worker_general;

CREATE POLICY conversations_worker_general_update ON public.conversations
FOR UPDATE TO app_worker_general USING (workspace_id = (SELECT public.app_current_workspace_id())) WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT UPDATE (latest_activity_at, campaign_summary_id) ON public.conversations TO app_worker_general;

CREATE POLICY messages_worker_general_update ON public.messages
FOR UPDATE TO app_worker_general USING (workspace_id = (SELECT public.app_current_workspace_id())) WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT UPDATE (content_subject, content_body_html, content_digest, renderer_version, frozen_destination, frozen_sender_address, frozen_sender_name, rendered_at, status, terminal_reason, hold_reason, due_at, anchor_at, schedule_generation, retry_count, next_retry_at, accepted_at, provider_message_id, conversation_id) ON public.messages TO app_worker_general;

CREATE POLICY inbound_outreach_links_worker_general_update ON public.inbound_outreach_links
FOR UPDATE TO app_worker_general USING (workspace_id = (SELECT public.app_current_workspace_id())) WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT UPDATE (status, confidence) ON public.inbound_outreach_links TO app_worker_general;

CREATE POLICY provider_receipts_worker_general_update ON public.provider_receipts
FOR UPDATE TO app_worker_general USING (workspace_id = (SELECT public.app_current_workspace_id())) WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT UPDATE (receipt_status, lease_owner, lease_generation, lease_expires_at, retry_count, next_due_at, safe_error) ON public.provider_receipts TO app_worker_general;

CREATE POLICY safety_holds_worker_general_update ON public.safety_holds
FOR UPDATE TO app_worker_general USING (workspace_id = (SELECT public.app_current_workspace_id())) WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT UPDATE (status, resolved_at) ON public.safety_holds TO app_worker_general;

CREATE POLICY messages_worker_send_select ON public.messages
FOR SELECT TO app_worker_send USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.messages TO app_worker_send;

CREATE POLICY message_attempts_worker_send_select ON public.message_attempts
FOR SELECT TO app_worker_send USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.message_attempts TO app_worker_send;

CREATE POLICY messages_worker_sync_select ON public.messages
FOR SELECT TO app_worker_sync USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.messages TO app_worker_sync;

CREATE POLICY message_attempts_worker_sync_select ON public.message_attempts
FOR SELECT TO app_worker_sync USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.message_attempts TO app_worker_sync;

CREATE POLICY messages_scheduler_select ON public.messages
FOR SELECT TO app_scheduler USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.messages TO app_scheduler;

CREATE POLICY message_attempts_scheduler_select ON public.message_attempts
FOR SELECT TO app_scheduler USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.message_attempts TO app_scheduler;

CREATE POLICY message_attempts_worker_send_update ON public.message_attempts
FOR UPDATE TO app_worker_send USING (workspace_id = (SELECT public.app_current_workspace_id())) WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT UPDATE (evidence_state, completed_at, provider_request_id, provider_message_ref, provider_thread_ref, error_category, error_code, reconciliation_metadata) ON public.message_attempts TO app_worker_send;

CREATE POLICY attempt_evidence_worker_send_insert ON public.attempt_evidence
FOR INSERT TO app_worker_send WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT INSERT (id, workspace_id, attempt_id, source_kind, source_key, observed_at, occurred_at, classification, payload_ref) ON public.attempt_evidence TO app_worker_send;

CREATE POLICY messages_worker_send_update ON public.messages
FOR UPDATE TO app_worker_send USING (workspace_id = (SELECT public.app_current_workspace_id())) WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT UPDATE (status, terminal_reason, dispatch_origin, dispatch_generation, claim_expires_at, retry_count, next_retry_at, due_at, accepted_at, provider_message_id, hold_reason) ON public.messages TO app_worker_send;

CREATE POLICY message_attempts_worker_sync_update ON public.message_attempts
FOR UPDATE TO app_worker_sync USING (workspace_id = (SELECT public.app_current_workspace_id())) WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT UPDATE (evidence_state, completed_at, provider_request_id, provider_message_ref, provider_thread_ref, error_category, error_code, reconciliation_metadata) ON public.message_attempts TO app_worker_sync;

CREATE POLICY attempt_evidence_worker_sync_insert ON public.attempt_evidence
FOR INSERT TO app_worker_sync WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT INSERT (id, workspace_id, attempt_id, source_kind, source_key, observed_at, occurred_at, classification, payload_ref) ON public.attempt_evidence TO app_worker_sync;

CREATE POLICY messages_worker_sync_update ON public.messages
FOR UPDATE TO app_worker_sync USING (workspace_id = (SELECT public.app_current_workspace_id())) WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT UPDATE (status, terminal_reason, dispatch_origin, dispatch_generation, claim_expires_at, retry_count, next_retry_at, due_at, accepted_at, provider_message_id, hold_reason) ON public.messages TO app_worker_sync;

CREATE POLICY message_attempts_worker_send_insert ON public.message_attempts
FOR INSERT TO app_worker_send WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT INSERT (id, workspace_id, message_id, mailbox_id, ordinal, invocation_owner, credential_generation, authorization_deadline, dispatch_generation, evidence_state, started_at, completed_at, provider_request_id, provider_message_ref, provider_thread_ref, error_category, error_code, reconciliation_metadata) ON public.message_attempts TO app_worker_send;

CREATE POLICY messages_scheduler_update ON public.messages
FOR UPDATE TO app_scheduler USING (workspace_id = (SELECT public.app_current_workspace_id())) WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT UPDATE (status, dispatch_origin, dispatch_generation, claim_expires_at, due_at, anchor_at, schedule_generation, hold_reason) ON public.messages TO app_scheduler;

CREATE POLICY conversations_worker_sync_select ON public.conversations
FOR SELECT TO app_worker_sync USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.conversations TO app_worker_sync;

CREATE POLICY conversations_worker_sync_insert ON public.conversations
FOR INSERT TO app_worker_sync WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT INSERT (id, workspace_id, mailbox_id, provider_thread_id, local_anchor_id, campaign_summary_id, latest_activity_at, archived_at) ON public.conversations TO app_worker_sync;

CREATE POLICY inbound_messages_worker_sync_select ON public.inbound_messages
FOR SELECT TO app_worker_sync USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.inbound_messages TO app_worker_sync;

CREATE POLICY inbound_messages_worker_sync_insert ON public.inbound_messages
FOR INSERT TO app_worker_sync WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT INSERT (imap_folder, id, workspace_id, mailbox_id, conversation_id, connection_generation, provider_message_id, imap_uidvalidity, imap_uid, rfc_message_id, in_reply_to, references_header, participants, subject, content_text, received_at, observed_at, direction, classification, association_status) ON public.inbound_messages TO app_worker_sync;

CREATE POLICY provider_receipts_worker_sync_select ON public.provider_receipts
FOR SELECT TO app_worker_sync USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.provider_receipts TO app_worker_sync;

CREATE POLICY provider_receipts_worker_sync_insert ON public.provider_receipts
FOR INSERT TO app_worker_sync WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT INSERT (lease_expires_at, payload_digest, id, workspace_id, mailbox_id, provider, scope_kind, event_identity, verified_at, received_at, source_schema, receipt_status, lease_owner, lease_generation, retry_count, next_due_at, safe_error, payload_ref) ON public.provider_receipts TO app_worker_sync;

CREATE POLICY safety_holds_worker_sync_select ON public.safety_holds
FOR SELECT TO app_worker_sync USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.safety_holds TO app_worker_sync;

CREATE POLICY safety_holds_worker_sync_insert ON public.safety_holds
FOR INSERT TO app_worker_sync WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT INSERT (id, workspace_id, source_receipt_id, source_work_identity, target_kind, target_mailbox_id, status, reason, resolved_at) ON public.safety_holds TO app_worker_sync;

CREATE POLICY conversations_worker_sync_update ON public.conversations
FOR UPDATE TO app_worker_sync USING (workspace_id = (SELECT public.app_current_workspace_id())) WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT UPDATE (latest_activity_at) ON public.conversations TO app_worker_sync;

CREATE POLICY inbound_messages_worker_general_update ON public.inbound_messages
FOR UPDATE TO app_worker_general USING (workspace_id = (SELECT public.app_current_workspace_id())) WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT UPDATE (classification, association_status) ON public.inbound_messages TO app_worker_general;

CREATE POLICY domain_events_api_insert ON public.domain_events
FOR INSERT TO app_api WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('product.read'));
GRANT INSERT (id, workspace_id, event_type, schema_version, aggregate_type, aggregate_id, aggregate_version, semantic_key, occurred_at, recorded_at, correlation_id, payload) ON public.domain_events TO app_api;

CREATE POLICY outbox_work_api_insert ON public.outbox_work
FOR INSERT TO app_api WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('product.read'));
GRANT INSERT (id, workspace_id, kind, schema_version, resource_id, resource_type, event_id, semantic_key, correlation_id, available_at, superseded, superseded_at) ON public.outbox_work TO app_api;

CREATE POLICY outbox_deliveries_api_insert ON public.outbox_deliveries
FOR INSERT TO app_api WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('product.read'));
GRANT INSERT (id, workspace_id, work_id, consumer, state, lease_owner, lease_generation, lease_expires_at, next_attempt_at, attempts, safe_error, published_at) ON public.outbox_deliveries TO app_api;

CREATE POLICY domain_events_connection_insert ON public.domain_events
FOR INSERT TO app_connection WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('mailboxes.manage'));
GRANT INSERT (id, workspace_id, event_type, schema_version, aggregate_type, aggregate_id, aggregate_version, semantic_key, occurred_at, recorded_at, correlation_id, payload) ON public.domain_events TO app_connection;

CREATE POLICY outbox_work_connection_insert ON public.outbox_work
FOR INSERT TO app_connection WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('mailboxes.manage'));
GRANT INSERT (id, workspace_id, kind, schema_version, resource_id, resource_type, event_id, semantic_key, correlation_id, available_at, superseded, superseded_at) ON public.outbox_work TO app_connection;

CREATE POLICY outbox_deliveries_connection_insert ON public.outbox_deliveries
FOR INSERT TO app_connection WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('mailboxes.manage'));
GRANT INSERT (id, workspace_id, work_id, consumer, state, lease_owner, lease_generation, lease_expires_at, next_attempt_at, attempts, safe_error, published_at) ON public.outbox_deliveries TO app_connection;

CREATE POLICY domain_events_worker_general_insert ON public.domain_events
FOR INSERT TO app_worker_general WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT INSERT (id, workspace_id, event_type, schema_version, aggregate_type, aggregate_id, aggregate_version, semantic_key, occurred_at, recorded_at, correlation_id, payload) ON public.domain_events TO app_worker_general;

CREATE POLICY outbox_work_worker_general_insert ON public.outbox_work
FOR INSERT TO app_worker_general WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT INSERT (id, workspace_id, kind, schema_version, resource_id, resource_type, event_id, semantic_key, correlation_id, available_at, superseded, superseded_at) ON public.outbox_work TO app_worker_general;

CREATE POLICY outbox_deliveries_worker_general_insert ON public.outbox_deliveries
FOR INSERT TO app_worker_general WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT INSERT (id, workspace_id, work_id, consumer, state, lease_owner, lease_generation, lease_expires_at, next_attempt_at, attempts, safe_error, published_at) ON public.outbox_deliveries TO app_worker_general;

CREATE POLICY consumer_receipts_worker_general_insert ON public.consumer_receipts
FOR INSERT TO app_worker_general WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT INSERT (workspace_id, consumer, work_id, effect_version, completed_at, result_ref) ON public.consumer_receipts TO app_worker_general;

CREATE POLICY domain_events_worker_send_insert ON public.domain_events
FOR INSERT TO app_worker_send WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT INSERT (id, workspace_id, event_type, schema_version, aggregate_type, aggregate_id, aggregate_version, semantic_key, occurred_at, recorded_at, correlation_id, payload) ON public.domain_events TO app_worker_send;

CREATE POLICY outbox_work_worker_send_insert ON public.outbox_work
FOR INSERT TO app_worker_send WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT INSERT (id, workspace_id, kind, schema_version, resource_id, resource_type, event_id, semantic_key, correlation_id, available_at, superseded, superseded_at) ON public.outbox_work TO app_worker_send;

CREATE POLICY outbox_deliveries_worker_send_insert ON public.outbox_deliveries
FOR INSERT TO app_worker_send WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT INSERT (id, workspace_id, work_id, consumer, state, lease_owner, lease_generation, lease_expires_at, next_attempt_at, attempts, safe_error, published_at) ON public.outbox_deliveries TO app_worker_send;

CREATE POLICY consumer_receipts_worker_send_insert ON public.consumer_receipts
FOR INSERT TO app_worker_send WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT INSERT (workspace_id, consumer, work_id, effect_version, completed_at, result_ref) ON public.consumer_receipts TO app_worker_send;

CREATE POLICY domain_events_worker_sync_insert ON public.domain_events
FOR INSERT TO app_worker_sync WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT INSERT (id, workspace_id, event_type, schema_version, aggregate_type, aggregate_id, aggregate_version, semantic_key, occurred_at, recorded_at, correlation_id, payload) ON public.domain_events TO app_worker_sync;

CREATE POLICY outbox_work_worker_sync_insert ON public.outbox_work
FOR INSERT TO app_worker_sync WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT INSERT (id, workspace_id, kind, schema_version, resource_id, resource_type, event_id, semantic_key, correlation_id, available_at, superseded, superseded_at) ON public.outbox_work TO app_worker_sync;

CREATE POLICY outbox_deliveries_worker_sync_insert ON public.outbox_deliveries
FOR INSERT TO app_worker_sync WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT INSERT (id, workspace_id, work_id, consumer, state, lease_owner, lease_generation, lease_expires_at, next_attempt_at, attempts, safe_error, published_at) ON public.outbox_deliveries TO app_worker_sync;

CREATE POLICY consumer_receipts_worker_sync_insert ON public.consumer_receipts
FOR INSERT TO app_worker_sync WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT INSERT (workspace_id, consumer, work_id, effect_version, completed_at, result_ref) ON public.consumer_receipts TO app_worker_sync;

CREATE POLICY domain_events_scheduler_insert ON public.domain_events
FOR INSERT TO app_scheduler WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT INSERT (id, workspace_id, event_type, schema_version, aggregate_type, aggregate_id, aggregate_version, semantic_key, occurred_at, recorded_at, correlation_id, payload) ON public.domain_events TO app_scheduler;

CREATE POLICY outbox_work_scheduler_insert ON public.outbox_work
FOR INSERT TO app_scheduler WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT INSERT (id, workspace_id, kind, schema_version, resource_id, resource_type, event_id, semantic_key, correlation_id, available_at, superseded, superseded_at) ON public.outbox_work TO app_scheduler;

CREATE POLICY outbox_deliveries_scheduler_insert ON public.outbox_deliveries
FOR INSERT TO app_scheduler WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT INSERT (id, workspace_id, work_id, consumer, state, lease_owner, lease_generation, lease_expires_at, next_attempt_at, attempts, safe_error, published_at) ON public.outbox_deliveries TO app_scheduler;

CREATE POLICY consumer_receipts_scheduler_insert ON public.consumer_receipts
FOR INSERT TO app_scheduler WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT INSERT (workspace_id, consumer, work_id, effect_version, completed_at, result_ref) ON public.consumer_receipts TO app_scheduler;

CREATE POLICY outbox_work_outbox_relay_select ON public.outbox_work
FOR SELECT TO app_outbox_relay USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.outbox_work TO app_outbox_relay;

CREATE POLICY outbox_deliveries_outbox_relay_select ON public.outbox_deliveries
FOR SELECT TO app_outbox_relay USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.outbox_deliveries TO app_outbox_relay;

CREATE POLICY consumer_receipts_outbox_relay_select ON public.consumer_receipts
FOR SELECT TO app_outbox_relay USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.consumer_receipts TO app_outbox_relay;

CREATE POLICY outbox_deliveries_outbox_relay_update ON public.outbox_deliveries
FOR UPDATE TO app_outbox_relay USING (workspace_id = (SELECT public.app_current_workspace_id())) WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT UPDATE (state, lease_owner, lease_generation, lease_expires_at, next_attempt_at, attempts, safe_error, published_at) ON public.outbox_deliveries TO app_outbox_relay;

CREATE POLICY outbox_work_worker_general_update ON public.outbox_work
FOR UPDATE TO app_worker_general USING (workspace_id = (SELECT public.app_current_workspace_id())) WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT UPDATE (superseded, superseded_at) ON public.outbox_work TO app_worker_general;

CREATE POLICY messages_integrity_guard_select ON public.messages
FOR SELECT TO app_integrity_guard USING (true);
GRANT SELECT ON public.messages TO app_integrity_guard;

CREATE FUNCTION public.app_guard_reply_attribution() RETURNS trigger
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = ''
AS $function$
DECLARE actual_campaign uuid; actual_enrollment uuid;
BEGIN
    SELECT campaign_id,enrollment_id INTO STRICT actual_campaign,actual_enrollment FROM public.messages
      WHERE workspace_id=NEW.workspace_id AND mailbox_id=NEW.mailbox_id AND id=NEW.outbound_message_id;
    IF (NEW.campaign_id,NEW.enrollment_id) IS DISTINCT FROM (actual_campaign,actual_enrollment) THEN
        RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Reply attribution must match outbound message';
    END IF;
    RETURN NEW;
END;
$function$;
REVOKE ALL ON FUNCTION public.app_guard_reply_attribution() FROM PUBLIC, anon, authenticated, service_role;

-- The migration identity creates triggers after ownership transfer; keep these
-- grants temporary so function execution is not opened to runtime/browser roles.
GRANT EXECUTE ON FUNCTION public.app_guard_reply_attribution() TO CURRENT_USER;
GRANT app_integrity_guard TO CURRENT_USER;
GRANT CREATE ON SCHEMA public TO app_integrity_guard;
ALTER FUNCTION public.app_guard_reply_attribution() OWNER TO app_integrity_guard;
REVOKE CREATE ON SCHEMA public FROM app_integrity_guard;

CREATE TRIGGER inbound_outreach_links_guard_reply_attribution BEFORE INSERT OR UPDATE ON public.inbound_outreach_links FOR EACH ROW EXECUTE FUNCTION public.app_guard_reply_attribution();
REVOKE EXECUTE ON FUNCTION public.app_guard_reply_attribution() FROM CURRENT_USER;
REVOKE app_integrity_guard FROM CURRENT_USER;

CREATE FUNCTION public.app_guard_message_destination() RETURNS trigger
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = ''
AS $function$
DECLARE destination text;
BEGIN
    IF NEW.rendered_at IS NOT NULL THEN
        IF NEW.enrollment_id IS NOT NULL THEN
            SELECT frozen_destination INTO STRICT destination FROM public.campaign_enrollments
              WHERE workspace_id=NEW.workspace_id AND id=NEW.enrollment_id;
        ELSE
            SELECT canonical_address INTO STRICT destination FROM public.recipient_addresses
              WHERE workspace_id=NEW.workspace_id AND id=NEW.address_id;
        END IF;
        IF NEW.frozen_destination IS DISTINCT FROM destination THEN
            RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Rendered destination must match authorized recipient';
        END IF;
    END IF;
    RETURN NEW;
END;
$function$;
REVOKE ALL ON FUNCTION public.app_guard_message_destination() FROM PUBLIC, anon, authenticated, service_role;

GRANT EXECUTE ON FUNCTION public.app_guard_message_destination() TO CURRENT_USER;
GRANT app_integrity_guard TO CURRENT_USER;
GRANT CREATE ON SCHEMA public TO app_integrity_guard;
ALTER FUNCTION public.app_guard_message_destination() OWNER TO app_integrity_guard;
REVOKE CREATE ON SCHEMA public FROM app_integrity_guard;

CREATE TRIGGER messages_guard_message_destination BEFORE INSERT OR UPDATE ON public.messages FOR EACH ROW EXECUTE FUNCTION public.app_guard_message_destination();
REVOKE EXECUTE ON FUNCTION public.app_guard_message_destination() FROM CURRENT_USER;
REVOKE app_integrity_guard FROM CURRENT_USER;

CREATE FUNCTION public.app_guard_attempt_evidence() RETURNS trigger
LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path = ''
AS $function$
BEGIN
    IF OLD.evidence_state IN ('ACCEPTED','REJECTED','NOT_INVOKED') AND NEW IS DISTINCT FROM OLD THEN
        RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Terminal attempt evidence is immutable; append observations separately';
    END IF;
    RETURN NEW;
END;
$function$;
REVOKE ALL ON FUNCTION public.app_guard_attempt_evidence() FROM PUBLIC, anon, authenticated, service_role;

CREATE TRIGGER message_attempts_guard_attempt_evidence BEFORE UPDATE ON public.message_attempts FOR EACH ROW EXECUTE FUNCTION public.app_guard_attempt_evidence();

ALTER TABLE public.messages ADD CONSTRAINT messages_content_digest_shape_check CHECK (content_digest IS NULL OR content_digest ~ '^[0-9a-f]{64}$');

ALTER TABLE public.message_attempts ADD CONSTRAINT message_attempts_acceptance_evidence_check CHECK (evidence_state <> 'ACCEPTED' OR provider_request_id IS NOT NULL OR provider_message_ref IS NOT NULL OR reconciliation_metadata <> '{}'::jsonb);

ALTER TABLE public.message_attempts ADD CONSTRAINT message_attempts_provider_request_id_bound_check CHECK (provider_request_id IS NULL OR pg_catalog.char_length(provider_request_id) BETWEEN 1 AND 1024);

ALTER TABLE public.message_attempts ADD CONSTRAINT message_attempts_provider_message_ref_bound_check CHECK (provider_message_ref IS NULL OR pg_catalog.char_length(provider_message_ref) BETWEEN 1 AND 1024);

ALTER TABLE public.message_attempts ADD CONSTRAINT message_attempts_provider_thread_ref_bound_check CHECK (provider_thread_ref IS NULL OR pg_catalog.char_length(provider_thread_ref) BETWEEN 1 AND 1024);

ALTER TABLE public.message_attempts ADD CONSTRAINT message_attempts_error_code_bound_check CHECK (error_code IS NULL OR pg_catalog.char_length(error_code) BETWEEN 1 AND 1024);

ALTER TABLE public.attempt_evidence ADD CONSTRAINT attempt_evidence_payload_ref_bound_check CHECK (payload_ref IS NULL OR pg_catalog.char_length(payload_ref) BETWEEN 1 AND 1024);

ALTER TABLE public.provider_receipts ADD CONSTRAINT provider_receipts_processing_due_check CHECK (receipt_status <> 'PROCESSING' OR next_due_at IS NOT NULL);

ALTER TABLE public.outbox_work ADD CONSTRAINT outbox_work_resource_pair_check CHECK ((resource_id IS NULL) = (resource_type IS NULL));

CREATE FUNCTION public.app_maintain_safety_counter() RETURNS trigger
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = ''
AS $function$
DECLARE delta integer;
BEGIN
    IF TG_OP='UPDATE' AND ((NEW.workspace_id,NEW.target_kind,NEW.target_mailbox_id,NEW.source_receipt_id,NEW.source_work_identity)
       IS DISTINCT FROM (OLD.workspace_id,OLD.target_kind,OLD.target_mailbox_id,OLD.source_receipt_id,OLD.source_work_identity)
       OR OLD.status='RESOLVED') THEN
        RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Safety hold identity and resolved evidence are immutable';
    END IF;
    delta := CASE WHEN NEW.status='ACTIVE' THEN 1 ELSE 0 END - CASE WHEN TG_OP='UPDATE' AND OLD.status='ACTIVE' THEN 1 ELSE 0 END;
    -- All safety writers and authorization commands take workspace before mailbox.
    PERFORM 1 FROM public.workspaces WHERE id=NEW.workspace_id FOR UPDATE;
    IF NEW.target_kind='WORKSPACE' THEN
        UPDATE public.workspaces SET pending_safety_count=pending_safety_count+delta WHERE id=NEW.workspace_id;
    ELSE
        UPDATE public.mailboxes SET pending_safety_count=pending_safety_count+delta
          WHERE workspace_id=NEW.workspace_id AND id=NEW.target_mailbox_id;
    END IF;
    RETURN NEW;
END;
$function$;
REVOKE ALL ON FUNCTION public.app_maintain_safety_counter() FROM PUBLIC, anon, authenticated, service_role;

GRANT EXECUTE ON FUNCTION public.app_maintain_safety_counter() TO CURRENT_USER;
GRANT app_integrity_guard TO CURRENT_USER;
GRANT CREATE ON SCHEMA public TO app_integrity_guard;
ALTER FUNCTION public.app_maintain_safety_counter() OWNER TO app_integrity_guard;
REVOKE CREATE ON SCHEMA public FROM app_integrity_guard;

CREATE TRIGGER safety_holds_maintain_safety_counter AFTER INSERT OR UPDATE ON public.safety_holds FOR EACH ROW EXECUTE FUNCTION public.app_maintain_safety_counter();
REVOKE EXECUTE ON FUNCTION public.app_maintain_safety_counter() FROM CURRENT_USER;
REVOKE app_integrity_guard FROM CURRENT_USER;

GRANT SELECT ON public.conversations TO app_worker_send;
CREATE POLICY conversations_app_worker_send_additional_read ON public.conversations FOR SELECT TO app_worker_send USING (workspace_id = (SELECT public.app_current_workspace_id()));

GRANT SELECT ON public.attempt_evidence TO app_worker_send;
CREATE POLICY attempt_evidence_app_worker_send_additional_read ON public.attempt_evidence FOR SELECT TO app_worker_send USING (workspace_id = (SELECT public.app_current_workspace_id()));

GRANT SELECT ON public.attempt_evidence TO app_worker_sync;
CREATE POLICY attempt_evidence_app_worker_sync_additional_read ON public.attempt_evidence FOR SELECT TO app_worker_sync USING (workspace_id = (SELECT public.app_current_workspace_id()));

GRANT SELECT ON public.inbound_messages TO app_worker_send;
CREATE POLICY inbound_messages_app_worker_send_additional_read ON public.inbound_messages FOR SELECT TO app_worker_send USING (workspace_id = (SELECT public.app_current_workspace_id()));

GRANT SELECT ON public.outbox_work TO app_worker_send;
CREATE POLICY outbox_work_app_worker_send_additional_read ON public.outbox_work FOR SELECT TO app_worker_send USING (workspace_id = (SELECT public.app_current_workspace_id()));

GRANT SELECT ON public.consumer_receipts TO app_worker_send;
CREATE POLICY consumer_receipts_app_worker_send_additional_read ON public.consumer_receipts FOR SELECT TO app_worker_send USING (workspace_id = (SELECT public.app_current_workspace_id()));

GRANT SELECT ON public.consumer_receipts TO app_worker_sync;
CREATE POLICY consumer_receipts_app_worker_sync_additional_read ON public.consumer_receipts FOR SELECT TO app_worker_sync USING (workspace_id = (SELECT public.app_current_workspace_id()));

GRANT SELECT ON public.consumer_receipts TO app_scheduler;
CREATE POLICY consumer_receipts_app_scheduler_additional_read ON public.consumer_receipts FOR SELECT TO app_scheduler USING (workspace_id = (SELECT public.app_current_workspace_id()));

GRANT SELECT ON public.outbox_work TO app_scheduler;
CREATE POLICY outbox_work_app_scheduler_additional_read ON public.outbox_work FOR SELECT TO app_scheduler USING (workspace_id = (SELECT public.app_current_workspace_id()));

CREATE POLICY messages_api_controlled_insert ON public.messages FOR INSERT TO app_api
WITH CHECK (workspace_id=public.app_current_workspace_id() AND public.app_has_permission('campaigns.execute')
 AND purpose='CONTROLLED_TEST' AND status='PLANNED'
 AND EXISTS (SELECT 1 FROM public.controlled_send_authorizations a WHERE a.workspace_id=messages.workspace_id
   AND a.id=messages.controlled_send_authorization_id AND a.requester_user_id=public.app_current_user_id()
   AND a.revoked_at IS NULL AND a.expires_at > pg_catalog.transaction_timestamp()));
GRANT INSERT (id,workspace_id,purpose,controlled_send_authorization_id,mailbox_id,address_id,rfc_message_id,
 content_subject,content_body_html,content_digest,renderer_version,frozen_destination,frozen_sender_address,
 frozen_sender_name,rendered_at,status,due_at,anchor_at) ON public.messages TO app_api;

ALTER TABLE public.messages ADD CONSTRAINT messages_rendered_version_check CHECK (rendered_at IS NULL OR renderer_version IS NOT NULL);

CREATE FUNCTION public.app_touch_timestamp() RETURNS trigger
LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path = ''
AS $function$
BEGIN
    IF (NEW.id,NEW.created_at) IS DISTINCT FROM (OLD.id,OLD.created_at) THEN
        RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Record identity is immutable';
    END IF;
    NEW.updated_at := pg_catalog.transaction_timestamp();
    RETURN NEW;
END;
$function$;
REVOKE ALL ON FUNCTION public.app_touch_timestamp() FROM PUBLIC, anon, authenticated, service_role;

CREATE TRIGGER message_attempts_touch_timestamp BEFORE UPDATE ON public.message_attempts FOR EACH ROW EXECUTE FUNCTION public.app_touch_timestamp();

CREATE TRIGGER outbox_deliveries_touch_timestamp BEFORE UPDATE ON public.outbox_deliveries FOR EACH ROW EXECUTE FUNCTION public.app_touch_timestamp();

CREATE INDEX provider_receipts_lease_recovery_idx ON public.provider_receipts (workspace_id, lease_expires_at, id) WHERE lease_owner IS NOT NULL;

CREATE INDEX outbox_deliveries_lease_recovery_idx ON public.outbox_deliveries (workspace_id, lease_expires_at, id) WHERE state = 'LEASED';

CREATE INDEX outbox_deliveries_published_recovery_idx ON public.outbox_deliveries (workspace_id, published_at, work_id) WHERE state = 'PUBLISHED';

COMMIT;
