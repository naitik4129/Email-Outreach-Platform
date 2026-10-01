from __future__ import annotations

import logging
import uuid
from typing import Any
from uuid import UUID

from app.core.config import Settings
from app.core.middleware import is_transient_database_failure
from app.db.session import SessionLocal
from app.modules.personalization.factory import (
    build_generation_runner,
    build_model,
    build_preview_runner,
)
from workers.celery_app import celery_app

logger = logging.getLogger(__name__)

_PREVIEW_MAX_RETRIES = 4
_PREVIEW_RETRY_BASE_SECONDS = 15


def _close(model: Any) -> None:
    close = getattr(model, "close", None)
    if callable(close):
        close()


@celery_app.task(
    name="personalization.generate_chunk",
    queue="personalization",
    bind=True,
    max_retries=None,
)
def generate_chunk(self: Any, workspace_id: str, campaign_id: str) -> None:
    """One bounded chunk of just-in-time generation for a campaign (ADR-0011).

    The payload carries ids only: no lead data, content or credentials. All
    state (lease, attempt counters, eligibility) is durable in PostgreSQL, so a
    duplicate or redelivered task is harmless -- the lease claim serializes it.
    """
    settings = Settings.current()
    if not settings.personalization_enabled:
        return
    ws_uuid = UUID(workspace_id)
    campaign_uuid = UUID(campaign_id)
    lease_owner = str(getattr(self.request, "id", None) or uuid.uuid4())

    model = build_model(settings)
    try:
        runner = build_generation_runner(
            settings, ws_uuid, session_factory=SessionLocal, model=model
        )
        summary = runner.run_chunk(
            workspace_id=ws_uuid, campaign_id=campaign_uuid, lease_owner=lease_owner
        )
    finally:
        _close(model)

    if summary.claimed or summary.exhausted:
        # Counts and stable reason codes only: never recipient data or content.
        logger.info(
            f"Campaign {campaign_id} personalization: claimed={summary.claimed} "
            f"generated={summary.generated} fallback={summary.fallback} "
            f"rejected={summary.rejected} failed={summary.failed} "
            f"superseded={summary.superseded} deferred={summary.deferred} "
            f"transient_errors={summary.transient_errors} "
            f"permanent_errors={summary.permanent_errors} "
            f"exhausted={summary.exhausted} codes={summary.codes}"
        )
    if summary.permanent_errors:
        logger.error(
            f"Campaign {campaign_id}: personalization model configuration error "
            "(check the API key, model name and provider billing)"
        )
    if summary.claimed >= settings.personalization_chunk_size and (
        summary.generated + summary.fallback + summary.failed + summary.superseded
    ):
        # A full chunk made progress: look for more right away.
        self.retry(
            kwargs={"workspace_id": workspace_id, "campaign_id": campaign_id},
            countdown=1,
            max_retries=self.request.retries + 1,
        )


@celery_app.task(
    name="personalization.generate_previews",
    queue="personalization",
    bind=True,
    max_retries=None,
)
def generate_previews(
    self: Any, workspace_id: str, campaign_id: str, batch_id: str
) -> None:
    """Generate the sample previews of one batch (ids only in the payload).

    A person is watching this batch, so it must always end: a transient database
    failure (the pooler is full, a connection dropped) is retried a few times --
    safe, because only PENDING samples are picked up -- and anything else, or the
    last failed retry, marks the unfinished samples FAILED instead of leaving them
    PENDING for a page that polls forever.
    """
    settings = Settings.current()
    if not settings.personalization_enabled:
        return
    ws_uuid = UUID(workspace_id)
    campaign_uuid = UUID(campaign_id)
    batch_uuid = UUID(batch_id)
    model = build_model(settings)
    try:
        runner = build_preview_runner(
            settings, ws_uuid, session_factory=SessionLocal, model=model
        )
        try:
            summary = runner.run_batch(
                workspace_id=ws_uuid, campaign_id=campaign_uuid, batch_id=batch_uuid
            )
        except Exception as exc:
            retries = int(self.request.retries or 0)
            if is_transient_database_failure(exc) and retries < _PREVIEW_MAX_RETRIES:
                logger.warning(
                    f"Campaign {campaign_id} preview batch {batch_id}: database "
                    f"unavailable, retry {retries + 1} of {_PREVIEW_MAX_RETRIES}"
                )
                raise self.retry(
                    exc=exc,
                    countdown=min(_PREVIEW_RETRY_BASE_SECONDS * 2**retries, 120),
                    max_retries=_PREVIEW_MAX_RETRIES,
                ) from exc
            logger.exception(
                f"Campaign {campaign_id} preview batch {batch_id}: generation failed"
            )
            _fail_unfinished(runner, ws_uuid, campaign_uuid, batch_uuid)
            raise
    finally:
        _close(model)
    logger.info(
        f"Campaign {campaign_id} preview batch {batch_id}: ok={summary.ok} "
        f"failed={summary.failed} codes={summary.codes}"
    )


def _fail_unfinished(
    runner: Any, workspace_id: UUID, campaign_id: UUID, batch_id: UUID
) -> None:
    """Best effort: if the database is what failed this can fail too, and the page
    then falls back to its own "taking longer than expected" message. It must not
    hide the original error, which the caller re-raises."""
    try:
        runner.fail_unfinished(
            workspace_id=workspace_id,
            campaign_id=campaign_id,
            batch_id=batch_id,
            codes=["generation_unavailable"],
        )
    except Exception:
        logger.exception(
            f"Campaign {campaign_id} preview batch {batch_id}: could not mark the "
            "unfinished samples as failed"
        )
