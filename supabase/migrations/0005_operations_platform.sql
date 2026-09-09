-- Operations/capacity draft corrected with the full five-file chain.
-- Completes notification and MANUAL-release audit references. Global configured
-- limits and operator commands remain separately controlled; only the dedicated
-- rate controller owns recovery readiness. See docs/database/MIGRATION_REVIEW.md.
-- Prepared only: no migration execution was performed.

BEGIN;
SET LOCAL search_path = '';

-- ---------------------------------------------------------------------------
-- Audit
-- ---------------------------------------------------------------------------

CREATE TABLE public.audit_events (
    effect_key text,
    id uuid DEFAULT pg_catalog.gen_random_uuid() CONSTRAINT audit_events_pkey PRIMARY KEY,
    workspace_id uuid NOT NULL,
    actor_kind text NOT NULL,
    actor_id uuid,
    action text NOT NULL,
    target_type text NOT NULL,
    target_id uuid,
    before_state jsonb,
    after_state jsonb,
    reason text,
    request_id uuid,
    recorded_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    CONSTRAINT audit_events_workspace_fkey FOREIGN KEY (workspace_id)
        REFERENCES public.workspaces (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT audit_events_actor_fkey FOREIGN KEY (actor_id)
        REFERENCES public.profiles (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT audit_events_actor_kind_check CHECK (actor_kind IN ('USER', 'SYSTEM')),
    CONSTRAINT audit_events_action_check CHECK (pg_catalog.char_length(action) BETWEEN 1 AND 200),
    CONSTRAINT audit_events_target_type_check CHECK (pg_catalog.char_length(target_type) BETWEEN 1 AND 100),
    CONSTRAINT audit_events_before_state_check CHECK (
        before_state IS NULL
        OR (pg_catalog.jsonb_typeof(before_state) = 'object'
            AND pg_catalog.octet_length(pg_catalog.convert_to(before_state::text, 'UTF8')) <= 8192)
    ),
    CONSTRAINT audit_events_after_state_check CHECK (
        after_state IS NULL
        OR (pg_catalog.jsonb_typeof(after_state) = 'object'
            AND pg_catalog.octet_length(pg_catalog.convert_to(after_state::text, 'UTF8')) <= 8192)
    ),
    CONSTRAINT audit_events_reason_check CHECK (reason IS NULL OR pg_catalog.char_length(reason) <= 1000),
    CONSTRAINT audit_events_workspace_id_key UNIQUE (workspace_id, id),
    CONSTRAINT audit_events_actor_shape_check CHECK ((actor_kind = 'USER') = (actor_id IS NOT NULL)),
    CONSTRAINT audit_events_effect_key_check CHECK ((request_id IS NULL) = (effect_key IS NULL)
        AND (effect_key IS NULL OR pg_catalog.char_length(effect_key) BETWEEN 1 AND 200))
);

CREATE UNIQUE INDEX audit_events_request_key
    ON public.audit_events (workspace_id, request_id, effect_key)
    WHERE request_id IS NOT NULL;
CREATE INDEX audit_events_target_idx ON public.audit_events (workspace_id, target_type, target_id, recorded_at);
CREATE INDEX audit_events_recorded_idx ON public.audit_events (workspace_id, recorded_at);

-- Separate from audit_events (not a nullable-workspace union) so platform
-- operator evidence can never be reached through a tenant-scoped policy.
CREATE TABLE public.platform_audit_events (
    id uuid DEFAULT pg_catalog.gen_random_uuid() CONSTRAINT platform_audit_events_pkey PRIMARY KEY,
    actor_kind text NOT NULL,
    actor_id uuid,
    action text NOT NULL,
    target_type text NOT NULL,
    target_id uuid,
    before_state jsonb,
    after_state jsonb,
    reason text,
    request_id uuid,
    recorded_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    CONSTRAINT platform_audit_events_actor_fkey FOREIGN KEY (actor_id)
        REFERENCES public.profiles (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT platform_audit_events_actor_kind_check CHECK (actor_kind IN ('OPERATOR', 'SYSTEM')),
    CONSTRAINT platform_audit_events_action_check CHECK (pg_catalog.char_length(action) BETWEEN 1 AND 200),
    CONSTRAINT platform_audit_events_target_type_check CHECK (pg_catalog.char_length(target_type) BETWEEN 1 AND 100),
    CONSTRAINT platform_audit_events_before_state_check CHECK (
        before_state IS NULL
        OR (pg_catalog.jsonb_typeof(before_state) = 'object'
            AND pg_catalog.octet_length(pg_catalog.convert_to(before_state::text, 'UTF8')) <= 8192)
    ),
    CONSTRAINT platform_audit_events_after_state_check CHECK (
        after_state IS NULL
        OR (pg_catalog.jsonb_typeof(after_state) = 'object'
            AND pg_catalog.octet_length(pg_catalog.convert_to(after_state::text, 'UTF8')) <= 8192)
    ),
    CONSTRAINT platform_audit_events_reason_check CHECK (reason IS NULL OR pg_catalog.char_length(reason) <= 1000)
);

CREATE INDEX platform_audit_events_actor_idx ON public.platform_audit_events (actor_id, recorded_at);

-- ---------------------------------------------------------------------------
-- Notifications
-- ---------------------------------------------------------------------------

CREATE TABLE public.notifications (
    id uuid DEFAULT pg_catalog.gen_random_uuid() CONSTRAINT notifications_pkey PRIMARY KEY,
    workspace_id uuid NOT NULL,
    recipient_user_id uuid NOT NULL,
    recipient_membership_id uuid NOT NULL,
    source_event_id uuid NOT NULL,
    type text NOT NULL,
    resource_link text,
    summary text,
    read_at timestamptz,
    version bigint NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    CONSTRAINT notifications_workspace_fkey FOREIGN KEY (workspace_id)
        REFERENCES public.workspaces (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT notifications_recipient_fkey FOREIGN KEY (recipient_user_id)
        REFERENCES public.profiles (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT notifications_membership_fkey FOREIGN KEY (workspace_id, recipient_membership_id)
        REFERENCES public.workspace_memberships (workspace_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT notifications_event_fkey FOREIGN KEY (workspace_id, source_event_id)
        REFERENCES public.domain_events (workspace_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT notifications_identity_key UNIQUE (workspace_id, recipient_user_id, source_event_id, type),
    CONSTRAINT notifications_type_check CHECK (pg_catalog.char_length(type) BETWEEN 1 AND 100),
    CONSTRAINT notifications_resource_link_check CHECK (resource_link IS NULL OR pg_catalog.char_length(resource_link) <= 1024),
    CONSTRAINT notifications_summary_check CHECK (summary IS NULL OR pg_catalog.char_length(summary) <= 1000),
    CONSTRAINT notifications_version_check CHECK (version > 0),
    CONSTRAINT notifications_workspace_id_key UNIQUE (workspace_id, id),
    CONSTRAINT notifications_recipient_membership_fkey FOREIGN KEY (workspace_id, recipient_user_id, recipient_membership_id)
    REFERENCES public.workspace_memberships (workspace_id, user_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT
);

CREATE INDEX notifications_center_idx ON public.notifications (workspace_id, recipient_user_id, created_at, id);

CREATE TABLE public.notification_deliveries (
    lease_owner text,
    lease_generation bigint NOT NULL DEFAULT 1,
    lease_expires_at timestamptz,
    id uuid DEFAULT pg_catalog.gen_random_uuid() CONSTRAINT notification_deliveries_pkey PRIMARY KEY,
    workspace_id uuid NOT NULL,
    notification_id uuid NOT NULL,
    channel text NOT NULL,
    attempt_ordinal integer NOT NULL,
    status text NOT NULL DEFAULT 'PENDING',
    due_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    provider_identifier text,
    safe_error text,
    created_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    CONSTRAINT notification_deliveries_workspace_fkey FOREIGN KEY (workspace_id)
        REFERENCES public.workspaces (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT notification_deliveries_notification_fkey FOREIGN KEY (workspace_id, notification_id)
        REFERENCES public.notifications (workspace_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT notification_deliveries_identity_key UNIQUE (workspace_id, notification_id, channel, attempt_ordinal),
    CONSTRAINT notification_deliveries_channel_check CHECK (channel IN ('IN_APP', 'EMAIL')),
    CONSTRAINT notification_deliveries_attempt_ordinal_check CHECK (attempt_ordinal > 0),
    CONSTRAINT notification_deliveries_status_check CHECK (status IN ('PENDING', 'SENDING', 'UNKNOWN', 'SENT', 'FAILED')),
    CONSTRAINT notification_deliveries_lease_check CHECK (
    lease_generation > 0 AND (lease_owner IS NULL) = (lease_expires_at IS NULL)
    AND (status <> 'SENDING' OR lease_owner IS NOT NULL))
);

CREATE INDEX notification_deliveries_due_idx
    ON public.notification_deliveries (due_at, id)
    WHERE status = 'PENDING';

-- ---------------------------------------------------------------------------
-- Rate-control persistence
-- ---------------------------------------------------------------------------

CREATE TABLE public.rate_control (
    id boolean NOT NULL DEFAULT true CONSTRAINT rate_control_pkey PRIMARY KEY,
    generation bigint NOT NULL DEFAULT 1,
    status text NOT NULL DEFAULT 'RECOVERING',
    recovery_watermark timestamptz,
    recovery_started_at timestamptz DEFAULT pg_catalog.transaction_timestamp(),
    policy_version bigint NOT NULL DEFAULT 1,
    version bigint NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    CONSTRAINT rate_control_singleton_check CHECK (id),
    CONSTRAINT rate_control_generation_check CHECK (generation > 0),
    CONSTRAINT rate_control_status_check CHECK (status IN ('READY', 'RECOVERING')),
    CONSTRAINT rate_control_recovery_check CHECK ((status = 'RECOVERING') = (recovery_started_at IS NOT NULL)),
    CONSTRAINT rate_control_policy_version_check CHECK (policy_version > 0),
    CONSTRAINT rate_control_version_check CHECK (version > 0)
);

CREATE TABLE public.rate_scopes (
    id uuid DEFAULT pg_catalog.gen_random_uuid() CONSTRAINT rate_scopes_pkey PRIMARY KEY,
    kind text NOT NULL,
    external_scope_key text NOT NULL,
    unit text NOT NULL,
    window_seconds integer NOT NULL,
    limit_value integer NOT NULL,
    cooldown_seconds integer NOT NULL DEFAULT 0,
    min_spacing_seconds integer NOT NULL DEFAULT 0,
    last_authorized_at timestamptz,
    version bigint NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    CONSTRAINT rate_scopes_identity_key UNIQUE (kind, external_scope_key, unit, window_seconds),
    CONSTRAINT rate_scopes_kind_check CHECK (kind IN ('PLATFORM', 'PROVIDER', 'PROVIDER_ACCOUNT')),
    CONSTRAINT rate_scopes_unit_check CHECK (pg_catalog.char_length(unit) BETWEEN 1 AND 100),
    CONSTRAINT rate_scopes_window_seconds_check CHECK (window_seconds > 0),
    CONSTRAINT rate_scopes_limit_value_check CHECK (limit_value > 0),
    CONSTRAINT rate_scopes_cooldown_seconds_check CHECK (cooldown_seconds >= 0),
    CONSTRAINT rate_scopes_min_spacing_seconds_check CHECK (min_spacing_seconds >= 0),
    CONSTRAINT rate_scopes_version_check CHECK (version > 0)
);

CREATE TABLE public.tenant_rate_policies (
    window_kind text NOT NULL DEFAULT 'ROLLING',
    min_spacing_seconds integer NOT NULL DEFAULT 0,
    last_authorized_at timestamptz,
    id uuid DEFAULT pg_catalog.gen_random_uuid() CONSTRAINT tenant_rate_policies_pkey PRIMARY KEY,
    workspace_id uuid NOT NULL,
    campaign_id uuid,
    mailbox_id uuid,
    kind text NOT NULL,
    unit text NOT NULL,
    window_seconds integer NOT NULL,
    limit_value integer NOT NULL,
    timezone text NOT NULL DEFAULT 'UTC',
    cooldown_seconds integer NOT NULL DEFAULT 0,
    version bigint NOT NULL DEFAULT 1,
    created_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    CONSTRAINT tenant_rate_policies_workspace_fkey FOREIGN KEY (workspace_id)
        REFERENCES public.workspaces (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT tenant_rate_policies_campaign_fkey FOREIGN KEY (workspace_id, campaign_id)
        REFERENCES public.campaigns (workspace_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT tenant_rate_policies_mailbox_fkey FOREIGN KEY (workspace_id, mailbox_id)
        REFERENCES public.mailboxes (workspace_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT tenant_rate_policies_workspace_id_key UNIQUE (workspace_id, id),
    CONSTRAINT tenant_rate_policies_kind_check CHECK (kind IN ('WORKSPACE', 'CAMPAIGN', 'MAILBOX')),
    CONSTRAINT tenant_rate_policies_shape_check CHECK (
        (kind = 'WORKSPACE' AND campaign_id IS NULL AND mailbox_id IS NULL)
        OR (kind = 'CAMPAIGN' AND campaign_id IS NOT NULL AND mailbox_id IS NULL)
        OR (kind = 'MAILBOX' AND mailbox_id IS NOT NULL AND campaign_id IS NULL)
    ),
    CONSTRAINT tenant_rate_policies_unit_check CHECK (pg_catalog.char_length(unit) BETWEEN 1 AND 100),
    CONSTRAINT tenant_rate_policies_window_seconds_check CHECK (window_seconds > 0),
    CONSTRAINT tenant_rate_policies_limit_value_check CHECK (limit_value > 0),
    CONSTRAINT tenant_rate_policies_timezone_check CHECK (pg_catalog.char_length(timezone) BETWEEN 1 AND 100),
    CONSTRAINT tenant_rate_policies_cooldown_seconds_check CHECK (cooldown_seconds >= 0),
    CONSTRAINT tenant_rate_policies_version_check CHECK (version > 0),
    CONSTRAINT tenant_rate_policies_window_kind_check CHECK (
    window_kind IN ('ROLLING','CALENDAR_DAY') AND (window_kind <> 'CALENDAR_DAY' OR window_seconds = 86400)),
    CONSTRAINT tenant_rate_policies_spacing_check CHECK (min_spacing_seconds >= 0)
);

CREATE UNIQUE INDEX tenant_rate_policies_workspace_scope_key
    ON public.tenant_rate_policies (workspace_id, unit, window_seconds, window_kind)
    WHERE kind = 'WORKSPACE';
CREATE UNIQUE INDEX tenant_rate_policies_campaign_scope_key
    ON public.tenant_rate_policies (workspace_id, campaign_id, unit, window_seconds, window_kind)
    WHERE kind = 'CAMPAIGN';
CREATE UNIQUE INDEX tenant_rate_policies_mailbox_scope_key
    ON public.tenant_rate_policies (workspace_id, mailbox_id, unit, window_seconds, window_kind)
    WHERE kind = 'MAILBOX';

CREATE TABLE public.capacity_debits (
    id uuid DEFAULT pg_catalog.gen_random_uuid() CONSTRAINT capacity_debits_pkey PRIMARY KEY,
    workspace_id uuid NOT NULL,
    attempt_id uuid NOT NULL,
    reservation_id text NOT NULL,
    unit text NOT NULL,
    quantity integer NOT NULL,
    authorized_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    settlement_status text NOT NULL DEFAULT 'CONSUMED',
    provider_counting_evidence jsonb NOT NULL DEFAULT '{}'::jsonb,
    created_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    updated_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    CONSTRAINT capacity_debits_workspace_fkey FOREIGN KEY (workspace_id)
        REFERENCES public.workspaces (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT capacity_debits_attempt_fkey FOREIGN KEY (workspace_id, attempt_id)
        REFERENCES public.message_attempts (workspace_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT capacity_debits_workspace_id_key UNIQUE (workspace_id, id),
    CONSTRAINT capacity_debits_identity_key UNIQUE (workspace_id, attempt_id, reservation_id, unit),
    CONSTRAINT capacity_debits_reservation_id_check CHECK (pg_catalog.char_length(reservation_id) BETWEEN 1 AND 200),
    CONSTRAINT capacity_debits_unit_check CHECK (pg_catalog.char_length(unit) BETWEEN 1 AND 100),
    CONSTRAINT capacity_debits_quantity_check CHECK (quantity > 0),
    CONSTRAINT capacity_debits_settlement_status_check CHECK (settlement_status IN ('CONSUMED', 'RELEASED')),
    CONSTRAINT capacity_debits_evidence_check CHECK (
        pg_catalog.jsonb_typeof(provider_counting_evidence) = 'object'
        AND pg_catalog.octet_length(pg_catalog.convert_to(provider_counting_evidence::text, 'UTF8')) <= 4096
    )
);

CREATE INDEX capacity_debits_authorized_idx ON public.capacity_debits (workspace_id, authorized_at);


CREATE TABLE public.capacity_debit_scopes (
    policy_snapshot jsonb NOT NULL,
    window_kind text NOT NULL DEFAULT 'ROLLING',
    bucket_start_at timestamptz,
    bucket_end_at timestamptz,
    id uuid DEFAULT pg_catalog.gen_random_uuid() CONSTRAINT capacity_debit_scopes_pkey PRIMARY KEY,
    workspace_id uuid NOT NULL,
    debit_id uuid NOT NULL,
    rate_scope_id uuid,
    tenant_rate_policy_id uuid,
    unit text NOT NULL,
    quantity integer NOT NULL,
    policy_version bigint NOT NULL,
    authorized_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    created_at timestamptz NOT NULL DEFAULT pg_catalog.transaction_timestamp(),
    CONSTRAINT capacity_debit_scopes_workspace_fkey FOREIGN KEY (workspace_id)
        REFERENCES public.workspaces (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT capacity_debit_scopes_debit_fkey FOREIGN KEY (workspace_id, debit_id)
        REFERENCES public.capacity_debits (workspace_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT capacity_debit_scopes_rate_scope_fkey FOREIGN KEY (rate_scope_id)
        REFERENCES public.rate_scopes (id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT capacity_debit_scopes_tenant_policy_fkey FOREIGN KEY (workspace_id, tenant_rate_policy_id)
        REFERENCES public.tenant_rate_policies (workspace_id, id) ON UPDATE RESTRICT ON DELETE RESTRICT,
    CONSTRAINT capacity_debit_scopes_exclusive_scope_check CHECK (
        pg_catalog.num_nonnulls(rate_scope_id, tenant_rate_policy_id) = 1
    ),
    CONSTRAINT capacity_debit_scopes_unit_check CHECK (pg_catalog.char_length(unit) BETWEEN 1 AND 100),
    CONSTRAINT capacity_debit_scopes_quantity_check CHECK (quantity > 0),
    CONSTRAINT capacity_debit_scopes_policy_version_check CHECK (policy_version > 0),
    CONSTRAINT capacity_debit_scopes_policy_snapshot_check CHECK (
    pg_catalog.jsonb_typeof(policy_snapshot) = 'object' AND policy_snapshot <> '{}'::jsonb
    AND pg_catalog.octet_length(pg_catalog.convert_to(policy_snapshot::text, 'UTF8')) <= 4096),
    CONSTRAINT capacity_debit_scopes_bucket_check CHECK (
        (window_kind = 'ROLLING' AND bucket_start_at IS NULL AND bucket_end_at IS NULL)
        OR (window_kind = 'CALENDAR_DAY' AND tenant_rate_policy_id IS NOT NULL AND bucket_start_at IS NOT NULL AND bucket_end_at IS NOT NULL
            AND bucket_start_at <= authorized_at AND authorized_at < bucket_end_at))
);

CREATE UNIQUE INDEX capacity_debit_scopes_internal_key
    ON public.capacity_debit_scopes (workspace_id, debit_id, rate_scope_id, unit)
    WHERE rate_scope_id IS NOT NULL;
CREATE UNIQUE INDEX capacity_debit_scopes_tenant_key
    ON public.capacity_debit_scopes (workspace_id, debit_id, tenant_rate_policy_id, unit)
    WHERE tenant_rate_policy_id IS NOT NULL;
CREATE INDEX capacity_debit_scopes_internal_rolling_idx
    ON public.capacity_debit_scopes (rate_scope_id, authorized_at)
    WHERE rate_scope_id IS NOT NULL;
CREATE INDEX capacity_debit_scopes_tenant_rolling_idx
    ON public.capacity_debit_scopes (workspace_id, tenant_rate_policy_id, authorized_at)
    WHERE tenant_rate_policy_id IS NOT NULL;

-- ---------------------------------------------------------------------------
-- Row level security and runtime capabilities

ALTER TABLE public.audit_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.audit_events FORCE ROW LEVEL SECURITY;
ALTER TABLE public.platform_audit_events ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.platform_audit_events FORCE ROW LEVEL SECURITY;
ALTER TABLE public.notifications ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.notifications FORCE ROW LEVEL SECURITY;
ALTER TABLE public.notification_deliveries ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.notification_deliveries FORCE ROW LEVEL SECURITY;
ALTER TABLE public.rate_control ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.rate_control FORCE ROW LEVEL SECURITY;
ALTER TABLE public.rate_scopes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.rate_scopes FORCE ROW LEVEL SECURITY;
ALTER TABLE public.tenant_rate_policies ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.tenant_rate_policies FORCE ROW LEVEL SECURITY;
ALTER TABLE public.capacity_debits ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.capacity_debits FORCE ROW LEVEL SECURITY;
ALTER TABLE public.capacity_debit_scopes ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.capacity_debit_scopes FORCE ROW LEVEL SECURITY;

REVOKE ALL PRIVILEGES ON TABLE
    public.audit_events, public.platform_audit_events, public.notifications, public.notification_deliveries,
    public.rate_control, public.rate_scopes, public.tenant_rate_policies,
    public.capacity_debits, public.capacity_debit_scopes
    FROM PUBLIC, anon, authenticated, service_role;



CREATE TRIGGER notifications_touch_row
    BEFORE UPDATE ON public.notifications FOR EACH ROW EXECUTE FUNCTION public.app_touch_row();
CREATE TRIGGER rate_control_touch_row
    BEFORE UPDATE ON public.rate_control FOR EACH ROW EXECUTE FUNCTION public.app_touch_row();
CREATE TRIGGER rate_scopes_touch_row
    BEFORE UPDATE ON public.rate_scopes FOR EACH ROW EXECUTE FUNCTION public.app_touch_row();
CREATE TRIGGER tenant_rate_policies_touch_row
    BEFORE UPDATE ON public.tenant_rate_policies FOR EACH ROW EXECUTE FUNCTION public.app_touch_row();

ALTER TABLE public.suppressions ADD CONSTRAINT suppressions_release_audit_fkey
FOREIGN KEY (workspace_id, release_audit_id) REFERENCES public.audit_events (workspace_id, id)
ON UPDATE RESTRICT ON DELETE RESTRICT;

CREATE POLICY tenant_rate_policies_api_select ON public.tenant_rate_policies
FOR SELECT TO app_api USING (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('product.read'));
GRANT SELECT ON public.tenant_rate_policies TO app_api;

CREATE POLICY tenant_rate_policies_api_insert ON public.tenant_rate_policies
FOR INSERT TO app_api WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('workspace.manage'));
GRANT INSERT (window_kind, min_spacing_seconds, id, workspace_id, campaign_id, mailbox_id, kind, unit, window_seconds, limit_value, timezone, cooldown_seconds) ON public.tenant_rate_policies TO app_api;

CREATE POLICY tenant_rate_policies_api_update ON public.tenant_rate_policies
FOR UPDATE TO app_api USING (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('workspace.manage')) WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('workspace.manage'));
GRANT UPDATE (limit_value, cooldown_seconds, timezone, min_spacing_seconds) ON public.tenant_rate_policies TO app_api;

CREATE POLICY notifications_worker_general_select ON public.notifications
FOR SELECT TO app_worker_general USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.notifications TO app_worker_general;

CREATE POLICY notification_deliveries_worker_general_select ON public.notification_deliveries
FOR SELECT TO app_worker_general USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.notification_deliveries TO app_worker_general;

CREATE POLICY tenant_rate_policies_worker_general_select ON public.tenant_rate_policies
FOR SELECT TO app_worker_general USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.tenant_rate_policies TO app_worker_general;

CREATE POLICY notifications_worker_general_insert ON public.notifications
FOR INSERT TO app_worker_general WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT INSERT (id, workspace_id, recipient_user_id, recipient_membership_id, source_event_id, type, resource_link, summary, read_at) ON public.notifications TO app_worker_general;

CREATE POLICY notification_deliveries_worker_general_insert ON public.notification_deliveries
FOR INSERT TO app_worker_general WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT INSERT (lease_owner, lease_generation, lease_expires_at, id, workspace_id, notification_id, channel, attempt_ordinal, status, due_at, provider_identifier, safe_error) ON public.notification_deliveries TO app_worker_general;

CREATE POLICY notification_deliveries_worker_general_update ON public.notification_deliveries
FOR UPDATE TO app_worker_general USING (workspace_id = (SELECT public.app_current_workspace_id())) WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT UPDATE (status, due_at, lease_owner, lease_generation, lease_expires_at, provider_identifier, safe_error) ON public.notification_deliveries TO app_worker_general;

CREATE POLICY tenant_rate_policies_worker_send_select ON public.tenant_rate_policies
FOR SELECT TO app_worker_send USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.tenant_rate_policies TO app_worker_send;

CREATE POLICY rate_control_worker_send_select ON public.rate_control
FOR SELECT TO app_worker_send USING (true);
GRANT SELECT ON public.rate_control TO app_worker_send;

CREATE POLICY rate_scopes_worker_send_select ON public.rate_scopes
FOR SELECT TO app_worker_send USING (true);
GRANT SELECT ON public.rate_scopes TO app_worker_send;

CREATE POLICY tenant_rate_policies_worker_sync_select ON public.tenant_rate_policies
FOR SELECT TO app_worker_sync USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.tenant_rate_policies TO app_worker_sync;

CREATE POLICY rate_control_worker_sync_select ON public.rate_control
FOR SELECT TO app_worker_sync USING (true);
GRANT SELECT ON public.rate_control TO app_worker_sync;

CREATE POLICY rate_scopes_worker_sync_select ON public.rate_scopes
FOR SELECT TO app_worker_sync USING (true);
GRANT SELECT ON public.rate_scopes TO app_worker_sync;

CREATE POLICY tenant_rate_policies_scheduler_select ON public.tenant_rate_policies
FOR SELECT TO app_scheduler USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.tenant_rate_policies TO app_scheduler;

CREATE POLICY rate_control_scheduler_select ON public.rate_control
FOR SELECT TO app_scheduler USING (true);
GRANT SELECT ON public.rate_control TO app_scheduler;

CREATE POLICY rate_scopes_scheduler_select ON public.rate_scopes
FOR SELECT TO app_scheduler USING (true);
GRANT SELECT ON public.rate_scopes TO app_scheduler;

CREATE POLICY audit_events_api_insert ON public.audit_events
FOR INSERT TO app_api WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('product.read') AND actor_kind = 'USER' AND actor_id = public.app_current_user_id());
GRANT INSERT (effect_key, id, workspace_id, actor_kind, actor_id, action, target_type, target_id, before_state, after_state, reason, request_id, recorded_at) ON public.audit_events TO app_api;

CREATE POLICY audit_events_connection_insert ON public.audit_events
FOR INSERT TO app_connection WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('mailboxes.manage') AND actor_kind = 'USER' AND actor_id = public.app_current_user_id());
GRANT INSERT (effect_key, id, workspace_id, actor_kind, actor_id, action, target_type, target_id, before_state, after_state, reason, request_id, recorded_at) ON public.audit_events TO app_connection;

CREATE POLICY audit_events_worker_general_insert ON public.audit_events
FOR INSERT TO app_worker_general WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()) AND actor_kind = 'SYSTEM' AND actor_id IS NULL);
GRANT INSERT (effect_key, id, workspace_id, actor_kind, actor_id, action, target_type, target_id, before_state, after_state, reason, request_id, recorded_at) ON public.audit_events TO app_worker_general;

CREATE POLICY audit_events_worker_send_insert ON public.audit_events
FOR INSERT TO app_worker_send WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()) AND actor_kind = 'SYSTEM' AND actor_id IS NULL);
GRANT INSERT (effect_key, id, workspace_id, actor_kind, actor_id, action, target_type, target_id, before_state, after_state, reason, request_id, recorded_at) ON public.audit_events TO app_worker_send;

CREATE POLICY audit_events_worker_sync_insert ON public.audit_events
FOR INSERT TO app_worker_sync WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()) AND actor_kind = 'SYSTEM' AND actor_id IS NULL);
GRANT INSERT (effect_key, id, workspace_id, actor_kind, actor_id, action, target_type, target_id, before_state, after_state, reason, request_id, recorded_at) ON public.audit_events TO app_worker_sync;

CREATE POLICY audit_events_scheduler_insert ON public.audit_events
FOR INSERT TO app_scheduler WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()) AND actor_kind = 'SYSTEM' AND actor_id IS NULL);
GRANT INSERT (effect_key, id, workspace_id, actor_kind, actor_id, action, target_type, target_id, before_state, after_state, reason, request_id, recorded_at) ON public.audit_events TO app_scheduler;

CREATE POLICY audit_events_api_select ON public.audit_events
FOR SELECT TO app_api USING (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('audit.read'));
GRANT SELECT ON public.audit_events TO app_api;

CREATE POLICY notifications_api_select ON public.notifications
FOR SELECT TO app_api USING (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('product.read') AND recipient_user_id = public.app_current_user_id());
GRANT SELECT ON public.notifications TO app_api;

CREATE POLICY notifications_api_update ON public.notifications
FOR UPDATE TO app_api USING (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('product.read') AND recipient_user_id = public.app_current_user_id()) WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()) AND public.app_has_permission('product.read') AND recipient_user_id = public.app_current_user_id());
GRANT UPDATE (read_at) ON public.notifications TO app_api;

CREATE POLICY capacity_debits_worker_send_select ON public.capacity_debits
FOR SELECT TO app_worker_send USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.capacity_debits TO app_worker_send;

CREATE POLICY capacity_debit_scopes_worker_send_select ON public.capacity_debit_scopes
FOR SELECT TO app_worker_send USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.capacity_debit_scopes TO app_worker_send;

CREATE POLICY capacity_debits_worker_sync_select ON public.capacity_debits
FOR SELECT TO app_worker_sync USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.capacity_debits TO app_worker_sync;

CREATE POLICY capacity_debit_scopes_worker_sync_select ON public.capacity_debit_scopes
FOR SELECT TO app_worker_sync USING (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT SELECT ON public.capacity_debit_scopes TO app_worker_sync;

CREATE POLICY capacity_debits_worker_send_insert ON public.capacity_debits
FOR INSERT TO app_worker_send WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT INSERT (id, workspace_id, attempt_id, reservation_id, unit, quantity, authorized_at, settlement_status, provider_counting_evidence) ON public.capacity_debits TO app_worker_send;

CREATE POLICY capacity_debit_scopes_worker_send_insert ON public.capacity_debit_scopes
FOR INSERT TO app_worker_send WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT INSERT (policy_snapshot, window_kind, bucket_start_at, bucket_end_at, id, workspace_id, debit_id, rate_scope_id, tenant_rate_policy_id, unit, quantity, policy_version, authorized_at) ON public.capacity_debit_scopes TO app_worker_send;

CREATE POLICY capacity_debits_worker_send_update ON public.capacity_debits
FOR UPDATE TO app_worker_send USING (workspace_id = (SELECT public.app_current_workspace_id())) WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT UPDATE (settlement_status, provider_counting_evidence) ON public.capacity_debits TO app_worker_send;

CREATE POLICY tenant_rate_policies_worker_send_update ON public.tenant_rate_policies
FOR UPDATE TO app_worker_send USING (workspace_id = (SELECT public.app_current_workspace_id())) WITH CHECK (workspace_id = (SELECT public.app_current_workspace_id()));
GRANT UPDATE (last_authorized_at) ON public.tenant_rate_policies TO app_worker_send;

CREATE POLICY rate_scopes_worker_send_update ON public.rate_scopes
FOR UPDATE TO app_worker_send USING (true) WITH CHECK (true);
GRANT UPDATE (last_authorized_at) ON public.rate_scopes TO app_worker_send;

CREATE POLICY rate_control_rate_controller_select ON public.rate_control
FOR SELECT TO app_rate_controller USING (true);
GRANT SELECT ON public.rate_control TO app_rate_controller;

CREATE POLICY rate_scopes_rate_controller_select ON public.rate_scopes
FOR SELECT TO app_rate_controller USING (true);
GRANT SELECT ON public.rate_scopes TO app_rate_controller;

CREATE POLICY capacity_debits_rate_controller_select ON public.capacity_debits
FOR SELECT TO app_rate_controller USING (true);
GRANT SELECT ON public.capacity_debits TO app_rate_controller;

CREATE POLICY capacity_debit_scopes_rate_controller_select ON public.capacity_debit_scopes
FOR SELECT TO app_rate_controller USING (true);
GRANT SELECT ON public.capacity_debit_scopes TO app_rate_controller;

CREATE POLICY tenant_rate_policies_rate_controller_select ON public.tenant_rate_policies
FOR SELECT TO app_rate_controller USING (true);
GRANT SELECT ON public.tenant_rate_policies TO app_rate_controller;

CREATE POLICY rate_control_rate_controller_insert ON public.rate_control
FOR INSERT TO app_rate_controller WITH CHECK (true);
GRANT INSERT (id, generation, status, recovery_watermark, recovery_started_at, policy_version) ON public.rate_control TO app_rate_controller;

CREATE POLICY rate_control_rate_controller_update ON public.rate_control
FOR UPDATE TO app_rate_controller USING (true) WITH CHECK (true);
GRANT UPDATE (generation, status, recovery_watermark, recovery_started_at, policy_version) ON public.rate_control TO app_rate_controller;

CREATE POLICY rate_scopes_rate_controller_update ON public.rate_scopes
FOR UPDATE TO app_rate_controller USING (true) WITH CHECK (true);
GRANT UPDATE (last_authorized_at) ON public.rate_scopes TO app_rate_controller;

CREATE POLICY audit_events_integrity_guard_select ON public.audit_events
FOR SELECT TO app_integrity_guard USING (true);
GRANT SELECT ON public.audit_events TO app_integrity_guard;

CREATE FUNCTION public.app_guard_suppression_release() RETURNS trigger
LANGUAGE plpgsql VOLATILE SECURITY DEFINER SET search_path = ''
AS $function$
DECLARE role_name text;
BEGIN
    IF (NEW.workspace_id,NEW.address_id,NEW.reason,NEW.first_observed_at) IS DISTINCT FROM
       (OLD.workspace_id,OLD.address_id,OLD.reason,OLD.first_observed_at) OR NEW.last_observed_at < OLD.last_observed_at THEN
        RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Suppression identity and observation history cannot regress';
    END IF;
    IF NEW.status = 'RELEASED' AND
       (NEW.status,NEW.release_audit_id,NEW.release_actor_id,NEW.released_at) IS DISTINCT FROM
       (OLD.status,OLD.release_audit_id,OLD.release_actor_id,OLD.released_at) THEN
        SELECT role_code INTO role_name FROM public.workspace_memberships
          WHERE workspace_id=NEW.workspace_id AND user_id=public.app_current_user_id() AND status='ACTIVE';
        IF NEW.reason <> 'MANUAL' OR role_name IS NULL OR role_name NOT IN ('ADMIN','OWNER')
           OR NEW.release_actor_id IS DISTINCT FROM public.app_current_user_id()
           OR NOT EXISTS (SELECT 1 FROM public.audit_events a WHERE a.workspace_id=NEW.workspace_id
              AND a.id=NEW.release_audit_id AND a.actor_kind='USER' AND a.actor_id=NEW.release_actor_id
              AND a.action='suppression.release_manual' AND a.target_type='suppression' AND a.target_id=NEW.id
              AND a.request_id IS NOT NULL AND a.effect_key IS NOT NULL AND pg_catalog.length(pg_catalog.btrim(a.reason)) > 0) THEN
            RAISE EXCEPTION USING ERRCODE='42501', MESSAGE='Manual suppression release requires authorized actor and matching audit';
        END IF;
    END IF;
    RETURN NEW;
END;
$function$;
REVOKE ALL ON FUNCTION public.app_guard_suppression_release() FROM PUBLIC, anon, authenticated, service_role;

-- The migration identity creates the trigger after ownership transfer; keep this
-- grant temporary so function execution is not opened to runtime/browser roles.
GRANT EXECUTE ON FUNCTION public.app_guard_suppression_release() TO CURRENT_USER;
GRANT app_integrity_guard TO CURRENT_USER;
GRANT CREATE ON SCHEMA public TO app_integrity_guard;
ALTER FUNCTION public.app_guard_suppression_release() OWNER TO app_integrity_guard;
REVOKE CREATE ON SCHEMA public FROM app_integrity_guard;

CREATE TRIGGER suppressions_guard_suppression_release BEFORE UPDATE ON public.suppressions FOR EACH ROW EXECUTE FUNCTION public.app_guard_suppression_release();
REVOKE EXECUTE ON FUNCTION public.app_guard_suppression_release() FROM CURRENT_USER;
REVOKE app_integrity_guard FROM CURRENT_USER;

CREATE TRIGGER notification_deliveries_touch_timestamp BEFORE UPDATE ON public.notification_deliveries FOR EACH ROW EXECUTE FUNCTION public.app_touch_timestamp();

CREATE TRIGGER capacity_debits_touch_timestamp BEFORE UPDATE ON public.capacity_debits FOR EACH ROW EXECUTE FUNCTION public.app_touch_timestamp();

ALTER TABLE public.notification_deliveries ADD CONSTRAINT notification_deliveries_error_size_check CHECK (safe_error IS NULL OR pg_catalog.char_length(safe_error) <= 1000);

ALTER TABLE public.notification_deliveries ADD CONSTRAINT notification_deliveries_provider_size_check CHECK (provider_identifier IS NULL OR pg_catalog.char_length(provider_identifier) BETWEEN 1 AND 1024);

ALTER TABLE public.notification_deliveries ADD CONSTRAINT notification_deliveries_sent_evidence_check CHECK (status <> 'SENT' OR channel='IN_APP' OR provider_identifier IS NOT NULL);

ALTER TABLE public.audit_events ADD CONSTRAINT audit_events_effect_size_check CHECK (effect_key IS NULL OR pg_catalog.char_length(effect_key) BETWEEN 1 AND 200);

ALTER TABLE public.rate_control ADD CONSTRAINT rate_control_ready_evidence_check CHECK (status <> 'READY' OR recovery_watermark IS NOT NULL);

ALTER TABLE public.capacity_debit_scopes ADD CONSTRAINT capacity_debit_scopes_calendar_bucket_check CHECK (bucket_end_at IS NULL OR bucket_end_at > bucket_start_at);

CREATE INDEX notification_deliveries_lease_recovery_idx ON public.notification_deliveries (workspace_id, lease_expires_at, id) WHERE status = 'SENDING';

CREATE INDEX notification_deliveries_unknown_recovery_idx ON public.notification_deliveries (workspace_id, updated_at, id) WHERE status = 'UNKNOWN';

-- Historical scope quantities cannot describe a different debit operation.
ALTER TABLE public.capacity_debits ADD CONSTRAINT capacity_debits_accounting_identity_key
UNIQUE (workspace_id, id, unit, quantity, authorized_at);
ALTER TABLE public.capacity_debit_scopes ADD CONSTRAINT capacity_debit_scopes_accounting_identity_fkey
FOREIGN KEY (workspace_id, debit_id, unit, quantity, authorized_at)
REFERENCES public.capacity_debits (workspace_id, id, unit, quantity, authorized_at)
ON UPDATE RESTRICT ON DELETE RESTRICT;

-- Never treat a missing singleton or an unreconstructed Redis generation as
-- permission to authorize provider I/O. Commands also compare the Redis token.
CREATE FUNCTION public.app_require_rate_readiness() RETURNS trigger
LANGUAGE plpgsql VOLATILE SECURITY INVOKER SET search_path = ''
AS $function$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM public.rate_control WHERE id AND status='READY' AND recovery_watermark IS NOT NULL) THEN
        RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Rate control must be reconstructed before authorizing capacity';
    END IF;
    RETURN NEW;
END;
$function$;
REVOKE ALL ON FUNCTION public.app_require_rate_readiness() FROM PUBLIC, anon, authenticated, service_role;
CREATE TRIGGER capacity_debits_require_rate_ready BEFORE INSERT ON public.capacity_debits
FOR EACH ROW EXECUTE FUNCTION public.app_require_rate_readiness();

COMMIT;
