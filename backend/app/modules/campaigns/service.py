from __future__ import annotations

from uuid import UUID

from sqlalchemy import RowMapping
from sqlalchemy.orm import Session

from app.api.deps import WorkspaceContext
from app.core.errors import AppError
from app.modules.campaigns.repository import CampaignRepository
from app.modules.campaigns.schemas import (
    CampaignCreateIn,
    CampaignDetailOut,
    CampaignDuplicateIn,
    CampaignListItem,
    CampaignPage,
    CampaignUpdateIn,
)
from app.modules.leads.pagination import decode_cursor, encode_cursor, normalize_limit

_ALLOWED_STATUS_FILTERS = {
    "DRAFT",
    "SCHEDULED",
    "RUNNING",
    "PAUSED",
    "ERROR",
    "COMPLETED",
    "ARCHIVED",
}


def _validate_name(name: str) -> str:
    cleaned = name.strip()
    if not cleaned:
        raise AppError("validation_error", "Campaign name is required", status_code=422)
    if len(cleaned) > 200:
        raise AppError(
            "validation_error",
            "Campaign name must be 200 characters or fewer",
            status_code=422,
        )
    return cleaned


class CampaignService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = CampaignRepository(session)

    def create_campaign(
        self, context: WorkspaceContext, payload: CampaignCreateIn
    ) -> CampaignDetailOut:
        clean_name = _validate_name(payload.name)
        row = self.repo.create_campaign(
            workspace_id=context.workspace_id,
            name=clean_name,
            description=payload.description,
            creator_id=context.user_id,
        )
        return self._to_detail_out(row)

    def get_campaign(
        self, context: WorkspaceContext, campaign_id: UUID
    ) -> CampaignDetailOut:
        row = self.repo.get_campaign(
            workspace_id=context.workspace_id, campaign_id=campaign_id
        )
        if row is None:
            raise AppError("not_found", "Campaign not found", status_code=404)
        return self._to_detail_out(row)

    def list_campaigns(
        self,
        context: WorkspaceContext,
        *,
        limit: int,
        cursor: str | None,
        query: str | None,
        status: str | None,
    ) -> CampaignPage:
        effective_limit = normalize_limit(limit)
        after_id = decode_cursor(cursor)

        clean_status = status if status in _ALLOWED_STATUS_FILTERS else None
        clean_query = query.strip() if query else None

        rows = self.repo.list_campaigns(
            workspace_id=context.workspace_id,
            limit=effective_limit + 1,
            after_id=after_id,
            status=clean_status,
            query=clean_query,
        )
        has_more = len(rows) > effective_limit
        page_rows = rows[:effective_limit]

        items = [self._to_list_item(row) for row in page_rows]
        next_cursor = (
            encode_cursor(page_rows[-1]["id"]) if has_more and page_rows else None
        )
        return CampaignPage(items=items, next_cursor=next_cursor)

    def update_campaign(
        self, context: WorkspaceContext, campaign_id: UUID, payload: CampaignUpdateIn
    ) -> CampaignDetailOut:
        clean_name = _validate_name(payload.name) if payload.name is not None else None
        row = self.repo.update_campaign(
            workspace_id=context.workspace_id,
            campaign_id=campaign_id,
            expected_version=payload.expected_version,
            name=clean_name,
            description=payload.description,
            description_provided="description" in payload.model_fields_set,
            actor_id=context.user_id,
        )
        if row is None:
            raise AppError("not_found", "Campaign not found", status_code=404)
        return self._to_detail_out(row)

    def archive_campaign(
        self, context: WorkspaceContext, campaign_id: UUID, expected_version: int
    ) -> CampaignDetailOut:
        row = self.repo.archive_campaign(
            workspace_id=context.workspace_id,
            campaign_id=campaign_id,
            expected_version=expected_version,
            actor_id=context.user_id,
        )
        if row is None:
            raise AppError("not_found", "Campaign not found", status_code=404)
        return self._to_detail_out(row)

    def duplicate_campaign(
        self, context: WorkspaceContext, campaign_id: UUID, payload: CampaignDuplicateIn
    ) -> CampaignDetailOut:
        """Copy metadata + sequence steps + mailbox assignments + settings.

        Audience is deliberately never copied -- CAMPAIGN_ENGINE.md requires
        re-selection/re-capture for a fresh outreach intent, and campaign_audiences
        rows cannot be reassigned to a different campaign_id.
        """
        source = self.repo.get_campaign(
            workspace_id=context.workspace_id, campaign_id=campaign_id
        )
        if source is None:
            raise AppError("not_found", "Campaign not found", status_code=404)

        dup_name = (
            _validate_name(payload.name)
            if payload.name
            else f"{source['name']} (Copy)"[:200]
        )
        new_campaign = self.repo.create_campaign(
            workspace_id=context.workspace_id,
            name=dup_name,
            description=source["description"],
            creator_id=context.user_id,
        )
        new_campaign_id = UUID(str(new_campaign["id"]))

        source_sequence = self.repo.get_sequence(
            workspace_id=context.workspace_id, campaign_id=campaign_id
        )
        if source_sequence is not None:
            new_sequence = self.repo.create_sequence(
                workspace_id=context.workspace_id, campaign_id=new_campaign_id
            )
            source_steps = self.repo.list_steps_ordered(
                workspace_id=context.workspace_id,
                sequence_id=UUID(str(source_sequence["id"])),
            )
            for step in source_steps:
                self.repo.insert_step(
                    workspace_id=context.workspace_id,
                    sequence_id=UUID(str(new_sequence["id"])),
                    campaign_id=new_campaign_id,
                    position=step["position"],
                    kind=step["kind"],
                    email_subject=step["email_subject"],
                    email_body_html=step["email_body_html"],
                    email_variable_schema=step["email_variable_schema"],
                    wait_duration_minutes=step["wait_duration_minutes"],
                    source_template_version_id=(
                        UUID(str(step["source_template_version_id"]))
                        if step["source_template_version_id"]
                        else None
                    ),
                )

        source_mailboxes = self.repo.list_campaign_mailboxes(
            workspace_id=context.workspace_id, campaign_id=campaign_id
        )
        for row in source_mailboxes:
            self.repo.insert_campaign_mailbox(
                workspace_id=context.workspace_id,
                campaign_id=new_campaign_id,
                mailbox_id=UUID(str(row["mailbox_id"])),
                allocation_position=row["allocation_position"],
            )

        settings_versions = self.repo.list_settings_versions(
            workspace_id=context.workspace_id, campaign_id=campaign_id
        )
        if settings_versions:
            latest = settings_versions[0]
            self.repo.create_settings_version(
                workspace_id=context.workspace_id,
                campaign_id=new_campaign_id,
                timezone=latest["timezone"],
                weekday_set=latest["weekday_set"],
                window_start_local=latest["window_start_local"],
                window_end_local=latest["window_end_local"],
                daily_limit=latest["daily_limit"],
            )

        return self.get_campaign(context, new_campaign_id)

    def _to_list_item(self, row: RowMapping) -> CampaignListItem:
        return CampaignListItem(
            id=row["id"],
            workspace_id=row["workspace_id"],
            name=row["name"],
            description=row["description"],
            status=row["status"],
            draft_sequence_id=row["draft_sequence_id"],
            draft_audience_id=row["draft_audience_id"],
            current_settings_id=row["current_settings_id"],
            version=row["version"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _to_detail_out(self, row: RowMapping) -> CampaignDetailOut:
        return CampaignDetailOut(
            id=row["id"],
            workspace_id=row["workspace_id"],
            name=row["name"],
            description=row["description"],
            creator_id=row["creator_id"],
            status=row["status"],
            start_at=row["start_at"],
            draft_sequence_id=row["draft_sequence_id"],
            draft_audience_id=row["draft_audience_id"],
            current_settings_id=row["current_settings_id"],
            planning_status=row["planning_status"],
            archived_at=row["archived_at"],
            error_reason=row["error_reason"],
            version=row["version"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
