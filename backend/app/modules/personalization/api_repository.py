"""SQL for the personalization API. Runs as app_api (RLS + column grants of
migrations 0026-0029); every statement is workspace-scoped."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import RowMapping, text
from sqlalchemy.orm import Session


class PersonalizationApiRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    # -- usage / budget ---------------------------------------------------

    def usage_today(self, *, workspace_id: UUID, day: date) -> dict[str, int]:
        rows = self.session.execute(
            text(
                """
                SELECT kind, units FROM personalization_usage_daily
                WHERE workspace_id = :workspace_id AND usage_day = :day
                """
            ),
            {"workspace_id": str(workspace_id), "day": day},
        ).all()
        return {str(kind): int(units) for kind, units in rows}

    # -- previews ---------------------------------------------------------

    def insert_previews(
        self,
        *,
        workspace_id: UUID,
        campaign_id: UUID,
        batch_id: UUID,
        config_digest: str,
        created_by: UUID,
        expires_at: datetime,
        rows: list[dict[str, str]],
    ) -> None:
        """rows: dicts with audience_member_id, lead_id, step_id. ON CONFLICT DO
        NOTHING on the batch identity makes a retried request converge."""
        self.session.execute(
            text(
                """
                INSERT INTO personalization_previews
                    (id, workspace_id, campaign_id, batch_id, audience_member_id,
                     lead_id, step_id, config_digest, created_by, expires_at)
                VALUES
                    (:id, :workspace_id, :campaign_id, :batch_id, :audience_member_id,
                     :lead_id, :step_id, :config_digest, :created_by, :expires_at)
                ON CONFLICT (workspace_id, batch_id, audience_member_id, step_id)
                DO NOTHING
                """
            ),
            [
                {
                    "id": str(uuid.uuid4()),
                    "workspace_id": str(workspace_id),
                    "campaign_id": str(campaign_id),
                    "batch_id": str(batch_id),
                    "config_digest": config_digest,
                    "created_by": str(created_by),
                    "expires_at": expires_at,
                    **row,
                }
                for row in rows
            ],
        )

    def list_batch(
        self, *, workspace_id: UUID, campaign_id: UUID, batch_id: UUID
    ) -> list[RowMapping]:
        return list(
            self.session.execute(
                text(
                    """
                    SELECT p.id, p.audience_member_id, p.lead_id, p.step_id,
                           p.config_digest, p.state, p.subject, p.body_html, p.facts,
                           p.research_summary, p.fallback_used, p.failure_codes,
                           p.created_at, p.expires_at,
                           ss.position AS step_position,
                           am.frozen_variables
                    FROM personalization_previews p
                    JOIN sequence_steps ss
                      ON ss.workspace_id = p.workspace_id AND ss.id = p.step_id
                    JOIN campaign_audience_members am
                      ON am.workspace_id = p.workspace_id AND am.id = p.audience_member_id
                    WHERE p.workspace_id = :workspace_id AND p.campaign_id = :campaign_id
                      AND p.batch_id = :batch_id
                    ORDER BY p.audience_member_id, ss.position
                    """
                ),
                {
                    "workspace_id": str(workspace_id),
                    "campaign_id": str(campaign_id),
                    "batch_id": str(batch_id),
                },
            )
            .mappings()
            .all()
        )

    def latest_batch_id(self, *, workspace_id: UUID, campaign_id: UUID) -> UUID | None:
        row = self.session.execute(
            text(
                """
                SELECT batch_id FROM personalization_previews
                WHERE workspace_id = :workspace_id AND campaign_id = :campaign_id
                ORDER BY created_at DESC, id DESC
                LIMIT 1
                """
            ),
            {"workspace_id": str(workspace_id), "campaign_id": str(campaign_id)},
        ).first()
        return UUID(str(row[0])) if row else None

    # -- approvals --------------------------------------------------------

    def insert_approval(
        self,
        *,
        workspace_id: UUID,
        campaign_id: UUID,
        config_digest: str,
        batch_id: UUID,
        approved_by: UUID,
    ) -> None:
        self.session.execute(
            text(
                """
                INSERT INTO campaign_personalization_approvals
                    (id, workspace_id, campaign_id, config_digest, batch_id, approved_by)
                VALUES
                    (:id, :workspace_id, :campaign_id, :config_digest, :batch_id,
                     :approved_by)
                ON CONFLICT (workspace_id, campaign_id, config_digest) DO NOTHING
                """
            ),
            {
                "id": str(uuid.uuid4()),
                "workspace_id": str(workspace_id),
                "campaign_id": str(campaign_id),
                "config_digest": config_digest,
                "batch_id": str(batch_id),
                "approved_by": str(approved_by),
            },
        )

    def list_approvals(
        self, *, workspace_id: UUID, campaign_id: UUID
    ) -> list[RowMapping]:
        return list(
            self.session.execute(
                text(
                    """
                    SELECT config_digest, approved_at, approved_by
                    FROM campaign_personalization_approvals
                    WHERE workspace_id = :workspace_id AND campaign_id = :campaign_id
                    ORDER BY approved_at DESC
                    """
                ),
                {"workspace_id": str(workspace_id), "campaign_id": str(campaign_id)},
            )
            .mappings()
            .all()
        )

    # -- generation progress ---------------------------------------------

    def progress(self, *, workspace_id: UUID, campaign_id: UUID) -> dict[str, Any]:
        counts = (
            self.session.execute(
                text(
                    """
                    SELECT
                        COUNT(*) FILTER (WHERE state = 'PENDING') AS pending,
                        COUNT(*) FILTER (WHERE state = 'SUCCEEDED') AS succeeded,
                        COUNT(*) FILTER (WHERE state = 'FAILED') AS failed,
                        COUNT(*) FILTER (WHERE state = 'SUPERSEDED') AS superseded,
                        COUNT(*) FILTER (WHERE fallback_used) AS fallback,
                        MIN(next_attempt_at) FILTER (WHERE state = 'PENDING')
                            AS oldest_pending_at
                    FROM message_generations
                    WHERE workspace_id = :workspace_id AND campaign_id = :campaign_id
                    """
                ),
                {"workspace_id": str(workspace_id), "campaign_id": str(campaign_id)},
            )
            .mappings()
            .one()
        )
        codes = self.session.execute(
            text(
                """
                SELECT failure_code, COUNT(*) FROM message_generations
                WHERE workspace_id = :workspace_id AND campaign_id = :campaign_id
                  AND state = 'FAILED' AND failure_code IS NOT NULL
                GROUP BY failure_code
                """
            ),
            {"workspace_id": str(workspace_id), "campaign_id": str(campaign_id)},
        ).all()
        return {
            "pending": int(counts["pending"]),
            "succeeded": int(counts["succeeded"]),
            "failed": int(counts["failed"]),
            "superseded": int(counts["superseded"]),
            "fallback": int(counts["fallback"]),
            "oldest_pending_at": counts["oldest_pending_at"],
            "failure_codes": {str(code): int(n) for code, n in codes},
        }
