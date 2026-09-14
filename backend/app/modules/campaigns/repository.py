from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from typing import Any
from uuid import UUID

from sqlalchemy import RowMapping, text
from sqlalchemy.orm import Session

from app.core.errors import AppError

# Large enough that no sequence in practice has this many steps, small enough
# to keep as a plain bigint. Used as a two-phase temporary offset so a bulk
# reposition never collides with the (workspace_id, sequence_id, position)
# unique constraint or the campaign_mailboxes-equivalent allocation ordering,
# without ever setting position <= 0 (sequence_steps_position_check forbids
# that even transiently -- CHECK constraints are not deferrable here).
_POSITION_TEMP_OFFSET = 1_000_000


class CampaignRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    # -------------------------------------------------------------------
    # Campaigns
    # -------------------------------------------------------------------

    def create_campaign(
        self,
        *,
        workspace_id: UUID,
        name: str,
        description: str | None,
        creator_id: UUID,
    ) -> RowMapping:
        campaign_id = uuid.uuid4()
        row = (
            self.session.execute(
                text(
                    """
                    INSERT INTO campaigns
                        (id, workspace_id, name, description, creator_id, status)
                    VALUES
                        (:id, :workspace_id, :name, :description, :creator_id, 'DRAFT')
                    RETURNING *
                    """
                ),
                {
                    "id": str(campaign_id),
                    "workspace_id": str(workspace_id),
                    "name": name,
                    "description": description,
                    "creator_id": str(creator_id),
                },
            )
            .mappings()
            .one()
        )
        self._record_audit_event(
            workspace_id=workspace_id,
            actor_id=creator_id,
            action="campaign.create",
            target_id=campaign_id,
            after_state={"name": name, "status": "DRAFT"},
        )
        return row

    def get_campaign(
        self,
        *,
        workspace_id: UUID,
        campaign_id: UUID,
        for_update: bool = False,
    ) -> RowMapping | None:
        clause = "FOR UPDATE" if for_update else ""
        row = (
            self.session.execute(
                text(
                    f"""
                    SELECT *
                    FROM campaigns
                    WHERE workspace_id = :workspace_id AND id = :campaign_id
                    {clause}
                    """
                ),
                {"workspace_id": str(workspace_id), "campaign_id": str(campaign_id)},
            )
            .mappings()
            .first()
        )
        return row

    def list_campaigns(
        self,
        *,
        workspace_id: UUID,
        limit: int,
        after_id: UUID | None,
        status: str | None,
        query: str | None,
    ) -> Sequence[RowMapping]:
        clauses = ["workspace_id = :workspace_id"]
        params: dict[str, Any] = {"workspace_id": str(workspace_id), "limit": limit}
        if after_id is not None:
            clauses.append("id > :after_id")
            params["after_id"] = str(after_id)
        if status:
            clauses.append("status = :status")
            params["status"] = status
        if query:
            clauses.append("name ILIKE :query_pattern")
            params["query_pattern"] = f"%{query}%"
        where_sql = " AND ".join(clauses)
        rows = (
            self.session.execute(
                text(
                    f"""
                    SELECT *
                    FROM campaigns
                    WHERE {where_sql}
                    ORDER BY id ASC
                    LIMIT :limit
                    """
                ),
                params,
            )
            .mappings()
            .all()
        )
        return rows

    def update_campaign(
        self,
        *,
        workspace_id: UUID,
        campaign_id: UUID,
        expected_version: int,
        name: str | None,
        description: str | None,
        description_provided: bool,
        actor_id: UUID,
    ) -> RowMapping | None:
        existing = self.get_campaign(
            workspace_id=workspace_id, campaign_id=campaign_id, for_update=True
        )
        if existing is None:
            return None
        if existing["status"] not in ("DRAFT",):
            raise AppError(
                "state_conflict",
                "Only DRAFT campaigns can be edited",
                status_code=409,
            )
        if existing["version"] != expected_version:
            raise AppError(
                "conflict",
                "Campaign was modified by another user. Please refresh and try again.",
                status_code=409,
            )

        new_name = name if name is not None else existing["name"]
        new_description = (
            description if description_provided else existing["description"]
        )

        row = (
            self.session.execute(
                text(
                    """
                    UPDATE campaigns
                    SET name = :name, description = :description
                    WHERE workspace_id = :workspace_id AND id = :campaign_id
                      AND version = :expected_version
                    RETURNING *
                    """
                ),
                {
                    "workspace_id": str(workspace_id),
                    "campaign_id": str(campaign_id),
                    "name": new_name,
                    "description": new_description,
                    "expected_version": expected_version,
                },
            )
            .mappings()
            .first()
        )
        if row is None:
            raise AppError(
                "conflict",
                "Campaign was concurrently updated. Please refresh and try again.",
                status_code=409,
            )
        self._record_audit_event(
            workspace_id=workspace_id,
            actor_id=actor_id,
            action="campaign.update",
            target_id=campaign_id,
            before_state={"name": existing["name"], "version": existing["version"]},
            after_state={"name": new_name, "version": row["version"]},
        )
        return row

    def archive_campaign(
        self,
        *,
        workspace_id: UUID,
        campaign_id: UUID,
        expected_version: int,
        actor_id: UUID,
    ) -> RowMapping | None:
        existing = self.get_campaign(
            workspace_id=workspace_id, campaign_id=campaign_id, for_update=True
        )
        if existing is None:
            return None
        if existing["status"] == "ARCHIVED":
            return existing
        if existing["status"] != "DRAFT":
            raise AppError(
                "state_conflict",
                "Only DRAFT campaigns can be archived in this phase",
                status_code=409,
            )
        if existing["version"] != expected_version:
            raise AppError(
                "conflict",
                "Campaign was modified by another user. Please refresh and try again.",
                status_code=409,
            )
        row = (
            self.session.execute(
                text(
                    """
                    UPDATE campaigns
                    SET status = 'ARCHIVED',
                        archived_at = pg_catalog.transaction_timestamp()
                    WHERE workspace_id = :workspace_id AND id = :campaign_id
                      AND version = :expected_version
                    RETURNING *
                    """
                ),
                {
                    "workspace_id": str(workspace_id),
                    "campaign_id": str(campaign_id),
                    "expected_version": expected_version,
                },
            )
            .mappings()
            .first()
        )
        if row is None:
            raise AppError(
                "conflict",
                "Campaign was concurrently updated. Please refresh and try again.",
                status_code=409,
            )
        self._record_audit_event(
            workspace_id=workspace_id,
            actor_id=actor_id,
            action="campaign.archive",
            target_id=campaign_id,
            after_state={"status": "ARCHIVED"},
        )
        return row

    def set_draft_sequence_id(
        self, *, workspace_id: UUID, campaign_id: UUID, sequence_id: UUID
    ) -> None:
        self.session.execute(
            text(
                """
                UPDATE campaigns
                SET draft_sequence_id = :sequence_id
                WHERE workspace_id = :workspace_id AND id = :campaign_id
                  AND draft_sequence_id IS NULL
                """
            ),
            {
                "workspace_id": str(workspace_id),
                "campaign_id": str(campaign_id),
                "sequence_id": str(sequence_id),
            },
        )

    def set_current_settings_id(
        self, *, workspace_id: UUID, campaign_id: UUID, settings_id: UUID
    ) -> None:
        self.session.execute(
            text(
                """
                UPDATE campaigns
                SET current_settings_id = :settings_id
                WHERE workspace_id = :workspace_id AND id = :campaign_id
                """
            ),
            {
                "workspace_id": str(workspace_id),
                "campaign_id": str(campaign_id),
                "settings_id": str(settings_id),
            },
        )

    def commit_draft_audience(
        self, *, workspace_id: UUID, campaign_id: UUID, audience_id: UUID
    ) -> RowMapping | None:
        row = (
            self.session.execute(
                text(
                    """
                    UPDATE campaigns
                    SET draft_audience_id = :audience_id
                    WHERE workspace_id = :workspace_id AND id = :campaign_id
                      AND status = 'DRAFT'
                      AND EXISTS (
                          SELECT 1 FROM campaign_audiences a
                          WHERE a.workspace_id = :workspace_id
                            AND a.campaign_id = :campaign_id
                            AND a.id = :audience_id
                            AND a.status = 'READY'
                      )
                    RETURNING *
                    """
                ),
                {
                    "workspace_id": str(workspace_id),
                    "campaign_id": str(campaign_id),
                    "audience_id": str(audience_id),
                },
            )
            .mappings()
            .first()
        )
        return row

    # -------------------------------------------------------------------
    # Sequence + steps
    # -------------------------------------------------------------------

    def get_sequence(
        self, *, workspace_id: UUID, campaign_id: UUID
    ) -> RowMapping | None:
        row = (
            self.session.execute(
                text(
                    """
                    SELECT * FROM campaign_sequences
                    WHERE workspace_id = :workspace_id AND campaign_id = :campaign_id
                    ORDER BY revision DESC
                    LIMIT 1
                    """
                ),
                {"workspace_id": str(workspace_id), "campaign_id": str(campaign_id)},
            )
            .mappings()
            .first()
        )
        return row

    def create_sequence(self, *, workspace_id: UUID, campaign_id: UUID) -> RowMapping:
        sequence_id = uuid.uuid4()
        row = (
            self.session.execute(
                text(
                    """
                    INSERT INTO campaign_sequences
                        (id, workspace_id, campaign_id, revision, status)
                    VALUES
                        (:id, :workspace_id, :campaign_id, 1, 'DRAFT')
                    RETURNING *
                    """
                ),
                {
                    "id": str(sequence_id),
                    "workspace_id": str(workspace_id),
                    "campaign_id": str(campaign_id),
                },
            )
            .mappings()
            .one()
        )
        self.set_draft_sequence_id(
            workspace_id=workspace_id, campaign_id=campaign_id, sequence_id=sequence_id
        )
        return row

    def list_steps_ordered(
        self, *, workspace_id: UUID, sequence_id: UUID
    ) -> list[RowMapping]:
        rows = (
            self.session.execute(
                text(
                    """
                    SELECT * FROM sequence_steps
                    WHERE workspace_id = :workspace_id AND sequence_id = :sequence_id
                    ORDER BY position ASC
                    """
                ),
                {"workspace_id": str(workspace_id), "sequence_id": str(sequence_id)},
            )
            .mappings()
            .all()
        )
        return list(rows)

    def get_step(
        self, *, workspace_id: UUID, step_id: UUID, for_update: bool = False
    ) -> RowMapping | None:
        clause = "FOR UPDATE" if for_update else ""
        row = (
            self.session.execute(
                text(
                    f"""
                    SELECT * FROM sequence_steps
                    WHERE workspace_id = :workspace_id AND id = :step_id
                    {clause}
                    """
                ),
                {"workspace_id": str(workspace_id), "step_id": str(step_id)},
            )
            .mappings()
            .first()
        )
        return row

    def _apply_full_reorder(
        self,
        *,
        workspace_id: UUID,
        sequence_id: UUID,
        ordering: list[tuple[UUID, int]],
    ) -> None:
        """Two-phase reposition: temp-shift all touched rows, then apply final
        positions individually. Avoids colliding with the (workspace_id,
        sequence_id, position) unique constraint at any intermediate step,
        without ever needing position <= 0 (which the CHECK constraint
        forbids even transiently)."""
        if not ordering:
            return
        ids = [str(step_id) for step_id, _ in ordering]
        self.session.execute(
            text(
                """
                UPDATE sequence_steps
                SET position = position + :offset
                WHERE workspace_id = :workspace_id AND sequence_id = :sequence_id
                  AND id = ANY(:ids)
                """
            ),
            {
                "workspace_id": str(workspace_id),
                "sequence_id": str(sequence_id),
                "ids": ids,
                "offset": _POSITION_TEMP_OFFSET,
            },
        )
        for step_id, new_position in ordering:
            self.session.execute(
                text(
                    """
                    UPDATE sequence_steps
                    SET position = :position
                    WHERE workspace_id = :workspace_id AND id = :step_id
                    """
                ),
                {
                    "workspace_id": str(workspace_id),
                    "step_id": str(step_id),
                    "position": new_position,
                },
            )

    def insert_step(
        self,
        *,
        workspace_id: UUID,
        sequence_id: UUID,
        campaign_id: UUID,
        position: int,
        kind: str,
        email_subject: str | None,
        email_body_html: str | None,
        email_variable_schema: dict[str, Any] | None,
        wait_duration_minutes: int | None,
        source_template_version_id: UUID | None,
    ) -> RowMapping:
        existing = self.list_steps_ordered(
            workspace_id=workspace_id, sequence_id=sequence_id
        )
        target_position = max(1, min(position, len(existing) + 1))

        shift_ordering = [
            (UUID(str(row["id"])), row["position"] + 1)
            for row in existing
            if row["position"] >= target_position
        ]
        self._apply_full_reorder(
            workspace_id=workspace_id, sequence_id=sequence_id, ordering=shift_ordering
        )

        step_id = uuid.uuid4()
        row = (
            self.session.execute(
                text(
                    """
                    INSERT INTO sequence_steps
                        (id, workspace_id, sequence_id, campaign_id, position, kind,
                         email_subject, email_body_html, email_variable_schema,
                         wait_duration_minutes, source_template_version_id)
                    VALUES
                        (:id, :workspace_id, :sequence_id, :campaign_id, :position,
                         :kind,
                         :email_subject, :email_body_html,
                         CAST(:email_variable_schema AS jsonb),
                         :wait_duration_minutes, :source_template_version_id)
                    RETURNING *
                    """
                ),
                {
                    "id": str(step_id),
                    "workspace_id": str(workspace_id),
                    "sequence_id": str(sequence_id),
                    "campaign_id": str(campaign_id),
                    "position": target_position,
                    "kind": kind,
                    "email_subject": email_subject,
                    "email_body_html": email_body_html,
                    "email_variable_schema": (
                        json.dumps(email_variable_schema)
                        if email_variable_schema is not None
                        else None
                    ),
                    "wait_duration_minutes": wait_duration_minutes,
                    "source_template_version_id": (
                        str(source_template_version_id)
                        if source_template_version_id
                        else None
                    ),
                },
            )
            .mappings()
            .one()
        )
        return row

    def update_step(
        self,
        *,
        workspace_id: UUID,
        step_id: UUID,
        expected_version: int,
        email_subject: str | None,
        email_body_html: str | None,
        email_variable_schema: dict[str, Any] | None,
        email_variable_schema_provided: bool,
        wait_duration_minutes: int | None,
        source_template_version_id: UUID | None,
        source_template_version_id_provided: bool,
    ) -> RowMapping | None:
        existing = self.get_step(
            workspace_id=workspace_id, step_id=step_id, for_update=True
        )
        if existing is None:
            return None
        if existing["version"] != expected_version:
            raise AppError(
                "conflict",
                "Step was modified by another user. Please refresh and try again.",
                status_code=409,
            )

        if existing["kind"] == "EMAIL":
            new_subject = (
                email_subject
                if email_subject is not None
                else existing["email_subject"]
            )
            new_body = (
                email_body_html
                if email_body_html is not None
                else existing["email_body_html"]
            )
            new_schema = (
                email_variable_schema
                if email_variable_schema_provided
                else existing["email_variable_schema"]
            )
            new_template_ref = (
                source_template_version_id
                if source_template_version_id_provided
                else existing["source_template_version_id"]
            )
            row = (
                self.session.execute(
                    text(
                        """
                        UPDATE sequence_steps
                        SET email_subject = :email_subject,
                            email_body_html = :email_body_html,
                            email_variable_schema =
                                CAST(:email_variable_schema AS jsonb),
                            source_template_version_id = :source_template_version_id
                        WHERE workspace_id = :workspace_id AND id = :step_id
                          AND version = :expected_version
                        RETURNING *
                        """
                    ),
                    {
                        "workspace_id": str(workspace_id),
                        "step_id": str(step_id),
                        "expected_version": expected_version,
                        "email_subject": new_subject,
                        "email_body_html": new_body,
                        "email_variable_schema": (
                            json.dumps(new_schema) if new_schema is not None else None
                        ),
                        "source_template_version_id": (
                            str(new_template_ref) if new_template_ref else None
                        ),
                    },
                )
                .mappings()
                .first()
            )
        else:
            new_wait = (
                wait_duration_minutes
                if wait_duration_minutes is not None
                else existing["wait_duration_minutes"]
            )
            row = (
                self.session.execute(
                    text(
                        """
                        UPDATE sequence_steps
                        SET wait_duration_minutes = :wait_duration_minutes
                        WHERE workspace_id = :workspace_id AND id = :step_id
                          AND version = :expected_version
                        RETURNING *
                        """
                    ),
                    {
                        "workspace_id": str(workspace_id),
                        "step_id": str(step_id),
                        "expected_version": expected_version,
                        "wait_duration_minutes": new_wait,
                    },
                )
                .mappings()
                .first()
            )
        if row is None:
            raise AppError(
                "conflict",
                "Step was concurrently updated. Please refresh and try again.",
                status_code=409,
            )
        return row

    def delete_step(
        self, *, workspace_id: UUID, sequence_id: UUID, step_id: UUID
    ) -> bool:
        existing = self.list_steps_ordered(
            workspace_id=workspace_id, sequence_id=sequence_id
        )
        if not any(str(row["id"]) == str(step_id) for row in existing):
            return False
        self.session.execute(
            text(
                """
                DELETE FROM sequence_steps
                WHERE workspace_id = :workspace_id AND id = :step_id
                """
            ),
            {"workspace_id": str(workspace_id), "step_id": str(step_id)},
        )
        remaining = [row for row in existing if str(row["id"]) != str(step_id)]
        ordering = [
            (UUID(str(row["id"])), index + 1) for index, row in enumerate(remaining)
        ]
        self._apply_full_reorder(
            workspace_id=workspace_id, sequence_id=sequence_id, ordering=ordering
        )
        return True

    def reorder_steps(
        self,
        *,
        workspace_id: UUID,
        sequence_id: UUID,
        ordering: list[tuple[UUID, int]],
    ) -> list[RowMapping]:
        self._apply_full_reorder(
            workspace_id=workspace_id, sequence_id=sequence_id, ordering=ordering
        )
        return self.list_steps_ordered(
            workspace_id=workspace_id, sequence_id=sequence_id
        )

    # -------------------------------------------------------------------
    # Settings versions
    # -------------------------------------------------------------------

    def create_settings_version(
        self,
        *,
        workspace_id: UUID,
        campaign_id: UUID,
        timezone: str,
        weekday_set: int,
        window_start_local: Any,
        window_end_local: Any,
        daily_limit: int | None,
    ) -> RowMapping:
        next_revision = self.session.execute(
            text(
                """
                    SELECT COALESCE(MAX(revision), 0) + 1
                    FROM campaign_settings_versions
                    WHERE workspace_id = :workspace_id AND campaign_id = :campaign_id
                    """
            ),
            {"workspace_id": str(workspace_id), "campaign_id": str(campaign_id)},
        ).scalar_one()
        settings_id = uuid.uuid4()
        row = (
            self.session.execute(
                text(
                    """
                    INSERT INTO campaign_settings_versions
                        (id, workspace_id, campaign_id, revision, timezone, weekday_set,
                         window_start_local, window_end_local, daily_limit)
                    VALUES
                        (:id, :workspace_id, :campaign_id, :revision, :timezone,
                         :weekday_set, :window_start_local, :window_end_local,
                         :daily_limit)
                    RETURNING *
                    """
                ),
                {
                    "id": str(settings_id),
                    "workspace_id": str(workspace_id),
                    "campaign_id": str(campaign_id),
                    "revision": next_revision,
                    "timezone": timezone,
                    "weekday_set": weekday_set,
                    "window_start_local": window_start_local,
                    "window_end_local": window_end_local,
                    "daily_limit": daily_limit,
                },
            )
            .mappings()
            .one()
        )
        self.set_current_settings_id(
            workspace_id=workspace_id, campaign_id=campaign_id, settings_id=settings_id
        )
        return row

    def get_settings_version(
        self, *, workspace_id: UUID, settings_id: UUID
    ) -> RowMapping | None:
        row = (
            self.session.execute(
                text(
                    """
                    SELECT * FROM campaign_settings_versions
                    WHERE workspace_id = :workspace_id AND id = :settings_id
                    """
                ),
                {"workspace_id": str(workspace_id), "settings_id": str(settings_id)},
            )
            .mappings()
            .first()
        )
        return row

    def list_settings_versions(
        self, *, workspace_id: UUID, campaign_id: UUID
    ) -> Sequence[RowMapping]:
        rows = (
            self.session.execute(
                text(
                    """
                    SELECT * FROM campaign_settings_versions
                    WHERE workspace_id = :workspace_id AND campaign_id = :campaign_id
                    ORDER BY revision DESC
                    """
                ),
                {"workspace_id": str(workspace_id), "campaign_id": str(campaign_id)},
            )
            .mappings()
            .all()
        )
        return rows

    # -------------------------------------------------------------------
    # Mailbox assignment
    # -------------------------------------------------------------------

    def list_campaign_mailboxes(
        self, *, workspace_id: UUID, campaign_id: UUID
    ) -> Sequence[RowMapping]:
        rows = (
            self.session.execute(
                text(
                    """
                    SELECT cm.mailbox_id, cm.active, cm.allocation_position,
                           m.provider, m.original_address, m.sender_display_name,
                           m.connection_state, m.health_state, m.policy_state,
                           m.policy_reason
                    FROM campaign_mailboxes cm
                    JOIN mailboxes m
                      ON m.workspace_id = cm.workspace_id AND m.id = cm.mailbox_id
                    WHERE cm.workspace_id = :workspace_id
                      AND cm.campaign_id = :campaign_id
                    ORDER BY cm.allocation_position ASC
                    """
                ),
                {"workspace_id": str(workspace_id), "campaign_id": str(campaign_id)},
            )
            .mappings()
            .all()
        )
        return rows

    def insert_campaign_mailbox(
        self,
        *,
        workspace_id: UUID,
        campaign_id: UUID,
        mailbox_id: UUID,
        allocation_position: int,
    ) -> None:
        self.session.execute(
            text(
                """
                INSERT INTO campaign_mailboxes
                    (workspace_id, campaign_id, mailbox_id, active, allocation_position)
                VALUES
                    (:workspace_id, :campaign_id, :mailbox_id, true,
                     :allocation_position)
                ON CONFLICT (workspace_id, campaign_id, mailbox_id) DO NOTHING
                """
            ),
            {
                "workspace_id": str(workspace_id),
                "campaign_id": str(campaign_id),
                "mailbox_id": str(mailbox_id),
                "allocation_position": allocation_position,
            },
        )

    def delete_campaign_mailbox(
        self, *, workspace_id: UUID, campaign_id: UUID, mailbox_id: UUID
    ) -> None:
        self.session.execute(
            text(
                """
                DELETE FROM campaign_mailboxes
                WHERE workspace_id = :workspace_id AND campaign_id = :campaign_id
                  AND mailbox_id = :mailbox_id
                """
            ),
            {
                "workspace_id": str(workspace_id),
                "campaign_id": str(campaign_id),
                "mailbox_id": str(mailbox_id),
            },
        )

    def delete_all_campaign_mailboxes(
        self, *, workspace_id: UUID, campaign_id: UUID
    ) -> None:
        self.session.execute(
            text(
                """
                DELETE FROM campaign_mailboxes
                WHERE workspace_id = :workspace_id AND campaign_id = :campaign_id
                """
            ),
            {"workspace_id": str(workspace_id), "campaign_id": str(campaign_id)},
        )

    # -------------------------------------------------------------------
    # Audience (app_api-permitted parts only -- capture itself is owned by
    # CampaignWorkerRepository under the app_worker_general role)
    # -------------------------------------------------------------------

    def get_existing_list_ids(
        self, *, workspace_id: UUID, list_ids: Sequence[UUID]
    ) -> dict[UUID, int]:
        if not list_ids:
            return {}
        rows = (
            self.session.execute(
                text(
                    """
                    SELECT id, version FROM lead_lists
                    WHERE workspace_id = :workspace_id AND id = ANY(:ids)
                      AND archived_at IS NULL
                    """
                ),
                {
                    "workspace_id": str(workspace_id),
                    "ids": [str(i) for i in list_ids],
                },
            )
            .mappings()
            .all()
        )
        return {UUID(str(row["id"])): int(row["version"]) for row in rows}

    def get_existing_lead_ids(
        self, *, workspace_id: UUID, lead_ids: Sequence[UUID]
    ) -> set[UUID]:
        if not lead_ids:
            return set()
        rows = (
            self.session.execute(
                text(
                    """
                    SELECT id FROM leads
                    WHERE workspace_id = :workspace_id AND id = ANY(:ids)
                    """
                ),
                {
                    "workspace_id": str(workspace_id),
                    "ids": [str(i) for i in lead_ids],
                },
            )
            .mappings()
            .all()
        )
        return {UUID(str(row["id"])) for row in rows}

    def count_candidate_leads(
        self,
        *,
        workspace_id: UUID,
        list_ids: Sequence[UUID],
        lead_ids: Sequence[UUID],
    ) -> int:
        row = (
            self.session.execute(
                text(
                    """
                    SELECT COUNT(DISTINCT lead_id) AS candidate_count
                    FROM (
                        SELECT id AS lead_id FROM leads
                        WHERE workspace_id = :workspace_id AND id = ANY(:lead_ids)
                        UNION
                        SELECT llm.lead_id FROM lead_list_memberships llm
                        WHERE llm.workspace_id = :workspace_id
                          AND llm.list_id = ANY(:list_ids)
                    ) candidates
                    """
                ),
                {
                    "workspace_id": str(workspace_id),
                    "lead_ids": [str(i) for i in lead_ids] or [],
                    "list_ids": [str(i) for i in list_ids] or [],
                },
            )
            .mappings()
            .one()
        )
        return int(row["candidate_count"])

    def get_audience(
        self, *, workspace_id: UUID, campaign_id: UUID, audience_id: UUID
    ) -> RowMapping | None:
        row = (
            self.session.execute(
                text(
                    """
                    SELECT * FROM campaign_audiences
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
            .mappings()
            .first()
        )
        return row

    def get_latest_audience(
        self, *, workspace_id: UUID, campaign_id: UUID
    ) -> RowMapping | None:
        row = (
            self.session.execute(
                text(
                    """
                    SELECT * FROM campaign_audiences
                    WHERE workspace_id = :workspace_id AND campaign_id = :campaign_id
                    ORDER BY revision DESC
                    LIMIT 1
                    """
                ),
                {"workspace_id": str(workspace_id), "campaign_id": str(campaign_id)},
            )
            .mappings()
            .first()
        )
        return row

    def insert_campaign_audience(
        self,
        *,
        workspace_id: UUID,
        campaign_id: UUID,
        selection_manifest: dict[str, Any],
    ) -> RowMapping:
        next_revision = self.session.execute(
            text(
                """
                    SELECT COALESCE(MAX(revision), 0) + 1
                    FROM campaign_audiences
                    WHERE workspace_id = :workspace_id AND campaign_id = :campaign_id
                    """
            ),
            {"workspace_id": str(workspace_id), "campaign_id": str(campaign_id)},
        ).scalar_one()
        audience_id = uuid.uuid4()
        row = (
            self.session.execute(
                text(
                    """
                    INSERT INTO campaign_audiences
                        (id, workspace_id, campaign_id, revision, status,
                         selection_manifest)
                    VALUES
                        (:id, :workspace_id, :campaign_id, :revision, 'CAPTURING',
                         CAST(:selection_manifest AS jsonb))
                    RETURNING *
                    """
                ),
                {
                    "id": str(audience_id),
                    "workspace_id": str(workspace_id),
                    "campaign_id": str(campaign_id),
                    "revision": next_revision,
                    "selection_manifest": json.dumps(selection_manifest),
                },
            )
            .mappings()
            .one()
        )
        return row

    def insert_audience_capture_source(
        self,
        *,
        workspace_id: UUID,
        campaign_id: UUID,
        audience_id: UUID,
        list_id: UUID,
        captured_list_revision: int,
    ) -> None:
        self.session.execute(
            text(
                """
                INSERT INTO audience_capture_sources
                    (workspace_id, campaign_id, audience_id, list_id,
                     captured_list_revision, gate_held)
                VALUES
                    (:workspace_id, :campaign_id, :audience_id, :list_id,
                     :captured_list_revision, true)
                """
            ),
            {
                "workspace_id": str(workspace_id),
                "campaign_id": str(campaign_id),
                "audience_id": str(audience_id),
                "list_id": str(list_id),
                "captured_list_revision": captured_list_revision,
            },
        )

    def insert_capture_planning_job(
        self,
        *,
        workspace_id: UUID,
        campaign_id: UUID,
        audience_id: UUID,
        total_count: int,
    ) -> RowMapping:
        job_id = uuid.uuid4()
        row = (
            self.session.execute(
                text(
                    """
                    INSERT INTO campaign_planning_jobs
                        (id, workspace_id, campaign_id, audience_id, phase, state,
                         total_count, next_due_at)
                    VALUES
                        (:id, :workspace_id, :campaign_id, :audience_id, 'CAPTURE',
                         'PENDING', :total_count, pg_catalog.transaction_timestamp())
                    RETURNING *
                    """
                ),
                {
                    "id": str(job_id),
                    "workspace_id": str(workspace_id),
                    "campaign_id": str(campaign_id),
                    "audience_id": str(audience_id),
                    "total_count": total_count,
                },
            )
            .mappings()
            .one()
        )
        return row

    def get_capture_job_for_audience(
        self, *, workspace_id: UUID, audience_id: UUID
    ) -> RowMapping | None:
        row = (
            self.session.execute(
                text(
                    """
                    SELECT * FROM campaign_planning_jobs
                    WHERE workspace_id = :workspace_id AND audience_id = :audience_id
                      AND phase = 'CAPTURE'
                    """
                ),
                {"workspace_id": str(workspace_id), "audience_id": str(audience_id)},
            )
            .mappings()
            .first()
        )
        return row

    def get_audience_member_counts(
        self, *, workspace_id: UUID, audience_id: UUID
    ) -> dict[str, int]:
        row = (
            self.session.execute(
                text(
                    """
                    SELECT
                        COUNT(*) FILTER (WHERE eligibility_status = 'ACCEPTED')
                            AS accepted,
                        COUNT(*) FILTER (WHERE eligibility_status = 'EXCLUDED')
                            AS excluded
                    FROM campaign_audience_members
                    WHERE workspace_id = :workspace_id AND audience_id = :audience_id
                    """
                ),
                {"workspace_id": str(workspace_id), "audience_id": str(audience_id)},
            )
            .mappings()
            .one()
        )
        return {"accepted": int(row["accepted"]), "excluded": int(row["excluded"])}

    # -------------------------------------------------------------------
    # Audit
    # -------------------------------------------------------------------

    def _record_audit_event(
        self,
        *,
        workspace_id: UUID,
        actor_id: UUID,
        action: str,
        target_id: UUID,
        before_state: dict[str, Any] | None = None,
        after_state: dict[str, Any] | None = None,
    ) -> None:
        self.session.execute(
            text(
                """
                INSERT INTO audit_events
                    (workspace_id, actor_kind, actor_id, action, target_type,
                     target_id, before_state, after_state)
                VALUES
                    (:workspace_id, 'USER', :actor_id, :action, 'campaign',
                     :target_id,
                     CAST(:before_state AS jsonb),
                     CAST(:after_state AS jsonb))
                """
            ),
            {
                "workspace_id": str(workspace_id),
                "actor_id": str(actor_id),
                "action": action,
                "target_id": str(target_id),
                "before_state": json.dumps(before_state) if before_state else None,
                "after_state": json.dumps(after_state) if after_state else None,
            },
        )
