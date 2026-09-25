from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, cast
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.modules.leads.fields import PROFILE_FIELD_NAMES
from app.modules.leads.repository import LeadRepository


class ImportRepository:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.leads = LeadRepository(session)

    # -- job lifecycle ----------------------------------------------------

    def create_job(
        self,
        *,
        workspace_id: UUID,
        initiator_id: UUID,
        storage_object_key: str,
        storage_object_version: str,
        storage_object_digest: str,
        import_kind: str,
        mapping: dict[str, Any],
        list_id: UUID | None,
        total_rows: int,
    ) -> Mapping[str, Any] | None:
        return cast(
            Mapping[str, Any] | None,
            self.session.execute(
                text(
                    """
                    INSERT INTO import_jobs
                        (workspace_id, initiator_id, storage_object_key,
                         storage_object_version, storage_object_digest, import_kind,
                         mapping, list_id, status, total_rows, next_due_at)
                    VALUES
                        (:workspace_id, :initiator_id, :storage_object_key,
                         :storage_object_version, :storage_object_digest, :import_kind,
                         CAST(:mapping AS jsonb), :list_id, 'PENDING', :total_rows,
                         pg_catalog.transaction_timestamp())
                    ON CONFLICT (workspace_id, storage_object_key) DO NOTHING
                    RETURNING *
                    """
                ),
                {
                    "workspace_id": str(workspace_id),
                    "initiator_id": str(initiator_id),
                    "storage_object_key": storage_object_key,
                    "storage_object_version": storage_object_version,
                    "storage_object_digest": storage_object_digest,
                    "import_kind": import_kind,
                    "mapping": json.dumps(mapping),
                    "list_id": str(list_id) if list_id else None,
                    "total_rows": total_rows,
                },
            )
            .mappings()
            .first(),
        )

    def get_job_by_storage_key(
        self, *, workspace_id: UUID, storage_object_key: str
    ) -> Mapping[str, Any] | None:
        return cast(
            Mapping[str, Any] | None,
            self.session.execute(
                text(
                    """
                    SELECT * FROM import_jobs
                    WHERE workspace_id = :workspace_id AND storage_object_key = :key
                    """
                ),
                {"workspace_id": str(workspace_id), "key": storage_object_key},
            )
            .mappings()
            .first(),
        )

    def get_job(
        self, *, workspace_id: UUID, import_id: UUID
    ) -> Mapping[str, Any] | None:
        return cast(
            Mapping[str, Any] | None,
            self.session.execute(
                text(
                    "SELECT * FROM import_jobs WHERE workspace_id = :workspace_id AND id = :id"
                ),
                {"workspace_id": str(workspace_id), "id": str(import_id)},
            )
            .mappings()
            .first(),
        )

    def list_jobs(
        self, *, workspace_id: UUID, limit: int, after_id: UUID | None
    ) -> Sequence[Mapping[str, Any]]:
        clauses = ["workspace_id = :workspace_id"]
        params: dict[str, Any] = {"workspace_id": str(workspace_id), "limit": limit}
        if after_id is not None:
            clauses.append("id > :after_id")
            params["after_id"] = str(after_id)
        return cast(
            Sequence[Mapping[str, Any]],
            self.session.execute(
                text(
                    f"""
                    SELECT * FROM import_jobs
                    WHERE {' AND '.join(clauses)}
                    ORDER BY id ASC
                    LIMIT :limit
                    """
                ),
                params,
            )
            .mappings()
            .all(),
        )

    def claim_job(
        self, *, workspace_id: UUID, import_id: UUID, lease_owner: str, ttl_seconds: int
    ) -> Mapping[str, Any] | None:
        """Compare-and-swap claim: only one concurrent delivery can win this.

        Matches PENDING jobs, PROCESSING jobs whose lease has expired
        (crashed-worker recovery), and PROCESSING jobs already leased to this
        same owner: a Celery retry keeps its task id, so that is how a
        multi-chunk import continues on its own lease instead of stalling
        until it expires. A job being processed under someone else's live
        lease, or already terminal, matches none of these and this simply
        returns None -- the caller must treat that as a no-op, never as an
        error (CLAUDE.md Phase 3 task, "Worker Execution": reload
        authoritative state, do not trust a stale queue payload).
        """
        return cast(
            Mapping[str, Any] | None,
            self.session.execute(
                text(
                    """
                    UPDATE import_jobs
                    SET status = 'PROCESSING',
                        lease_owner = :lease_owner,
                        lease_generation = lease_generation + 1,
                        lease_expires_at = pg_catalog.transaction_timestamp()
                            + make_interval(secs => :ttl_seconds),
                        next_due_at = pg_catalog.transaction_timestamp()
                            + make_interval(secs => :ttl_seconds)
                    WHERE workspace_id = :workspace_id AND id = :id
                      AND (
                          status = 'PENDING'
                          OR (status = 'PROCESSING'
                              AND (lease_expires_at < pg_catalog.transaction_timestamp()
                                   OR lease_owner = :lease_owner))
                      )
                    RETURNING *
                    """
                ),
                {
                    "workspace_id": str(workspace_id),
                    "id": str(import_id),
                    "lease_owner": lease_owner,
                    "ttl_seconds": ttl_seconds,
                },
            )
            .mappings()
            .first(),
        )

    def update_progress(
        self,
        *,
        workspace_id: UUID,
        import_id: UUID,
        row_cursor: int,
        processed_rows: int,
        accepted_rows: int,
        duplicate_rows: int,
        rejected_rows: int,
        lease_owner: str,
        ttl_seconds: int,
    ) -> None:
        self.session.execute(
            text(
                """
                UPDATE import_jobs
                SET row_cursor = :row_cursor,
                    processed_rows = :processed_rows,
                    accepted_rows = :accepted_rows,
                    duplicate_rows = :duplicate_rows,
                    rejected_rows = :rejected_rows,
                    lease_owner = :lease_owner,
                    lease_expires_at = pg_catalog.transaction_timestamp()
                        + make_interval(secs => :ttl_seconds),
                    next_due_at = pg_catalog.transaction_timestamp()
                        + make_interval(secs => :ttl_seconds)
                WHERE workspace_id = :workspace_id AND id = :id
                """
            ),
            {
                "workspace_id": str(workspace_id),
                "id": str(import_id),
                "row_cursor": row_cursor,
                "processed_rows": processed_rows,
                "accepted_rows": accepted_rows,
                "duplicate_rows": duplicate_rows,
                "rejected_rows": rejected_rows,
                "lease_owner": lease_owner,
                "ttl_seconds": ttl_seconds,
            },
        )

    def finalize_job(
        self,
        *,
        workspace_id: UUID,
        import_id: UUID,
        row_cursor: int,
        processed_rows: int,
        accepted_rows: int,
        duplicate_rows: int,
        rejected_rows: int,
        status: str,
    ) -> None:
        self.session.execute(
            text(
                """
                UPDATE import_jobs
                SET row_cursor = :row_cursor,
                    processed_rows = :processed_rows,
                    accepted_rows = :accepted_rows,
                    duplicate_rows = :duplicate_rows,
                    rejected_rows = :rejected_rows,
                    status = :status,
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    next_due_at = NULL
                WHERE workspace_id = :workspace_id AND id = :id
                """
            ),
            {
                "workspace_id": str(workspace_id),
                "id": str(import_id),
                "row_cursor": row_cursor,
                "processed_rows": processed_rows,
                "accepted_rows": accepted_rows,
                "duplicate_rows": duplicate_rows,
                "rejected_rows": rejected_rows,
                "status": status,
            },
        )

    def fail_job(self, *, workspace_id: UUID, import_id: UUID, failure_summary: str) -> None:
        self.session.execute(
            text(
                """
                UPDATE import_jobs
                SET status = 'FAILED',
                    failure_summary = :failure_summary,
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    next_due_at = NULL
                WHERE workspace_id = :workspace_id AND id = :id
                """
            ),
            {
                "workspace_id": str(workspace_id),
                "id": str(import_id),
                "failure_summary": failure_summary,
            },
        )

    # -- row results --------------------------------------------------------

    def insert_row_result(
        self,
        *,
        workspace_id: UUID,
        import_id: UUID,
        row_number: int,
        status: str,
        lead_id: UUID | None = None,
        suppression_id: UUID | None = None,
        validation_reason: str | None = None,
    ) -> None:
        self.session.execute(
            text(
                """
                INSERT INTO import_row_results
                    (workspace_id, import_id, row_number, status, lead_id,
                     suppression_id, validation_reason)
                VALUES
                    (:workspace_id, :import_id, :row_number, :status, :lead_id,
                     :suppression_id, :validation_reason)
                ON CONFLICT (workspace_id, import_id, row_number) DO NOTHING
                """
            ),
            {
                "workspace_id": str(workspace_id),
                "import_id": str(import_id),
                "row_number": row_number,
                "status": status,
                "lead_id": str(lead_id) if lead_id else None,
                "suppression_id": str(suppression_id) if suppression_id else None,
                "validation_reason": validation_reason,
            },
        )

    def list_row_results(
        self, *, workspace_id: UUID, import_id: UUID, limit: int, after_row_number: int | None
    ) -> Sequence[Mapping[str, Any]]:
        clauses = ["workspace_id = :workspace_id", "import_id = :import_id"]
        params: dict[str, Any] = {
            "workspace_id": str(workspace_id),
            "import_id": str(import_id),
            "limit": limit,
        }
        if after_row_number is not None:
            clauses.append("row_number > :after_row_number")
            params["after_row_number"] = after_row_number
        return cast(
            Sequence[Mapping[str, Any]],
            self.session.execute(
                text(
                    f"""
                    SELECT * FROM import_row_results
                    WHERE {' AND '.join(clauses)}
                    ORDER BY row_number ASC
                    LIMIT :limit
                    """
                ),
                params,
            )
            .mappings()
            .all(),
        )

    # -- leads / suppression writes used while processing rows --------------

    def get_address_id(
        self, *, workspace_id: UUID, canonical_address: str
    ) -> UUID | None:
        row = self.session.execute(
            text(
                """
                SELECT id FROM recipient_addresses
                WHERE workspace_id = :workspace_id AND canonical_address = :canonical_address
                  AND normalization_version = 1
                """
            ),
            {"workspace_id": str(workspace_id), "canonical_address": canonical_address},
        ).first()
        return row[0] if row else None

    def upsert_lead(
        self,
        *,
        workspace_id: UUID,
        original_address: str,
        canonical_address: str,
        first_name: str | None,
        last_name: str | None,
        company: str | None,
        title: str | None,
        profile: Mapping[str, Any],
    ) -> tuple[UUID, bool]:
        # Column names come from the fixed PROFILE_FIELD_NAMES tuple, never from input.
        profile_columns = ", ".join(PROFILE_FIELD_NAMES)
        profile_params = ", ".join(f":{name}" for name in PROFILE_FIELD_NAMES)
        row = self.session.execute(
            text(
                f"""
                INSERT INTO leads
                    (workspace_id, original_address, canonical_address,
                     normalization_version, first_name, last_name, company, title,
                     {profile_columns})
                VALUES
                    (:workspace_id, :original_address, :canonical_address, 1,
                     :first_name, :last_name, :company, :title,
                     {profile_params})
                ON CONFLICT (workspace_id, canonical_address, normalization_version)
                DO NOTHING
                RETURNING id
                """
            ),
            {
                "workspace_id": str(workspace_id),
                "original_address": original_address,
                "canonical_address": canonical_address,
                "first_name": first_name,
                "last_name": last_name,
                "company": company,
                "title": title,
                **{name: profile.get(name) for name in PROFILE_FIELD_NAMES},
            },
        ).first()
        if row is not None:
            return row[0], True
        existing = self.session.execute(
            text(
                """
                SELECT id FROM leads
                WHERE workspace_id = :workspace_id AND canonical_address = :canonical_address
                  AND normalization_version = 1
                """
            ),
            {"workspace_id": str(workspace_id), "canonical_address": canonical_address},
        ).first()
        assert existing is not None  # ON CONFLICT proved a row exists
        return existing[0], False

    def add_member_if_missing(
        self, *, workspace_id: UUID, list_id: UUID, lead_id: UUID, actor_id: UUID
    ) -> None:
        self.session.execute(
            text(
                """
                INSERT INTO lead_list_memberships (workspace_id, list_id, lead_id, added_by)
                VALUES (:workspace_id, :list_id, :lead_id, :added_by)
                ON CONFLICT (workspace_id, list_id, lead_id) DO NOTHING
                """
            ),
            {
                "workspace_id": str(workspace_id),
                "list_id": str(list_id),
                "lead_id": str(lead_id),
                "added_by": str(actor_id),
            },
        )

    def upsert_manual_suppression_for_import(
        self,
        *,
        workspace_id: UUID,
        address_id: UUID,
        import_id: UUID,
        row_number: int,
        actor_id: UUID,
    ) -> tuple[UUID, bool]:
        """Create, or reactivate-if-released, a MANUAL suppression.

        A SUPPRESSION-kind import only ever writes reason='MANUAL' -- Phase 3
        may not fabricate UNSUBSCRIBE/HARD_BOUNCE/COMPLAINT evidence
        (CLAUDE.md Phase 3 task, "Suppression Sources"). Reactivating a
        previously-released MANUAL suppression on a fresh import matches
        SUPPRESSION.md's "a later new valid source reactivates the
        prohibition with new audit evidence."
        """
        row = self.session.execute(
            text(
                """
                INSERT INTO suppressions
                    (workspace_id, address_id, reason, status, first_observed_at,
                     last_observed_at)
                VALUES
                    (:workspace_id, :address_id, 'MANUAL', 'ACTIVE',
                     pg_catalog.transaction_timestamp(), pg_catalog.transaction_timestamp())
                ON CONFLICT (workspace_id, address_id, reason) DO UPDATE
                SET status = 'ACTIVE',
                    last_observed_at = pg_catalog.transaction_timestamp(),
                    released_at = NULL,
                    release_actor_id = NULL,
                    release_audit_id = NULL
                WHERE suppressions.status = 'RELEASED'
                RETURNING id, (xmax = 0) AS was_insert
                """
            ),
            {"workspace_id": str(workspace_id), "address_id": str(address_id)},
        ).mappings().first()

        if row is None:
            existing = self.session.execute(
                text(
                    """
                    SELECT id FROM suppressions
                    WHERE workspace_id = :workspace_id AND address_id = :address_id
                      AND reason = 'MANUAL'
                    """
                ),
                {"workspace_id": str(workspace_id), "address_id": str(address_id)},
            ).first()
            assert existing is not None
            suppression_id, was_insert = existing[0], False
        else:
            suppression_id, was_insert = row["id"], bool(row["was_insert"])

        self.session.execute(
            text(
                """
                INSERT INTO suppression_sources
                    (workspace_id, suppression_id, source_kind, source_key, actor_id, evidence)
                VALUES
                    (:workspace_id, :suppression_id, 'IMPORT', :source_key, :actor_id,
                     CAST(:evidence AS jsonb))
                ON CONFLICT (workspace_id, suppression_id, source_kind, source_key) DO NOTHING
                """
            ),
            {
                "workspace_id": str(workspace_id),
                "suppression_id": str(suppression_id),
                "source_key": f"import:{import_id}:row:{row_number}",
                "actor_id": str(actor_id),
                "evidence": json.dumps({"import_id": str(import_id), "row_number": row_number}),
            },
        )
        return suppression_id, was_insert
