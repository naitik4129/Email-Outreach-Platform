from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.api.deps import WorkspaceContext
from app.core.config import Settings
from app.core.errors import AppError
from app.modules.campaigns.attachment_service import default_storage_client
from app.modules.campaigns.repository import CampaignRepository
from app.modules.imports.storage import StorageError, SupabaseStorageClient
from app.schemas.erasure import ErasureOut

logger = logging.getLogger(__name__)

StorageFactory = Callable[[], SupabaseStorageClient]

#: Imports have no human name, so the confirmation is this fixed word.
IMPORT_CONFIRMATION = "DELETE"

# Message prefix the SQL commands (migration 0033) raise for expected refusals.
_SQL_PREFIX = "erasure:"

_STATUS_FOR_CODE: dict[str, tuple[str, int]] = {
    "forbidden": ("forbidden", 403),
    "not_found": ("not_found", 404),
    "state": ("state_conflict", 409),
    "in_use": ("in_use", 409),
    "in_flight": ("in_flight", 409),
    "recent_sends": ("recent_sends", 409),
    "invalid": ("confirmation_mismatch", 422),
}


#: Database failures that are not one of the command's own refusals but that a user
#: can understand and act on: (code, HTTP status, message). Nothing internal is shown.
_NOT_AVAILABLE = (
    "not_available",
    503,
    "This action isn't available on this server yet because a database update "
    "for it hasn't been applied. Nothing was deleted. Ask an administrator to "
    "apply the latest database update.",
)
_BUSY = (
    "busy",
    409,
    "This took too long or clashed with other activity, so nothing was deleted. "
    "Please try again in a moment.",
)
_PERMISSION = (
    "database_permission",
    500,
    "The database refused this action, so nothing was deleted. "
    "Please contact support.",
)
_STATUS_FOR_SQLSTATE: dict[str, tuple[str, int, str]] = {
    "42883": _NOT_AVAILABLE,  # undefined_function
    "42P01": _NOT_AVAILABLE,  # undefined_table
    "42703": _NOT_AVAILABLE,  # undefined_column
    "42501": _PERMISSION,  # insufficient_privilege
    "57014": _BUSY,  # query_canceled (statement timeout)
    "55P03": _BUSY,  # lock_not_available
    "40P01": _BUSY,  # deadlock_detected
    "40001": _BUSY,  # serialization_failure
}


def _translate(exc: DBAPIError) -> AppError | None:
    """Turn a command failure into a client-safe error; None for anything else."""
    orig = getattr(exc, "orig", None)
    diag = getattr(orig, "diag", None)
    primary = getattr(diag, "message_primary", None) or ""
    if not primary.startswith(_SQL_PREFIX):
        sqlstate = getattr(orig, "sqlstate", None) or getattr(diag, "sqlstate", None)
        known = _STATUS_FOR_SQLSTATE.get(sqlstate or "")
        if known is None:
            return None
        # Server log only (never sent to the client): the database's own words
        # and the object involved, so an operator can find the real cause.
        logger.warning(
            "erasure command failed: sqlstate=%s message=%r table=%s constraint=%s",
            sqlstate,
            primary[:300],
            getattr(diag, "table_name", None),
            getattr(diag, "constraint_name", None),
        )
        code, status, message = known
        return AppError(code, message, status_code=status)
    code, status = _STATUS_FOR_CODE.get(
        primary[len(_SQL_PREFIX) :], ("erasure_refused", 409)
    )
    detail = getattr(diag, "message_detail", None)
    message = detail or {
        "forbidden": "You do not have permission to do this",
        "not_found": "Not found",
    }.get(code, "This cannot be done right now")
    return AppError(code, message, status_code=status)


class ErasureService:
    """Runs the purge / erase / delete commands (ADR-0015) and the file cleanup.

    The rules themselves (who may, what blocks it, what is removed or redacted) are
    in the database commands so a worker or script cannot bypass them; this class
    adds the typed confirmation, translates refusals, and removes stored files only
    after the database change has committed.
    """

    def __init__(
        self,
        session: Session,
        *,
        imports_storage: StorageFactory | None = None,
        attachments_storage: StorageFactory | None = None,
    ) -> None:
        self.session = session
        self._imports_storage = imports_storage or (
            lambda: SupabaseStorageClient(Settings.current())
        )
        self._attachments_storage = attachments_storage or default_storage_client

    # -- public commands ---------------------------------------------------

    def purge_campaign(
        self, context: WorkspaceContext, campaign_id: UUID, confirm: str
    ) -> ErasureOut:
        self._confirm(context, "campaigns", "name", campaign_id, confirm, "Campaign")
        result = self._run("app_delete_campaign", context, campaign_id)
        # Attachment files can be shared with a duplicated campaign: only the
        # ones nothing else references are removed.
        repo = CampaignRepository(self.session)
        orphaned = [
            key
            for key in result.get("storage_keys", [])
            if repo.count_storage_key_references(
                workspace_id=context.workspace_id, storage_key=key
            )
            == 0
        ]
        return self._finish(
            "campaign.delete",
            campaign_id,
            result.get("deleted", {}),
            [(self._attachments_storage, orphaned)],
        )

    def erase_campaign(
        self, context: WorkspaceContext, campaign_id: UUID, confirm: str
    ) -> ErasureOut:
        self._confirm(context, "campaigns", "name", campaign_id, confirm, "Campaign")
        result = self._run("app_erase_campaign", context, campaign_id)
        return self._finish("campaign.erase", campaign_id, result.get("redacted", {}))

    def erase_lead(
        self, context: WorkspaceContext, lead_id: UUID, confirm: str
    ) -> ErasureOut:
        self._confirm(
            context, "leads", "original_address", lead_id, confirm, "Lead"
        )
        result = self._run("app_erase_lead", context, lead_id)
        return self._finish("lead.erase", lead_id, result.get("redacted", {}))

    def purge_template(
        self, context: WorkspaceContext, template_id: UUID, confirm: str
    ) -> ErasureOut:
        self._confirm(context, "templates", "name", template_id, confirm, "Template")
        result = self._run("app_purge_template", context, template_id)
        return self._finish("template.purge", template_id, result.get("deleted", {}))

    def purge_lead_list(
        self, context: WorkspaceContext, list_id: UUID, confirm: str
    ) -> ErasureOut:
        self._confirm(context, "lead_lists", "name", list_id, confirm, "Lead list")
        result = self._run("app_purge_lead_list", context, list_id)
        return self._finish("lead_list.purge", list_id, result.get("deleted", {}))

    def purge_import(
        self, context: WorkspaceContext, import_id: UUID, confirm: str
    ) -> ErasureOut:
        exists = self.session.execute(
            text(
                "SELECT 1 FROM import_jobs WHERE workspace_id = :ws AND id = :id"
            ),
            {"ws": str(context.workspace_id), "id": str(import_id)},
        ).first()
        if exists is None:
            raise AppError("not_found", "Import not found", status_code=404)
        if confirm.strip() != IMPORT_CONFIRMATION:
            raise self._mismatch()
        result = self._run("app_purge_import", context, import_id)
        return self._finish(
            "import.purge",
            import_id,
            result.get("deleted", {}),
            [(self._imports_storage, result.get("storage_keys", []))],
        )

    def purge_mailbox(
        self, context: WorkspaceContext, mailbox_id: UUID, confirm: str
    ) -> ErasureOut:
        self._confirm(
            context, "mailboxes", "original_address", mailbox_id, confirm, "Mailbox"
        )
        result = self._run("app_delete_mailbox", context, mailbox_id)
        return self._finish("mailbox.delete", mailbox_id, result.get("deleted", {}))

    def purge_workspace(self, context: WorkspaceContext, confirm: str) -> ErasureOut:
        """Permanently deletes the workspace and every row it owns. Irreversible.

        Doesn't reuse _confirm/_run: those assume a "workspace_id + id" row
        inside the workspace, but here the workspace itself is the target, so
        there is no workspace_id column to filter by.
        """
        row = self.session.execute(
            text("SELECT name FROM workspaces WHERE id = :id"),
            {"id": str(context.workspace_id)},
        ).first()
        if row is None:
            raise AppError("not_found", "Workspace not found", status_code=404)
        if confirm.strip() != str(row[0]).strip():
            raise self._mismatch()

        try:
            with self.session.begin_nested():
                value = self.session.execute(
                    text("SELECT public.app_delete_workspace(:ws, :confirm)"),
                    {"ws": str(context.workspace_id), "confirm": confirm},
                ).scalar_one()
        except DBAPIError as exc:
            translated = _translate(exc)
            if translated is None:
                raise
            raise translated from exc

        result = dict(value or {})
        return self._finish(
            "workspace.delete", context.workspace_id, result.get("deleted", {})
        )

    # -- internals ----------------------------------------------------------

    @staticmethod
    def _mismatch() -> AppError:
        return AppError(
            "confirmation_mismatch",
            "The confirmation text does not match",
            status_code=422,
        )

    def _confirm(
        self,
        context: WorkspaceContext,
        table: str,
        column: str,
        target_id: UUID,
        confirm: str,
        label: str,
    ) -> None:
        # table/column are fixed literals from this module, never request input.
        row = self.session.execute(
            text(
                f"SELECT {column} FROM {table} "  # noqa: S608
                "WHERE workspace_id = :ws AND id = :id"
            ),
            {"ws": str(context.workspace_id), "id": str(target_id)},
        ).first()
        if row is None:
            raise AppError("not_found", f"{label} not found", status_code=404)
        if confirm.strip() != str(row[0]).strip():
            raise self._mismatch()

    def _run(
        self, function: str, context: WorkspaceContext, target_id: UUID
    ) -> dict[str, Any]:
        try:
            with self.session.begin_nested():
                value = self.session.execute(
                    text(f"SELECT public.{function}(:ws, :id)"),  # noqa: S608
                    {"ws": str(context.workspace_id), "id": str(target_id)},
                ).scalar_one()
        except DBAPIError as exc:
            translated = _translate(exc)
            if translated is None:
                raise
            raise translated from exc
        return dict(value or {})

    def _finish(
        self,
        operation: str,
        target_id: UUID,
        details: dict[str, Any],
        files: list[tuple[StorageFactory, list[str]]] | None = None,
    ) -> ErasureOut:
        # Commit first: a file must never be removed for a change that then
        # rolls back. From here on nothing else touches the database.
        self.session.commit()
        pending = 0
        for factory, keys in files or []:
            if not keys:
                continue
            client = factory()
            for key in keys:
                try:
                    client.delete_object(key)
                except StorageError:
                    pending += 1
                    logger.warning(
                        "erasure file cleanup failed", extra={"operation": operation}
                    )
        logger.info(
            "erasure completed",
            extra={"operation": operation, "target_id": str(target_id)},
        )
        return ErasureOut(
            operation=operation,
            target_id=target_id,
            details=details,
            files_pending_cleanup=pending,
        )
