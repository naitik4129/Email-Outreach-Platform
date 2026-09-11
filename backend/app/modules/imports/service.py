from __future__ import annotations

import hashlib
from collections.abc import Mapping
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.api.deps import WorkspaceContext
from app.core.config import Settings
from app.core.errors import AppError
from app.modules.imports.csv_parsing import decode_csv_bytes, iter_csv_rows, parse_csv_preview
from app.modules.imports.mapping import sanitize_source_filename, validate_mapping
from app.modules.imports.repository import ImportRepository
from app.modules.imports.storage import (
    StorageError,
    StorageObjectCorruptError,
    SupabaseStorageClient,
    map_storage_error,
)
from app.modules.leads.normalization import clean_optional_text, normalize_email
from app.modules.leads.pagination import decode_cursor, encode_cursor, normalize_limit
from app.schemas.imports import (
    ImportCreateIn,
    ImportJobOut,
    ImportJobPage,
    ImportMappingIn,
    ImportRowResultOut,
    ImportRowResultPage,
    ImportUploadOut,
)

_ALLOWED_CONTENT_TYPES = {"text/csv", "application/vnd.ms-excel", "application/csv", "text/plain"}


def _storage_prefix(workspace_id: UUID) -> str:
    return f"workspace/{workspace_id}/imports/"


def _job_out(row: Mapping[str, Any]) -> ImportJobOut:
    return ImportJobOut(
        id=row["id"],
        workspace_id=row["workspace_id"],
        initiator_id=row["initiator_id"],
        import_kind=row["import_kind"],
        mapping=row["mapping"],
        list_id=row["list_id"],
        status=row["status"],
        total_rows=row["total_rows"],
        processed_rows=row["processed_rows"],
        accepted_rows=row["accepted_rows"],
        duplicate_rows=row["duplicate_rows"],
        rejected_rows=row["rejected_rows"],
        failure_summary=row["failure_summary"],
        version=row["version"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _row_result_out(row: Mapping[str, Any]) -> ImportRowResultOut:
    return ImportRowResultOut(
        row_number=row["row_number"],
        status=row["status"],
        lead_id=row["lead_id"],
        suppression_id=row["suppression_id"],
        validation_reason=row["validation_reason"],
    )


class ImportService:
    def __init__(
        self,
        session: Session,
        *,
        settings: Settings | None = None,
        storage: SupabaseStorageClient | None = None,
    ) -> None:
        self.session = session
        self.repo = ImportRepository(session)
        self.settings = settings or Settings.current()
        self.storage = storage or SupabaseStorageClient(self.settings)

    # -- API-facing: upload / confirm / read -------------------------------

    def create_upload(
        self,
        context: WorkspaceContext,
        *,
        content_type: str | None,
        filename: str | None,
        data: bytes,
    ) -> ImportUploadOut:
        if len(data) > self.settings.import_max_file_bytes:
            raise AppError(
                "file_too_large",
                f"File exceeds the maximum size of {self.settings.import_max_file_bytes} bytes",
                status_code=413,
            )
        if content_type is not None and content_type.split(";")[0].strip().lower() not in (
            _ALLOWED_CONTENT_TYPES
        ):
            raise AppError(
                "unsupported_file_type", "File must be a CSV file", status_code=415
            )

        text = decode_csv_bytes(data)
        preview = parse_csv_preview(text, settings=self.settings)

        key = f"{_storage_prefix(context.workspace_id)}{uuid4().hex}.csv"
        try:
            stored = self.storage.upload_object(key, content_type="text/csv", data=data)
        except StorageError as exc:
            raise map_storage_error(exc) from exc

        return ImportUploadOut(
            storage_object_key=stored.key,
            storage_object_version=stored.version,
            storage_object_digest=stored.digest,
            headers=preview.headers,
            sample_rows=preview.sample_rows,
            detected_total_rows=preview.total_rows,
            warnings=preview.warnings,
        )

    def confirm_import(self, context: WorkspaceContext, payload: ImportCreateIn) -> ImportJobOut:
        expected_prefix = _storage_prefix(context.workspace_id)
        if not payload.storage_object_key.startswith(expected_prefix):
            raise AppError("not_found", "Uploaded file not found", status_code=404)

        if payload.list_id is not None:
            if payload.import_kind != "LEADS":
                raise AppError(
                    "invalid_mapping",
                    "Only a LEADS import may target a lead list",
                    status_code=422,
                )
            target_list = self.repo.leads.get_list(
                workspace_id=context.workspace_id, list_id=payload.list_id
            )
            if target_list is None or target_list["archived_at"] is not None:
                raise AppError("not_found", "Lead list not found", status_code=404)

        try:
            data = self.storage.download_object(payload.storage_object_key)
        except StorageError as exc:
            raise map_storage_error(exc) from exc

        digest = hashlib.sha256(data).hexdigest()
        if digest != payload.storage_object_digest:
            raise map_storage_error(StorageObjectCorruptError())

        # Re-derive headers/total_rows from the actual stored bytes -- never
        # trust the client-echoed preview (CLAUDE.md Phase 3 task, "Import
        # Preview"/"Upload Security": backend re-validates before creating
        # any durable state).
        text = decode_csv_bytes(data)
        preview = parse_csv_preview(text, settings=self.settings)
        mapping_columns = validate_mapping(payload.import_kind, payload.mapping, preview.headers)
        source_filename = sanitize_source_filename(payload.mapping.source_filename)
        mapping_json: dict[str, Any] = {"columns": mapping_columns}
        if source_filename is not None:
            mapping_json["source_filename"] = source_filename

        row = self.repo.create_job(
            workspace_id=context.workspace_id,
            initiator_id=context.user_id,
            storage_object_key=payload.storage_object_key,
            storage_object_version=payload.storage_object_version,
            storage_object_digest=payload.storage_object_digest,
            import_kind=payload.import_kind,
            mapping=mapping_json,
            list_id=payload.list_id,
            total_rows=preview.total_rows,
        )
        created = row is not None
        if row is None:
            row = self.repo.get_job_by_storage_key(
                workspace_id=context.workspace_id, storage_object_key=payload.storage_object_key
            )
            if row is None:  # pragma: no cover - defensive, should be unreachable
                raise AppError("internal_error", "Import job could not be created", status_code=500)

        if created:
            self._dispatch_process_task(workspace_id=context.workspace_id, import_id=row["id"])
        return _job_out(row)

    def get_job(self, context: WorkspaceContext, import_id: UUID) -> ImportJobOut:
        row = self.repo.get_job(workspace_id=context.workspace_id, import_id=import_id)
        if row is None:
            raise AppError("import_job_not_found", "Import job not found", status_code=404)
        return _job_out(row)

    def list_jobs(
        self, context: WorkspaceContext, *, limit: int, cursor: str | None
    ) -> ImportJobPage:
        effective_limit = normalize_limit(limit)
        rows = self.repo.list_jobs(
            workspace_id=context.workspace_id,
            limit=effective_limit + 1,
            after_id=decode_cursor(cursor),
        )
        page_rows = rows[:effective_limit]
        next_cursor = (
            encode_cursor(page_rows[-1]["id"]) if len(rows) > effective_limit else None
        )
        return ImportJobPage(items=[_job_out(row) for row in page_rows], next_cursor=next_cursor)

    def list_row_results(
        self, context: WorkspaceContext, import_id: UUID, *, limit: int, cursor: str | None
    ) -> ImportRowResultPage:
        self.get_job(context, import_id)
        effective_limit = normalize_limit(limit)
        after_row_number = None
        if cursor:
            after_row_number = int(decode_cursor(cursor).int) if False else None
        # Row-result pages are cursor-paginated by row_number (an integer),
        # not the UUID cursor used elsewhere; encode/decode it the same way
        # leads.pagination does but keep the payload an int.
        after_row_number = _decode_row_cursor(cursor)
        rows = self.repo.list_row_results(
            workspace_id=context.workspace_id,
            import_id=import_id,
            limit=effective_limit + 1,
            after_row_number=after_row_number,
        )
        page_rows = rows[:effective_limit]
        next_cursor = (
            _encode_row_cursor(page_rows[-1]["row_number"])
            if len(rows) > effective_limit
            else None
        )
        return ImportRowResultPage(
            items=[_row_result_out(row) for row in page_rows], next_cursor=next_cursor
        )

    def _dispatch_process_task(self, *, workspace_id: UUID, import_id: UUID) -> None:
        from workers.celery_app import celery_app

        celery_app.send_task(
            "imports.process_chunk",
            kwargs={"workspace_id": str(workspace_id), "import_id": str(import_id)},
            queue="imports",
        )

    # -- worker-facing: claim / process ------------------------------------

    def claim(
        self, *, workspace_id: UUID, import_id: UUID, lease_owner: str
    ) -> Mapping[str, Any] | None:
        return self.repo.claim_job(
            workspace_id=workspace_id,
            import_id=import_id,
            lease_owner=lease_owner,
            ttl_seconds=self.settings.import_lease_ttl_seconds,
        )

    def fail(self, *, workspace_id: UUID, import_id: UUID, error: AppError) -> None:
        self.repo.fail_job(
            workspace_id=workspace_id, import_id=import_id, failure_summary=error.message
        )

    def download_and_decode(self, storage_object_key: str, expected_digest: str) -> str:
        try:
            data = self.storage.download_object(storage_object_key)
        except StorageError as exc:
            raise map_storage_error(exc) from exc
        if hashlib.sha256(data).hexdigest() != expected_digest:
            raise map_storage_error(StorageObjectCorruptError())
        return decode_csv_bytes(data)

    def process_batch(
        self,
        *,
        workspace_id: UUID,
        import_id: UUID,
        job: Mapping[str, Any],
        batch: list[tuple[int, dict[str, str]]],
        lease_owner: str,
    ) -> bool:
        """Process one bounded batch inside the caller's transaction.

        Returns True once the job has reached a terminal state.
        """
        mapping_columns: dict[str, str] = dict(job["mapping"].get("columns", {}))
        accepted = duplicate = rejected = 0
        last_row_number = job["row_cursor"]
        for row_number, row in batch:
            last_row_number = row_number
            outcome = self._process_row(
                workspace_id=workspace_id,
                import_id=import_id,
                import_kind=job["import_kind"],
                mapping=mapping_columns,
                list_id=job["list_id"],
                initiator_id=job["initiator_id"],
                row_number=row_number,
                row=row,
            )
            if outcome == "ACCEPTED":
                accepted += 1
            elif outcome == "DUPLICATE":
                duplicate += 1
            else:
                rejected += 1

        new_processed = job["processed_rows"] + len(batch)
        new_accepted = job["accepted_rows"] + accepted
        new_duplicate = job["duplicate_rows"] + duplicate
        new_rejected = job["rejected_rows"] + rejected
        total_rows = job["total_rows"] or 0
        finished = len(batch) == 0 or last_row_number >= total_rows

        if finished:
            status = "COMPLETED" if new_rejected == 0 else "COMPLETED_WITH_ERRORS"
            self.repo.finalize_job(
                workspace_id=workspace_id,
                import_id=import_id,
                row_cursor=last_row_number,
                processed_rows=new_processed,
                accepted_rows=new_accepted,
                duplicate_rows=new_duplicate,
                rejected_rows=new_rejected,
                status=status,
            )
        else:
            self.repo.update_progress(
                workspace_id=workspace_id,
                import_id=import_id,
                row_cursor=last_row_number,
                processed_rows=new_processed,
                accepted_rows=new_accepted,
                duplicate_rows=new_duplicate,
                rejected_rows=new_rejected,
                lease_owner=lease_owner,
                ttl_seconds=self.settings.import_lease_ttl_seconds,
            )
        return finished

    def _process_row(
        self,
        *,
        workspace_id: UUID,
        import_id: UUID,
        import_kind: str,
        mapping: dict[str, str],
        list_id: UUID | None,
        initiator_id: UUID,
        row_number: int,
        row: dict[str, str],
    ) -> str:
        email_header = mapping.get("email")
        raw_email = row.get(email_header, "") if email_header else ""
        if not raw_email or not raw_email.strip():
            self.repo.insert_row_result(
                workspace_id=workspace_id,
                import_id=import_id,
                row_number=row_number,
                status="REJECTED",
                validation_reason="missing_email",
            )
            return "REJECTED"
        try:
            normalized = normalize_email(raw_email)
        except AppError:
            self.repo.insert_row_result(
                workspace_id=workspace_id,
                import_id=import_id,
                row_number=row_number,
                status="REJECTED",
                validation_reason="invalid_email",
            )
            return "REJECTED"

        self.repo.leads.ensure_recipient_address(
            workspace_id=workspace_id,
            canonical_address=normalized.canonical,
            original_display=normalized.original,
        )

        if import_kind == "LEADS":
            first_name = clean_optional_text(
                row.get(mapping.get("first_name", ""), None), "First name"
            )
            last_name = clean_optional_text(
                row.get(mapping.get("last_name", ""), None), "Last name"
            )
            company = clean_optional_text(row.get(mapping.get("company", ""), None), "Company")
            title = clean_optional_text(row.get(mapping.get("title", ""), None), "Title")
            lead_id, was_new = self.repo.upsert_lead(
                workspace_id=workspace_id,
                original_address=normalized.original,
                canonical_address=normalized.canonical,
                first_name=first_name,
                last_name=last_name,
                company=company,
                title=title,
            )
            if list_id is not None:
                self.repo.add_member_if_missing(
                    workspace_id=workspace_id,
                    list_id=list_id,
                    lead_id=lead_id,
                    actor_id=initiator_id,
                )
            status = "ACCEPTED" if was_new else "DUPLICATE"
            self.repo.insert_row_result(
                workspace_id=workspace_id,
                import_id=import_id,
                row_number=row_number,
                status=status,
                lead_id=lead_id,
            )
            return status

        address_id = self.repo.get_address_id(
            workspace_id=workspace_id, canonical_address=normalized.canonical
        )
        assert address_id is not None  # ensure_recipient_address just guaranteed this row
        suppression_id, was_new = self.repo.upsert_manual_suppression_for_import(
            workspace_id=workspace_id,
            address_id=address_id,
            import_id=import_id,
            row_number=row_number,
            actor_id=initiator_id,
        )
        status = "ACCEPTED" if was_new else "DUPLICATE"
        self.repo.insert_row_result(
            workspace_id=workspace_id,
            import_id=import_id,
            row_number=row_number,
            status=status,
            suppression_id=suppression_id,
        )
        return status


def _encode_row_cursor(row_number: int) -> str:
    import base64
    import json as _json

    payload = _json.dumps({"row": row_number}, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")


def _decode_row_cursor(cursor: str | None) -> int | None:
    import base64
    import json as _json

    if not cursor:
        return None
    try:
        padded = cursor + ("=" * (-len(cursor) % 4))
        data = _json.loads(base64.urlsafe_b64decode(padded).decode("utf-8"))
        return int(data["row"])
    except Exception as exc:
        raise AppError("validation_error", "Cursor is invalid", status_code=422) from exc


__all__ = ["ImportService", "iter_csv_rows", "ImportMappingIn"]
