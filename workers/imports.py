from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from app.core.config import Settings
from app.core.errors import AppError
from app.db.session import SessionLocal
from app.modules.imports.csv_parsing import iter_csv_rows
from app.modules.imports.service import ImportService
from workers.celery_app import celery_app

logger = logging.getLogger(__name__)

# Recommended batch size from CLAUDE.md guidelines
BATCH_SIZE = 500


@celery_app.task(name="imports.process_chunk", queue="imports", bind=True, max_retries=3)
def process_chunk(self: Any, workspace_id: str, import_id: str) -> None:
    settings = Settings.current()
    lease_owner = self.request.id

    with SessionLocal() as session:
        service = ImportService(session, settings=settings)

        try:
            job = service.claim(
                workspace_id=UUID(workspace_id),
                import_id=UUID(import_id),
                lease_owner=lease_owner,
            )
            if not job:
                logger.info(f"Import {import_id} not claimable or already terminal/claimed.")
                return

            text = service.download_and_decode(
                job["storage_object_key"], job["storage_object_digest"]
            )

            # Iterating from row 0 but slicing the exact batch needed
            row_iterator = iter_csv_rows(text)
            
            # Fast-forward to the current cursor
            current_cursor = job["row_cursor"]
            for _ in range(current_cursor):
                try:
                    next(row_iterator)
                except StopIteration:
                    break

            # Collect the next batch
            batch = []
            for _ in range(BATCH_SIZE):
                try:
                    batch.append(next(row_iterator))
                except StopIteration:
                    break

            # Process batch within a single transaction
            with session.begin():
                finished = service.process_batch(
                    workspace_id=UUID(workspace_id),
                    import_id=UUID(import_id),
                    job=job,
                    batch=batch,
                    lease_owner=lease_owner,
                )

            if not finished:
                logger.info(f"Import {import_id} batch processed. Re-queuing for next chunk.")
                # Re-queue the task to process the next chunk
                self.retry(countdown=1, max_retries=self.request.retries + 1)
            else:
                logger.info(f"Import {import_id} completed successfully.")

        except AppError as exc:
            logger.error(f"App error processing import {import_id}: {exc.message}")
            with session.begin():
                service.fail(
                    workspace_id=UUID(workspace_id),
                    import_id=UUID(import_id),
                    error=exc,
                )
        except Exception as exc:
            logger.exception(f"Unexpected error processing import {import_id}")
            with session.begin():
                service.fail(
                    workspace_id=UUID(workspace_id),
                    import_id=UUID(import_id),
                    error=AppError("internal_error", "An unexpected error occurred during processing", status_code=500),
                )
