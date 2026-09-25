from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import RowMapping, text
from sqlalchemy.orm import Session

from app.modules.leads.fields import PROFILE_FIELD_NAMES


def _safe_set_role(session: Session, role_name: str) -> None:
    """Attempt SET LOCAL ROLE if running against PostgreSQL; ignore in SQLite.

    Mirrors app.modules.mailboxes.repository._safe_set_role -- duplicated
    rather than imported, matching the existing per-repository convention.
    """
    bind = session.get_bind()
    if bind and getattr(bind.dialect, "name", "") != "sqlite":
        session.execute(text(f"RESET ROLE; SET LOCAL ROLE {role_name}"))


class CampaignWorkerRepository:
    """Everything requiring the app_worker_general role.

    Kept in a separate file from CampaignRepository (app_api role) so
    app_api-serving code paths can never accidentally import a role-switching
    method. Only workers/campaigns.py constructs this class.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def claim_capture_job(
        self,
        *,
        workspace_id: UUID,
        audience_id: UUID,
        lease_owner: str,
        ttl_seconds: int,
    ) -> RowMapping | None:
        _safe_set_role(self.session, "app_worker_general")
        row = (
            self.session.execute(
                text(
                    """
                    UPDATE campaign_planning_jobs
                    SET state = 'PROCESSING',
                        lease_owner = :lease_owner,
                        lease_generation = lease_generation + 1,
                        lease_expires_at = pg_catalog.transaction_timestamp()
                            + make_interval(secs => :ttl_seconds),
                        next_due_at = pg_catalog.transaction_timestamp()
                            + make_interval(secs => :ttl_seconds)
                    WHERE workspace_id = :workspace_id
                      AND audience_id = :audience_id
                      AND phase = 'CAPTURE'
                      AND state IN ('PENDING', 'PROCESSING')
                      AND (
                          lease_owner IS NULL
                          OR lease_expires_at < pg_catalog.transaction_timestamp()
                          OR lease_owner = :lease_owner
                      )
                    RETURNING *
                    """
                ),
                {
                    "workspace_id": str(workspace_id),
                    "audience_id": str(audience_id),
                    "lease_owner": lease_owner,
                    "ttl_seconds": ttl_seconds,
                },
            )
            .mappings()
            .first()
        )
        return row

    def get_audience(
        self, *, workspace_id: UUID, audience_id: UUID
    ) -> RowMapping | None:
        _safe_set_role(self.session, "app_worker_general")
        row = (
            self.session.execute(
                text(
                    """
                    SELECT * FROM campaign_audiences
                    WHERE workspace_id = :workspace_id AND id = :audience_id
                    """
                ),
                {"workspace_id": str(workspace_id), "audience_id": str(audience_id)},
            )
            .mappings()
            .first()
        )
        return row

    def resolve_candidate_lead_ids(
        self, *, workspace_id: UUID, list_ids: Sequence[str], lead_ids: Sequence[str]
    ) -> list[str]:
        """Deduplicated, deterministically ordered candidate lead ids for a
        selection manifest. Computed once (chunk 0) and persisted verbatim
        into campaign_planning_jobs.cursor_data so later chunks resume
        without recomputing -- selection_manifest is bounded to 65536 bytes,
        so this candidate set is bounded too."""
        _safe_set_role(self.session, "app_worker_general")
        rows = (
            self.session.execute(
                text(
                    """
                    SELECT DISTINCT lead_id::text AS lead_id FROM (
                        SELECT id AS lead_id FROM leads
                        WHERE workspace_id = :workspace_id AND id = ANY(:lead_ids)
                        UNION
                        SELECT llm.lead_id FROM lead_list_memberships llm
                        WHERE llm.workspace_id = :workspace_id
                          AND llm.list_id = ANY(:list_ids)
                    ) candidates
                    ORDER BY lead_id
                    """
                ),
                {
                    "workspace_id": str(workspace_id),
                    "lead_ids": list(lead_ids),
                    "list_ids": list(list_ids),
                },
            )
            .mappings()
            .all()
        )
        return [str(row["lead_id"]) for row in rows]

    def fetch_lead_capture_rows(
        self, *, workspace_id: UUID, lead_ids: Sequence[str]
    ) -> list[RowMapping]:
        """One round trip for a whole chunk -- avoids N+1 lead/suppression
        lookups. LEFT JOINs recipient_addresses (by canonical address, since
        leads has no direct address_id FK) and correlates the suppression
        check per row rather than issuing one query per lead."""
        _safe_set_role(self.session, "app_worker_general")
        # Every profile field must be selected here: build_lead_render_context reads
        # them from this row, and a missing column would render as empty in the
        # frozen snapshot rather than fail.
        profile_columns = ", ".join(f"l.{name}" for name in PROFILE_FIELD_NAMES)
        rows = (
            self.session.execute(
                text(
                    f"""
                    SELECT
                        l.id AS lead_id, l.status AS lead_status,
                        l.contact_revision, l.first_name, l.last_name,
                        l.company, l.title, {profile_columns}, l.custom_fields,
                        l.original_address, l.canonical_address,
                        ra.id AS address_id,
                        EXISTS (
                            SELECT 1 FROM suppressions s
                            WHERE s.workspace_id = l.workspace_id
                              AND s.address_id = ra.id
                              AND s.status = 'ACTIVE'
                        ) AS is_suppressed
                    FROM leads l
                    LEFT JOIN recipient_addresses ra
                      ON ra.workspace_id = l.workspace_id
                     AND ra.canonical_address = l.canonical_address
                     AND ra.normalization_version = l.normalization_version
                    WHERE l.workspace_id = :workspace_id AND l.id = ANY(:lead_ids)
                    """
                ),
                {"workspace_id": str(workspace_id), "lead_ids": list(lead_ids)},
            )
            .mappings()
            .all()
        )
        return list(rows)

    def insert_audience_members(
        self,
        *,
        workspace_id: UUID,
        campaign_id: UUID,
        audience_id: UUID,
        members: list[dict[str, Any]],
    ) -> None:
        if not members:
            return
        _safe_set_role(self.session, "app_worker_general")
        self.session.execute(
            text(
                """
                INSERT INTO campaign_audience_members
                    (id, workspace_id, campaign_id, audience_id, lead_id, address_id,
                     capture_ordinal, contact_revision, frozen_variables,
                     eligibility_status, exclusion_reason)
                VALUES
                    (:id, :workspace_id, :campaign_id, :audience_id, :lead_id,
                     :address_id, :capture_ordinal, :contact_revision,
                     CAST(:frozen_variables AS jsonb),
                     :eligibility_status, :exclusion_reason)
                ON CONFLICT DO NOTHING
                """
            ),
            [
                {
                    "id": str(uuid.uuid4()),
                    "workspace_id": str(workspace_id),
                    "campaign_id": str(campaign_id),
                    "audience_id": str(audience_id),
                    **member,
                    "frozen_variables": json.dumps(member["frozen_variables"]),
                }
                for member in members
            ],
        )

    def update_job_progress(
        self,
        *,
        workspace_id: UUID,
        job_id: UUID,
        cursor_data: str,
        processed_count: int,
        lease_owner: str,
        ttl_seconds: int,
    ) -> None:
        _safe_set_role(self.session, "app_worker_general")
        self.session.execute(
            text(
                """
                UPDATE campaign_planning_jobs
                SET cursor_data = :cursor_data,
                    processed_count = :processed_count,
                    lease_owner = :lease_owner,
                    lease_expires_at = pg_catalog.transaction_timestamp()
                        + make_interval(secs => :ttl_seconds),
                    next_due_at = pg_catalog.transaction_timestamp()
                WHERE workspace_id = :workspace_id AND id = :job_id
                """
            ),
            {
                "workspace_id": str(workspace_id),
                "job_id": str(job_id),
                "cursor_data": cursor_data,
                "processed_count": processed_count,
                "lease_owner": lease_owner,
                "ttl_seconds": ttl_seconds,
            },
        )

    def complete_capture(
        self,
        *,
        workspace_id: UUID,
        campaign_id: UUID,
        audience_id: UUID,
        job_id: UUID,
        processed_count: int,
        source_manifest_digest: str,
    ) -> None:
        _safe_set_role(self.session, "app_worker_general")
        self.session.execute(
            text(
                """
                UPDATE campaign_planning_jobs
                SET state = 'READY', processed_count = :processed_count,
                    next_due_at = NULL
                WHERE workspace_id = :workspace_id AND id = :job_id
                """
            ),
            {
                "workspace_id": str(workspace_id),
                "job_id": str(job_id),
                "processed_count": processed_count,
            },
        )
        self.session.execute(
            text(
                """
                UPDATE campaign_audiences
                SET status = 'READY',
                    source_manifest_digest = :digest,
                    completed_at = pg_catalog.transaction_timestamp()
                WHERE workspace_id = :workspace_id AND campaign_id = :campaign_id
                  AND id = :audience_id
                """
            ),
            {
                "workspace_id": str(workspace_id),
                "campaign_id": str(campaign_id),
                "audience_id": str(audience_id),
                "digest": source_manifest_digest,
            },
        )
        self._release_capture_sources(
            workspace_id=workspace_id, audience_id=audience_id
        )

    def fail_capture(
        self,
        *,
        workspace_id: UUID,
        campaign_id: UUID,
        audience_id: UUID,
        job_id: UUID,
        error_reason: str,
    ) -> None:
        _safe_set_role(self.session, "app_worker_general")
        self.session.execute(
            text(
                """
                UPDATE campaign_planning_jobs
                SET state = 'FAILED', error_reason = :error_reason, next_due_at = NULL
                WHERE workspace_id = :workspace_id AND id = :job_id
                """
            ),
            {
                "workspace_id": str(workspace_id),
                "job_id": str(job_id),
                "error_reason": error_reason[:500],
            },
        )
        self.session.execute(
            text(
                """
                UPDATE campaign_audiences
                SET status = 'FAILED', completed_at = pg_catalog.transaction_timestamp()
                WHERE workspace_id = :workspace_id AND campaign_id = :campaign_id
                  AND id = :audience_id
                """
            ),
            {
                "workspace_id": str(workspace_id),
                "campaign_id": str(campaign_id),
                "audience_id": str(audience_id),
            },
        )
        self._release_capture_sources(
            workspace_id=workspace_id, audience_id=audience_id
        )

    def abandon_capture(
        self,
        *,
        workspace_id: UUID,
        campaign_id: UUID,
        audience_id: UUID,
        job_id: UUID,
    ) -> None:
        _safe_set_role(self.session, "app_worker_general")
        self.session.execute(
            text(
                """
                UPDATE campaign_planning_jobs
                SET state = 'FAILED', error_reason = 'abandoned_by_user',
                    next_due_at = NULL
                WHERE workspace_id = :workspace_id AND id = :job_id
                """
            ),
            {"workspace_id": str(workspace_id), "job_id": str(job_id)},
        )
        self.session.execute(
            text(
                """
                UPDATE campaign_audiences
                SET status = 'ABANDONED',
                    completed_at = pg_catalog.transaction_timestamp()
                WHERE workspace_id = :workspace_id AND campaign_id = :campaign_id
                  AND id = :audience_id
                """
            ),
            {
                "workspace_id": str(workspace_id),
                "campaign_id": str(campaign_id),
                "audience_id": str(audience_id),
            },
        )
        self._release_capture_sources(
            workspace_id=workspace_id, audience_id=audience_id
        )

    # -------------------------------------------------------------------
    # Activation planning (ENROLL / RENDER phases)
    # -------------------------------------------------------------------

    def claim_planning_job(
        self,
        *,
        workspace_id: UUID,
        campaign_id: UUID,
        activation_id: UUID,
        phase: str,
        lease_owner: str,
        ttl_seconds: int,
    ) -> RowMapping | None:
        _safe_set_role(self.session, "app_worker_general")
        row = (
            self.session.execute(
                text(
                    """
                    UPDATE campaign_planning_jobs
                    SET state = 'PROCESSING',
                        lease_owner = :lease_owner,
                        lease_generation = lease_generation + 1,
                        lease_expires_at = pg_catalog.transaction_timestamp()
                            + make_interval(secs => :ttl_seconds),
                        next_due_at = pg_catalog.transaction_timestamp()
                            + make_interval(secs => :ttl_seconds)
                    WHERE workspace_id = :workspace_id
                      AND campaign_id = :campaign_id
                      AND activation_id = :activation_id
                      AND phase = :phase
                      AND state IN ('PENDING', 'PROCESSING')
                      AND (
                          lease_owner IS NULL
                          OR lease_expires_at < pg_catalog.transaction_timestamp()
                          OR lease_owner = :lease_owner
                      )
                    RETURNING *
                    """
                ),
                {
                    "workspace_id": str(workspace_id),
                    "campaign_id": str(campaign_id),
                    "activation_id": str(activation_id),
                    "phase": phase,
                    "lease_owner": lease_owner,
                    "ttl_seconds": ttl_seconds,
                },
            )
            .mappings()
            .first()
        )
        return row

    def get_activation_context(
        self, *, workspace_id: UUID, campaign_id: UUID, activation_id: UUID
    ) -> RowMapping | None:
        """Guards on activation_id matching the campaign's *current*
        activation: if the campaign has since been reactivated (a new
        activation_id), this returns None so the caller can recognize its
        job as stale work superseded by a fresh activation, not a failure."""
        _safe_set_role(self.session, "app_worker_general")
        row = (
            self.session.execute(
                text(
                    """
                    SELECT c.start_at, c.activated_audience_id,
                           c.activated_sequence_id, sv.timezone, sv.weekday_set,
                           sv.window_start_local, sv.window_end_local,
                           ss.id AS first_step_id
                    FROM campaigns c
                    JOIN campaign_settings_versions sv
                      ON sv.workspace_id = c.workspace_id
                     AND sv.id = c.current_settings_id
                    JOIN sequence_steps ss
                      ON ss.workspace_id = c.workspace_id
                     AND ss.sequence_id = c.activated_sequence_id
                     AND ss.position = 1
                    WHERE c.workspace_id = :workspace_id AND c.id = :campaign_id
                      AND c.activation_id = :activation_id
                    """
                ),
                {
                    "workspace_id": str(workspace_id),
                    "campaign_id": str(campaign_id),
                    "activation_id": str(activation_id),
                },
            )
            .mappings()
            .first()
        )
        return row

    def list_eligible_campaign_mailboxes(
        self, *, workspace_id: UUID, campaign_id: UUID
    ) -> list[RowMapping]:
        _safe_set_role(self.session, "app_worker_general")
        rows = (
            self.session.execute(
                text(
                    """
                    SELECT mailbox_id FROM campaign_mailboxes
                    WHERE workspace_id = :workspace_id AND campaign_id = :campaign_id
                      AND active = true
                    ORDER BY allocation_position ASC
                    """
                ),
                {"workspace_id": str(workspace_id), "campaign_id": str(campaign_id)},
            )
            .mappings()
            .all()
        )
        return list(rows)

    def fetch_accepted_audience_members_chunk(
        self,
        *,
        workspace_id: UUID,
        audience_id: UUID,
        after_ordinal: int,
        limit: int,
    ) -> list[RowMapping]:
        _safe_set_role(self.session, "app_worker_general")
        rows = (
            self.session.execute(
                text(
                    """
                    SELECT m.id AS audience_member_id, m.lead_id, m.address_id,
                           m.capture_ordinal, m.frozen_variables,
                           ra.canonical_address
                    FROM campaign_audience_members m
                    JOIN recipient_addresses ra
                      ON ra.workspace_id = m.workspace_id AND ra.id = m.address_id
                    WHERE m.workspace_id = :workspace_id
                      AND m.audience_id = :audience_id
                      AND m.eligibility_status = 'ACCEPTED'
                      AND m.capture_ordinal > :after_ordinal
                    ORDER BY m.capture_ordinal ASC
                    LIMIT :limit
                    """
                ),
                {
                    "workspace_id": str(workspace_id),
                    "audience_id": str(audience_id),
                    "after_ordinal": after_ordinal,
                    "limit": limit,
                },
            )
            .mappings()
            .all()
        )
        return list(rows)

    def insert_enrollments(
        self,
        *,
        workspace_id: UUID,
        campaign_id: UUID,
        audience_id: UUID,
        sequence_id: UUID,
        first_step_id: UUID,
        rows: list[dict[str, Any]],
    ) -> None:
        """rows: each dict with audience_member_id, lead_id, address_id,
        frozen_destination, frozen_variables, assigned_mailbox_id.
        ON CONFLICT DO NOTHING against campaign_enrollments' unique
        (workspace_id, campaign_id, lead_id) / (..., address_id) keys makes a
        redelivered chunk converge on the same rows rather than duplicate."""
        if not rows:
            return
        _safe_set_role(self.session, "app_worker_general")
        self.session.execute(
            text(
                """
                INSERT INTO campaign_enrollments
                    (id, workspace_id, campaign_id, audience_id, audience_member_id,
                     sequence_id, lead_id, address_id, frozen_destination,
                     frozen_variables, assigned_mailbox_id, state, next_step_id,
                     next_sequence_position)
                VALUES
                    (:id, :workspace_id, :campaign_id, :audience_id,
                     :audience_member_id, :sequence_id, :lead_id, :address_id,
                     :frozen_destination, CAST(:frozen_variables AS jsonb),
                     :assigned_mailbox_id, 'ACTIVE', :next_step_id, 1)
                ON CONFLICT DO NOTHING
                """
            ),
            [
                {
                    "id": str(uuid.uuid4()),
                    "workspace_id": str(workspace_id),
                    "campaign_id": str(campaign_id),
                    "audience_id": str(audience_id),
                    "sequence_id": str(sequence_id),
                    "next_step_id": str(first_step_id),
                    **row,
                    "frozen_variables": json.dumps(row["frozen_variables"]),
                }
                for row in rows
            ],
        )

    def get_enrollments_for_leads(
        self, *, workspace_id: UUID, campaign_id: UUID, lead_ids: Sequence[str]
    ) -> list[RowMapping]:
        if not lead_ids:
            return []
        _safe_set_role(self.session, "app_worker_general")
        rows = (
            self.session.execute(
                text(
                    """
                    SELECT id, lead_id, assigned_mailbox_id, address_id
                    FROM campaign_enrollments
                    WHERE workspace_id = :workspace_id AND campaign_id = :campaign_id
                      AND lead_id = ANY(:lead_ids)
                    """
                ),
                {
                    "workspace_id": str(workspace_id),
                    "campaign_id": str(campaign_id),
                    "lead_ids": list(lead_ids),
                },
            )
            .mappings()
            .all()
        )
        return list(rows)

    def insert_planned_first_messages(
        self,
        *,
        workspace_id: UUID,
        campaign_id: UUID,
        sequence_id: UUID,
        step_id: UUID,
        rows: list[dict[str, Any]],
    ) -> None:
        """rows: each dict with enrollment_id, mailbox_id, address_id.
        ON CONFLICT DO NOTHING against messages_enrollment_step_key -- a
        retried chunk converges on the same message row, never a duplicate."""
        if not rows:
            return
        _safe_set_role(self.session, "app_worker_general")
        self.session.execute(
            text(
                """
                INSERT INTO messages
                    (id, workspace_id, purpose, campaign_id, enrollment_id,
                     sequence_id, step_id, mailbox_id, address_id, status)
                VALUES
                    (:id, :workspace_id, 'CAMPAIGN', :campaign_id, :enrollment_id,
                     :sequence_id, :step_id, :mailbox_id, :address_id, 'PLANNED')
                ON CONFLICT DO NOTHING
                """
            ),
            [
                {
                    "id": str(uuid.uuid4()),
                    "workspace_id": str(workspace_id),
                    "campaign_id": str(campaign_id),
                    "sequence_id": str(sequence_id),
                    "step_id": str(step_id),
                    **row,
                }
                for row in rows
            ],
        )

    def complete_enroll_job(
        self,
        *,
        workspace_id: UUID,
        campaign_id: UUID,
        job_id: UUID,
        processed_count: int,
    ) -> None:
        _safe_set_role(self.session, "app_worker_general")
        self.session.execute(
            text(
                """
                UPDATE campaign_planning_jobs
                SET state = 'READY', processed_count = :processed_count,
                    next_due_at = NULL
                WHERE workspace_id = :workspace_id AND id = :job_id
                """
            ),
            {
                "workspace_id": str(workspace_id),
                "job_id": str(job_id),
                "processed_count": processed_count,
            },
        )
        self.session.execute(
            text(
                """
                UPDATE campaigns SET planning_status = 'READY'
                WHERE workspace_id = :workspace_id AND id = :campaign_id
                """
            ),
            {"workspace_id": str(workspace_id), "campaign_id": str(campaign_id)},
        )

    def fetch_planned_messages_chunk(
        self, *, workspace_id: UUID, campaign_id: UUID, limit: int
    ) -> list[RowMapping]:
        """FOR UPDATE OF m SKIP LOCKED: rows leave PLANNED as they're
        rendered, so redelivery is naturally idempotent and multiple RENDER
        workers can safely run concurrently over the same campaign."""
        _safe_set_role(self.session, "app_worker_general")
        rows = (
            self.session.execute(
                text(
                    """
                    SELECT m.id, m.mailbox_id, e.frozen_variables, e.frozen_destination,
                           ss.email_subject, ss.email_body_html,
                           mb.original_address AS sender_address,
                           mb.sender_display_name AS sender_name
                    FROM messages m
                    JOIN campaign_enrollments e
                      ON e.workspace_id = m.workspace_id AND e.id = m.enrollment_id
                    JOIN sequence_steps ss
                      ON ss.workspace_id = m.workspace_id AND ss.id = m.step_id
                    JOIN mailboxes mb
                      ON mb.workspace_id = m.workspace_id AND mb.id = m.mailbox_id
                    WHERE m.workspace_id = :workspace_id
                      AND m.campaign_id = :campaign_id
                      AND m.status = 'PLANNED'
                    ORDER BY m.id
                    LIMIT :limit
                    FOR UPDATE OF m SKIP LOCKED
                    """
                ),
                {
                    "workspace_id": str(workspace_id),
                    "campaign_id": str(campaign_id),
                    "limit": limit,
                },
            )
            .mappings()
            .all()
        )
        return list(rows)

    def render_message(
        self,
        *,
        workspace_id: UUID,
        message_id: UUID,
        content_subject: str,
        content_body_html: str,
        content_digest: str,
        frozen_destination: str,
        frozen_sender_address: str,
        frozen_sender_name: str | None,
        due_at: Any,
        anchor_at: Any,
    ) -> None:
        _safe_set_role(self.session, "app_worker_general")
        self.session.execute(
            text(
                """
                UPDATE messages
                SET content_subject = :content_subject,
                    content_body_html = :content_body_html,
                    content_digest = :content_digest,
                    frozen_destination = :frozen_destination,
                    frozen_sender_address = :frozen_sender_address,
                    frozen_sender_name = :frozen_sender_name,
                    rendered_at = pg_catalog.transaction_timestamp(),
                    due_at = :due_at,
                    anchor_at = :anchor_at,
                    status = 'SCHEDULED'
                WHERE workspace_id = :workspace_id AND id = :message_id
                  AND status = 'PLANNED'
                """
            ),
            {
                "workspace_id": str(workspace_id),
                "message_id": str(message_id),
                "content_subject": content_subject,
                "content_body_html": content_body_html,
                "content_digest": content_digest,
                "frozen_destination": frozen_destination,
                "frozen_sender_address": frozen_sender_address,
                "frozen_sender_name": frozen_sender_name,
                "due_at": due_at,
                "anchor_at": anchor_at,
            },
        )

    def complete_render_job(
        self, *, workspace_id: UUID, job_id: UUID, processed_count: int
    ) -> None:
        _safe_set_role(self.session, "app_worker_general")
        self.session.execute(
            text(
                """
                UPDATE campaign_planning_jobs
                SET state = 'READY', processed_count = :processed_count,
                    next_due_at = NULL
                WHERE workspace_id = :workspace_id AND id = :job_id
                """
            ),
            {
                "workspace_id": str(workspace_id),
                "job_id": str(job_id),
                "processed_count": processed_count,
            },
        )

    def fail_planning_job(
        self, *, workspace_id: UUID, job_id: UUID, error_reason: str
    ) -> None:
        """Fails the job only -- deliberately never touches campaigns.status.
        Auto-classifying a planning failure as campaign-level ERROR is an
        unapproved product decision (CAMPAIGN_ENGINE.md Open Decisions);
        nothing in this codebase sets ERROR today."""
        _safe_set_role(self.session, "app_worker_general")
        self.session.execute(
            text(
                """
                UPDATE campaign_planning_jobs
                SET state = 'FAILED', error_reason = :error_reason, next_due_at = NULL
                WHERE workspace_id = :workspace_id AND id = :job_id
                """
            ),
            {
                "workspace_id": str(workspace_id),
                "job_id": str(job_id),
                "error_reason": error_reason[:500],
            },
        )

    def _release_capture_sources(
        self, *, workspace_id: UUID, audience_id: UUID
    ) -> None:
        self.session.execute(
            text(
                """
                UPDATE audience_capture_sources
                SET gate_held = false, released_at = pg_catalog.transaction_timestamp()
                WHERE workspace_id = :workspace_id AND audience_id = :audience_id
                  AND gate_held = true
                """
            ),
            {"workspace_id": str(workspace_id), "audience_id": str(audience_id)},
        )
