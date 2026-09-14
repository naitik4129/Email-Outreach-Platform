from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import RowMapping, text
from sqlalchemy.orm import Session


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
        rows = (
            self.session.execute(
                text(
                    """
                    SELECT
                        l.id AS lead_id, l.status AS lead_status,
                        l.contact_revision, l.first_name, l.last_name,
                        l.company, l.title, l.custom_fields,
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
