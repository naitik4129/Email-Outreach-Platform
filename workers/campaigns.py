from __future__ import annotations

import hashlib
import json
import logging
from typing import Any
from uuid import UUID

from app.core.config import Settings
from app.core.errors import AppError
from app.db.context import set_transaction_context
from app.db.session import SessionLocal
from app.modules.campaigns.worker_repository import CampaignWorkerRepository
from app.modules.templates.rendering import build_lead_render_context
from celery.exceptions import Retry

from workers.celery_app import celery_app

logger = logging.getLogger(__name__)

# Matches workers/imports.py's BATCH_SIZE -- same order of magnitude of
# per-transaction work, no measured reason to diverge.
BATCH_SIZE = 500
_LEASE_TTL_SECONDS = 120


def _classify_member(lead: dict[str, Any]) -> tuple[str, str | None]:
    if lead["lead_status"] == "ARCHIVED":
        return "EXCLUDED", "archived_lead"
    if lead["address_id"] is None:
        return "EXCLUDED", "invalid_address"
    if lead["is_suppressed"]:
        return "EXCLUDED", "suppressed"
    return "ACCEPTED", None


@celery_app.task(
    name="campaigns.capture_audience_chunk",
    queue="campaigns",
    bind=True,
    max_retries=None,
)
def capture_audience_chunk(
    self: Any,
    workspace_id: str,
    campaign_id: str,
    audience_id: str,
    abandon: bool = False,
) -> None:
    """One bounded chunk of audience capture, claimed via a lease so a lost/
    duplicate delivery is safe to redeliver.

    Transactions are demarcated with explicit session.commit()/rollback()
    rather than `with session.begin():` -- SQLAlchemy 2.0's Session
    autobegins a transaction on the very first statement (the lease claim
    below), and a later `session.begin()` on top of that raises
    "A transaction is already begun on this Session" rather than nesting.
    Each commit() closes out the current (auto-begun) transaction so the
    next statement can autobegin a fresh one -- but set_transaction_context()
    uses SET LOCAL (transaction-scoped GUCs, see app/db/context.py), so
    app.workspace_id is wiped out by every commit()/rollback() too and must
    be re-applied before the next statement that needs RLS context.
    """
    Settings.current()
    lease_owner = self.request.id
    ws_uuid = UUID(workspace_id)

    with SessionLocal() as session:

        def _ctx() -> None:
            set_transaction_context(session, workspace_id=ws_uuid)

        _ctx()
        repo = CampaignWorkerRepository(session)
        job: dict[str, Any] | None = None

        try:
            job = repo.claim_capture_job(
                workspace_id=UUID(workspace_id),
                audience_id=UUID(audience_id),
                lease_owner=lease_owner,
                ttl_seconds=_LEASE_TTL_SECONDS,
            )
            if job is None:
                logger.info(
                    f"Capture job for audience {audience_id} not claimable "
                    "or already terminal/claimed."
                )
                session.rollback()
                return
            session.commit()  # persist the lease claim durably before any further work
            _ctx()

            if abandon:
                repo.abandon_capture(
                    workspace_id=UUID(workspace_id),
                    campaign_id=UUID(campaign_id),
                    audience_id=UUID(audience_id),
                    job_id=UUID(str(job["id"])),
                )
                session.commit()
                return

            audience = repo.get_audience(
                workspace_id=UUID(workspace_id), audience_id=UUID(audience_id)
            )
            if audience is None:
                raise AppError(
                    "not_found", "Audience revision not found", status_code=404
                )

            manifest = audience["selection_manifest"] or {}
            cursor = json.loads(job["cursor_data"]) if job["cursor_data"] else None
            if cursor is None:
                candidate_ids = repo.resolve_candidate_lead_ids(
                    workspace_id=UUID(workspace_id),
                    list_ids=manifest.get("lists", []),
                    lead_ids=manifest.get("leads", []),
                )
                cursor = {"candidate_ids": candidate_ids, "offset": 0}

            candidate_ids: list[str] = cursor["candidate_ids"]
            offset: int = cursor["offset"]
            batch_ids = candidate_ids[offset : offset + BATCH_SIZE]

            if batch_ids:
                lead_rows = repo.fetch_lead_capture_rows(
                    workspace_id=UUID(workspace_id), lead_ids=batch_ids
                )
                lead_by_id = {str(row["lead_id"]): dict(row) for row in lead_rows}
                members: list[dict[str, Any]] = []
                for i, lead_id in enumerate(batch_ids):
                    lead = lead_by_id.get(lead_id)
                    ordinal = offset + i + 1
                    if lead is None:
                        # Referenced by the manifest but not resolvable under
                        # this workspace's RLS scope any more (e.g. deleted
                        # between selection and capture).
                        members.append(
                            {
                                "lead_id": lead_id,
                                "address_id": None,
                                "capture_ordinal": ordinal,
                                "contact_revision": 1,
                                "frozen_variables": {},
                                "eligibility_status": "EXCLUDED",
                                "exclusion_reason": "invalid_address",
                            }
                        )
                        continue
                    eligibility, reason = _classify_member(lead)
                    frozen_variables = (
                        build_lead_render_context(lead)
                        if eligibility == "ACCEPTED"
                        else {}
                    )
                    members.append(
                        {
                            "lead_id": lead_id,
                            "address_id": (
                                str(lead["address_id"]) if lead["address_id"] else None
                            ),
                            "capture_ordinal": ordinal,
                            "contact_revision": lead["contact_revision"],
                            "frozen_variables": frozen_variables,
                            "eligibility_status": eligibility,
                            "exclusion_reason": reason,
                        }
                    )
                # A member without a resolvable address cannot be inserted
                # (address_id is NOT NULL) -- skip it entirely; it is
                # already excluded from send eligibility by construction and
                # is reflected only in the processed/total counters.
                insertable = [m for m in members if m["address_id"] is not None]
                repo.insert_audience_members(
                    workspace_id=UUID(workspace_id),
                    campaign_id=UUID(campaign_id),
                    audience_id=UUID(audience_id),
                    members=insertable,
                )

            new_offset = offset + len(batch_ids)
            finished = len(batch_ids) == 0 or new_offset >= len(candidate_ids)

            if finished:
                digest = hashlib.sha256(
                    json.dumps(manifest, sort_keys=True).encode("utf-8")
                ).hexdigest()
                repo.complete_capture(
                    workspace_id=UUID(workspace_id),
                    campaign_id=UUID(campaign_id),
                    audience_id=UUID(audience_id),
                    job_id=UUID(str(job["id"])),
                    processed_count=new_offset,
                    source_manifest_digest=digest,
                )
            else:
                repo.update_job_progress(
                    workspace_id=UUID(workspace_id),
                    job_id=UUID(str(job["id"])),
                    cursor_data=json.dumps(
                        {"candidate_ids": candidate_ids, "offset": new_offset}
                    ),
                    processed_count=new_offset,
                    lease_owner=lease_owner,
                    ttl_seconds=_LEASE_TTL_SECONDS,
                )
            session.commit()

            if not finished:
                logger.info(
                    f"Audience {audience_id} capture chunk processed "
                    f"({new_offset}/{len(candidate_ids)}). Re-queuing."
                )
                self.retry(countdown=1, max_retries=self.request.retries + 1)
            else:
                logger.info(f"Audience {audience_id} capture completed.")

        except Retry:
            # Celery's self.retry() raises this to schedule redelivery -- it
            # must propagate untouched, never be treated as a capture failure.
            raise
        except AppError as exc:
            logger.error(f"App error capturing audience {audience_id}: {exc.message}")
            session.rollback()
            _ctx()
            if job is not None:
                repo.fail_capture(
                    workspace_id=UUID(workspace_id),
                    campaign_id=UUID(campaign_id),
                    audience_id=UUID(audience_id),
                    job_id=UUID(str(job["id"])),
                    error_reason=exc.message,
                )
                session.commit()
        except Exception:
            logger.exception(f"Unexpected error capturing audience {audience_id}")
            session.rollback()
            _ctx()
            if job is not None:
                repo.fail_capture(
                    workspace_id=UUID(workspace_id),
                    campaign_id=UUID(campaign_id),
                    audience_id=UUID(audience_id),
                    job_id=UUID(str(job["id"])),
                    error_reason="An unexpected error occurred during audience capture",
                )
                session.commit()
