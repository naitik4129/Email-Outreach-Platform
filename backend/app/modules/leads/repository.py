from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, cast
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session


class LeadRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def ensure_recipient_address(
        self, *, workspace_id: UUID, canonical_address: str, original_display: str
    ) -> None:
        self.session.execute(
            text(
                """
                INSERT INTO recipient_addresses
                    (workspace_id, canonical_address, normalization_version,
                     original_display)
                VALUES
                    (:workspace_id, :canonical_address, 1, :original_display)
                ON CONFLICT (workspace_id, canonical_address, normalization_version)
                DO NOTHING
                """
            ),
            {
                "workspace_id": str(workspace_id),
                "canonical_address": canonical_address,
                "original_display": original_display,
            },
        )

    def create_lead(
        self,
        *,
        workspace_id: UUID,
        original_address: str,
        canonical_address: str,
        first_name: str | None,
        last_name: str | None,
        company: str | None,
        title: str | None,
        custom_fields: dict[str, Any],
    ) -> Mapping[str, Any]:
        return cast(
            Mapping[str, Any],
            self.session.execute(
                text(
                    """
                    INSERT INTO leads
                        (workspace_id, original_address, canonical_address,
                         normalization_version, first_name, last_name, company,
                         title, custom_fields)
                    VALUES
                        (:workspace_id, :original_address, :canonical_address, 1,
                         :first_name, :last_name, :company, :title,
                         CAST(:custom_fields AS jsonb))
                    RETURNING *
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
                    "custom_fields": json.dumps(custom_fields),
                },
            )
            .mappings()
            .one()
        )

    def list_leads(
        self,
        *,
        workspace_id: UUID,
        limit: int,
        after_id: UUID | None,
        status: str | None,
        query: str | None,
        list_id: UUID | None,
    ) -> Sequence[Mapping[str, Any]]:
        clauses = ["l.workspace_id = :workspace_id"]
        params: dict[str, Any] = {"workspace_id": str(workspace_id), "limit": limit}
        if after_id is not None:
            clauses.append("l.id > :after_id")
            params["after_id"] = str(after_id)
        if status is not None:
            clauses.append("l.status = :status")
            params["status"] = status
        if query:
            clauses.append(
                """
                (
                    l.canonical_address ILIKE :query
                    OR l.first_name ILIKE :query
                    OR l.last_name ILIKE :query
                    OR l.company ILIKE :query
                )
                """
            )
            params["query"] = f"%{query}%"
        if list_id is not None:
            clauses.append(
                """
                EXISTS (
                    SELECT 1
                    FROM lead_list_memberships llm_filter
                    WHERE llm_filter.workspace_id = l.workspace_id
                      AND llm_filter.lead_id = l.id
                      AND llm_filter.list_id = :list_id
                )
                """
            )
            params["list_id"] = str(list_id)

        where_sql = " AND ".join(clauses)
        return cast(
            Sequence[Mapping[str, Any]],
            self.session.execute(
                text(
                    f"""
                    SELECT l.*,
                           COALESCE(COUNT(llm.list_id), 0)::integer AS list_count
                    FROM leads l
                    LEFT JOIN lead_list_memberships llm
                      ON llm.workspace_id = l.workspace_id
                     AND llm.lead_id = l.id
                    WHERE {where_sql}
                    GROUP BY l.id
                    ORDER BY l.id ASC
                    LIMIT :limit
                    """
                ),
                params,
            )
            .mappings()
            .all()
        )

    def get_lead(
        self, *, workspace_id: UUID, lead_id: UUID
    ) -> Mapping[str, Any] | None:
        return cast(
            Mapping[str, Any] | None,
            self.session.execute(
                text(
                    """
                    SELECT *
                    FROM leads
                    WHERE workspace_id = :workspace_id
                      AND id = :id
                    """
                ),
                {"workspace_id": str(workspace_id), "id": str(lead_id)},
            )
            .mappings()
            .first()
        )

    def get_lead_lists(
        self, *, workspace_id: UUID, lead_id: UUID
    ) -> Sequence[Mapping[str, Any]]:
        return cast(
            Sequence[Mapping[str, Any]],
            self.session.execute(
                text(
                    """
                    SELECT ll.id, ll.name, ll.archived_at
                    FROM lead_lists ll
                    JOIN lead_list_memberships llm
                      ON llm.workspace_id = ll.workspace_id
                     AND llm.list_id = ll.id
                    WHERE ll.workspace_id = :workspace_id
                      AND llm.lead_id = :lead_id
                    ORDER BY ll.id ASC
                    """
                ),
                {"workspace_id": str(workspace_id), "lead_id": str(lead_id)},
            )
            .mappings()
            .all()
        )

    def update_lead(
        self,
        *,
        workspace_id: UUID,
        lead_id: UUID,
        expected_version: int,
        values: Mapping[str, Any],
    ) -> Mapping[str, Any] | None:
        assignments: list[str] = []
        params: dict[str, Any] = {
            "workspace_id": str(workspace_id),
            "id": str(lead_id),
            "expected_version": expected_version,
        }
        for key, value in values.items():
            if key == "custom_fields":
                assignments.append("custom_fields = CAST(:custom_fields AS jsonb)")
                params[key] = json.dumps(value)
            elif key == "archived_at" and value == "now":
                assignments.append("archived_at = pg_catalog.transaction_timestamp()")
            else:
                assignments.append(f"{key} = :{key}")
                params[key] = value

        return cast(
            Mapping[str, Any] | None,
            self.session.execute(
                text(
                    f"""
                    UPDATE leads
                    SET {', '.join(assignments)}
                    WHERE workspace_id = :workspace_id
                      AND id = :id
                      AND version = :expected_version
                    RETURNING *
                    """
                ),
                params,
            )
            .mappings()
            .first()
        )

    def create_list(self, *, workspace_id: UUID, name: str) -> Mapping[str, Any]:
        return cast(
            Mapping[str, Any],
            self.session.execute(
                text(
                    """
                    INSERT INTO lead_lists (workspace_id, name)
                    VALUES (:workspace_id, :name)
                    RETURNING *
                    """
                ),
                {"workspace_id": str(workspace_id), "name": name},
            )
            .mappings()
            .one()
        )

    def list_lists(
        self, *, workspace_id: UUID, limit: int, after_id: UUID | None
    ) -> Sequence[Mapping[str, Any]]:
        clauses = ["ll.workspace_id = :workspace_id", "ll.archived_at IS NULL"]
        params: dict[str, Any] = {"workspace_id": str(workspace_id), "limit": limit}
        if after_id is not None:
            clauses.append("ll.id > :after_id")
            params["after_id"] = str(after_id)

        return cast(
            Sequence[Mapping[str, Any]],
            self.session.execute(
                text(
                    f"""
                    SELECT ll.*,
                           COALESCE(COUNT(llm.lead_id), 0)::integer AS member_count
                    FROM lead_lists ll
                    LEFT JOIN lead_list_memberships llm
                      ON llm.workspace_id = ll.workspace_id
                     AND llm.list_id = ll.id
                    WHERE {' AND '.join(clauses)}
                    GROUP BY ll.id
                    ORDER BY ll.id ASC
                    LIMIT :limit
                    """
                ),
                params,
            )
            .mappings()
            .all()
        )

    def get_list(
        self, *, workspace_id: UUID, list_id: UUID
    ) -> Mapping[str, Any] | None:
        return cast(
            Mapping[str, Any] | None,
            self.session.execute(
                text(
                    """
                    SELECT ll.*,
                           COALESCE(COUNT(llm.lead_id), 0)::integer AS member_count
                    FROM lead_lists ll
                    LEFT JOIN lead_list_memberships llm
                      ON llm.workspace_id = ll.workspace_id
                     AND llm.list_id = ll.id
                    WHERE ll.workspace_id = :workspace_id
                      AND ll.id = :id
                    GROUP BY ll.id
                    """
                ),
                {"workspace_id": str(workspace_id), "id": str(list_id)},
            )
            .mappings()
            .first()
        )

    def update_list(
        self,
        *,
        workspace_id: UUID,
        list_id: UUID,
        expected_version: int,
        name: str | None = None,
        archive: bool = False,
    ) -> Mapping[str, Any] | None:
        assignments: list[str] = []
        params: dict[str, Any] = {
            "workspace_id": str(workspace_id),
            "id": str(list_id),
            "expected_version": expected_version,
        }
        if name is not None:
            assignments.append("name = :name")
            params["name"] = name
        if archive:
            assignments.append("archived_at = pg_catalog.transaction_timestamp()")
        return cast(
            Mapping[str, Any] | None,
            self.session.execute(
                text(
                    f"""
                    UPDATE lead_lists
                    SET {', '.join(assignments)}
                    WHERE workspace_id = :workspace_id
                      AND id = :id
                      AND version = :expected_version
                    RETURNING *,
                        (
                            SELECT COUNT(*)::integer
                            FROM lead_list_memberships llm
                            WHERE llm.workspace_id = lead_lists.workspace_id
                              AND llm.list_id = lead_lists.id
                        ) AS member_count
                    """
                ),
                params,
            )
            .mappings()
            .first()
        )

    def add_member(
        self, *, workspace_id: UUID, list_id: UUID, lead_id: UUID, actor_id: UUID
    ) -> None:
        self.session.execute(
            text(
                """
                INSERT INTO lead_list_memberships
                    (workspace_id, list_id, lead_id, added_by)
                VALUES
                    (:workspace_id, :list_id, :lead_id, :added_by)
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

    def remove_member(
        self, *, workspace_id: UUID, list_id: UUID, lead_id: UUID
    ) -> None:
        self.session.execute(
            text(
                """
                DELETE FROM lead_list_memberships
                WHERE workspace_id = :workspace_id
                  AND list_id = :list_id
                  AND lead_id = :lead_id
                """
            ),
            {
                "workspace_id": str(workspace_id),
                "list_id": str(list_id),
                "lead_id": str(lead_id),
            },
        )

    def list_members(
        self,
        *,
        workspace_id: UUID,
        list_id: UUID,
        limit: int,
        after_id: UUID | None,
    ) -> Sequence[Mapping[str, Any]]:
        clauses = ["llm.workspace_id = :workspace_id", "llm.list_id = :list_id"]
        params: dict[str, Any] = {
            "workspace_id": str(workspace_id),
            "list_id": str(list_id),
            "limit": limit,
        }
        if after_id is not None:
            clauses.append("llm.lead_id > :after_id")
            params["after_id"] = str(after_id)
        return cast(
            Sequence[Mapping[str, Any]],
            self.session.execute(
                text(
                    f"""
                    SELECT l.*, llm.added_by, llm.added_at
                    FROM lead_list_memberships llm
                    JOIN leads l
                      ON l.workspace_id = llm.workspace_id
                     AND l.id = llm.lead_id
                    WHERE {' AND '.join(clauses)}
                    ORDER BY llm.lead_id ASC
                    LIMIT :limit
                    """
                ),
                params,
            )
            .mappings()
            .all()
        )
