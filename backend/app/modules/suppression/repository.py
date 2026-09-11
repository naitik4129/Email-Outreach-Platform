from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, cast
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.modules.leads.repository import LeadRepository


class SuppressionRepository:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.leads = LeadRepository(session)

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

    def upsert_manual_suppression(
        self,
        *,
        workspace_id: UUID,
        address_id: UUID,
        actor_id: UUID,
    ) -> tuple[UUID, bool]:
        """Create, or reactivate-if-released, a MANUAL suppression."""
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

        # Create the source record for the manual addition
        self.session.execute(
            text(
                """
                INSERT INTO suppression_sources
                    (workspace_id, suppression_id, source_kind, source_key, actor_id, evidence)
                VALUES
                    (:workspace_id, :suppression_id, 'MANUAL', :source_key, :actor_id,
                     CAST(:evidence AS jsonb))
                ON CONFLICT (workspace_id, suppression_id, source_kind, source_key) DO NOTHING
                """
            ),
            {
                "workspace_id": str(workspace_id),
                "suppression_id": str(suppression_id),
                "source_key": f"manual:{actor_id}",
                "actor_id": str(actor_id),
                "evidence": json.dumps({"action": "create_manual"}),
            },
        )
        return suppression_id, was_insert

    def list_suppressions(
        self,
        *,
        workspace_id: UUID,
        limit: int,
        after_id: UUID | None,
        query: str | None,
    ) -> Sequence[Mapping[str, Any]]:
        clauses = ["s.workspace_id = :workspace_id"]
        params: dict[str, Any] = {"workspace_id": str(workspace_id), "limit": limit}
        
        if after_id is not None:
            clauses.append("s.id > :after_id")
            params["after_id"] = str(after_id)
            
        if query:
            clauses.append("a.canonical_address ILIKE :query")
            params["query"] = f"%{query}%"

        where_sql = " AND ".join(clauses)
        return cast(
            Sequence[Mapping[str, Any]],
            self.session.execute(
                text(
                    f"""
                    SELECT s.*,
                           a.canonical_address,
                           a.original_display AS email
                    FROM suppressions s
                    JOIN recipient_addresses a
                      ON a.workspace_id = s.workspace_id
                     AND a.id = s.address_id
                    WHERE {where_sql}
                    ORDER BY s.id ASC
                    LIMIT :limit
                    """
                ),
                params,
            )
            .mappings()
            .all()
        )

    def get_suppression(
        self, *, workspace_id: UUID, suppression_id: UUID
    ) -> Mapping[str, Any] | None:
        return cast(
            Mapping[str, Any] | None,
            self.session.execute(
                text(
                    """
                    SELECT s.*,
                           a.canonical_address,
                           a.original_display AS email
                    FROM suppressions s
                    JOIN recipient_addresses a
                      ON a.workspace_id = s.workspace_id
                     AND a.id = s.address_id
                    WHERE s.workspace_id = :workspace_id
                      AND s.id = :id
                    """
                ),
                {"workspace_id": str(workspace_id), "id": str(suppression_id)},
            )
            .mappings()
            .first()
        )

    def release_manual_suppression(
        self,
        *,
        workspace_id: UUID,
        suppression_id: UUID,
        actor_id: UUID,
        reason: str,
    ) -> Mapping[str, Any] | None:
        # Note: We enforce reason='MANUAL' in the UPDATE because SUPPRESSION.md states
        # that only MANUAL suppressions can be released by an authorized user in Phase 3.
        return cast(
            Mapping[str, Any] | None,
            self.session.execute(
                text(
                    """
                    UPDATE suppressions
                    SET status = 'RELEASED',
                        released_at = pg_catalog.transaction_timestamp(),
                        release_actor_id = :actor_id,
                        version = version + 1
                    WHERE workspace_id = :workspace_id
                      AND id = :id
                      AND reason = 'MANUAL'
                      AND status = 'ACTIVE'
                    RETURNING id
                    """
                ),
                {
                    "workspace_id": str(workspace_id),
                    "id": str(suppression_id),
                    "actor_id": str(actor_id),
                },
            )
            .mappings()
            .first()
        )
