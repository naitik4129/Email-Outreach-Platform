from __future__ import annotations

import logging
from itertools import islice
from typing import Any
from uuid import UUID

from celery.exceptions import Retry

from app.core.config import Settings
from app.core.errors import AppError
from app.db.context import enter_worker_scope
from app.db.session import SessionLocal
from app.modules.imports.csv_parsing import iter_csv_rows
from app.modules.imports.service import ImportService
from workers.celery_app import celery_app

logger = logging.getLogger(__name__)

# Recommended batch size from CLAUDE.md guidelines
BATCH_SIZE = 500
_WORKER_ROLE = "app_worker_general"


@celery_app.task(name="imports.process_chunk", queue="imports", bind=True, max_retries=3)
def process_chunk(self: Any, workspace_id: str, import_id: str) -> None:
    """One bounded chunk of a CSV import, claimed via a lease so a duplicate or
    redelivered task is safe.

    Transactions use explicit commit()/rollback() rather than
    `with session.begin():`: SQLAlchemy 2.0 autobegins on the first statement
    (the lease claim), and a later begin() raises "A transaction is already
    begun". Because the workspace GUC and SET LOCAL ROLE are transaction-scoped,
    every commit/rollback wipes them and _scope() re-applies them.
    """
    settings = Settings.current()
    lease_owner = self.request.id
    ws_uuid = UUID(workspace_id)
    import_uuid = UUID(import_id)

    with SessionLocal() as session:
        service = ImportService(session, settings=settings)

        def _scope() -> None:
            enter_worker_scope(session, workspace_id=ws_uuid, role_name=_WORKER_ROLE)

        def _fail(error: AppError) -> None:
            session.rollback()
            _scope()
            service.fail(workspace_id=ws_uuid, import_id=import_uuid, error=error)
            session.commit()

        _scope()
        try:
            job = service.claim(
                workspace_id=ws_uuid, import_id=import_uuid, lease_owner=lease_owner
            )
            if not job:
                logger.info(
                    f"Import {import_id} not claimable or already terminal/claimed."
                )
                session.rollback()
                return
            session.commit()  # persist the lease claim before any further work
            _scope()

            text = service.download_and_decode(
                job["storage_object_key"], job["storage_object_digest"]
            )

            # Resume after the durable checkpoint, then take one batch.
            batch = list(
                islice(iter_csv_rows(text, start_row=job["row_cursor"]), BATCH_SIZE)
            )

            finished = service.process_batch(
                workspace_id=ws_uuid,
                import_id=import_uuid,
                job=job,
                batch=batch,
                lease_owner=lease_owner,
            )
            session.commit()

            if not finished:
                logger.info(
                    f"Import {import_id} batch processed. Re-queuing for next chunk."
                )
                self.retry(countdown=1, max_retries=self.request.retries + 1)
            else:
                logger.info(f"Import {import_id} completed successfully.")

        except Retry:
            # self.retry() raises this to schedule redelivery; it is not a failure.
            raise
        except AppError as exc:
            logger.error(f"App error processing import {import_id}: {exc.message}")
            _fail(exc)
        except Exception:
            logger.exception(f"Unexpected error processing import {import_id}")
            _fail(
                AppError(
                    "internal_error",
                    "An unexpected error occurred during processing",
                    status_code=500,
                )
            )
