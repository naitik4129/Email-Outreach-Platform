from __future__ import annotations

import hashlib
import json
import logging
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy.orm import Session

from app.api.deps import get_db
from app.core.config import Settings
from app.core.metrics import (
    record_event_deduplicated,
    record_event_received,
    record_event_rejected,
    record_event_verified,
)
from app.db.context import set_transaction_context
from app.modules.events.adapters.generic import GenericWebhookAdapter
from app.modules.events.adapters.gmail import GmailEventAdapter
from app.modules.events.adapters.microsoft import MicrosoftGraphEventAdapter
from app.modules.events.repository import EventRepository
from app.modules.events.schemas import InboundEventType, ScopeKind

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

_MAX_PAYLOAD_BYTES = 1024 * 1024  # 1MB limit for inbound webhooks


def _require_secret(secret: str | None) -> str:
    """Webhooks are unauthenticated public endpoints; without a configured
    shared secret they must not accept events at all."""
    if not secret:
        raise HTTPException(status_code=503, detail="Webhook endpoint is not configured")
    return secret


def _dispatch_event_task(receipt_id: UUID, workspace_id: UUID) -> None:
    try:
        from workers.celery_app import celery_app

        celery_app.send_task(
            "event.process",
            kwargs={"receipt_id": str(receipt_id), "workspace_id": str(workspace_id)},
            queue="webhooks",
        )
    except Exception as exc:
        logger.warning(
            "Failed to enqueue event.process task immediately (will be recovered by sweep)",
            extra={"receipt_id": str(receipt_id), "error": str(exc)},
        )


@router.post("/gmail")
async def gmail_pubsub_webhook(
    request: Request,
    db: Session = Depends(get_db),
    token: str | None = Query(default=None),
) -> Response:
    """Google Cloud Pub/Sub push notification endpoint for Gmail mailbox updates."""
    record_event_received("GMAIL")

    body_bytes = await request.body()
    if len(body_bytes) > _MAX_PAYLOAD_BYTES:
        record_event_rejected("GMAIL", "payload_too_large")
        raise HTTPException(status_code=413, detail="Payload exceeds maximum permitted size")

    secret = _require_secret(Settings.current().gmail_webhook_secret)

    headers = {k.lower(): v for k, v in request.headers.items()}
    query_params = dict(request.query_params)

    adapter = GmailEventAdapter()
    evidence = adapter.verify_and_parse(
        headers=headers,
        body_bytes=body_bytes,
        query_params=query_params,
        secret=secret,
    )

    if not evidence.is_valid:
        record_event_rejected("GMAIL", evidence.error_message or "verification_failed")
        raise HTTPException(status_code=401, detail=evidence.error_message or "Verification failed")

    record_event_verified("GMAIL")
    repo = EventRepository(db)

    # Resolve mailbox and workspace server-side
    mailbox = repo.resolve_mailbox_by_email_or_account(
        provider="GMAIL",
        email_address=evidence.mailbox_email,
    )
    if mailbox is None:
        # Provider event references an account not currently configured in any workspace
        logger.info(
            "Gmail event received for unknown/disconnected mailbox",
            extra={"email": evidence.mailbox_email},
        )
        # Return 200/204 to Pub/Sub to avoid endless retries of unmappable accounts
        return Response(status_code=204)

    workspace_id = UUID(str(mailbox["workspace_id"]))
    mailbox_id = UUID(str(mailbox["id"]))
    set_transaction_context(db, workspace_id=workspace_id)

    payload_digest = hashlib.sha256(body_bytes).hexdigest()

    receipt_id, was_inserted = repo.store_raw_receipt(
        workspace_id=workspace_id,
        mailbox_id=mailbox_id,
        provider="GMAIL",
        scope_kind=ScopeKind.MAILBOX,
        event_identity=evidence.event_identity,
        source_schema=evidence.source_schema,
        payload_digest=payload_digest,
        payload_ref=json.dumps(evidence.raw_data),
        verified_at=evidence.occurred_at,
    )

    if not was_inserted:
        record_event_deduplicated("GMAIL")
        db.commit()
        return Response(status_code=200)

    db.commit()
    _dispatch_event_task(receipt_id, workspace_id)
    return Response(status_code=200)


@router.post("/microsoft")
async def microsoft_graph_webhook(
    request: Request,
    db: Session = Depends(get_db),
    validationToken: str | None = Query(default=None),
) -> Response:
    """Microsoft Graph change notification endpoint (supports validation challenge)."""
    # 1. Validation challenge handshake (only answered once the endpoint is configured)
    if validationToken:
        _require_secret(Settings.current().microsoft_webhook_client_state)
        return Response(content=validationToken, media_type="text/plain", status_code=200)

    record_event_received("MICROSOFT")

    body_bytes = await request.body()
    if len(body_bytes) > _MAX_PAYLOAD_BYTES:
        record_event_rejected("MICROSOFT", "payload_too_large")
        raise HTTPException(status_code=413, detail="Payload exceeds maximum permitted size")

    secret = _require_secret(Settings.current().microsoft_webhook_client_state)

    headers = {k.lower(): v for k, v in request.headers.items()}
    query_params = dict(request.query_params)

    adapter = MicrosoftGraphEventAdapter()
    evidence = adapter.verify_and_parse(
        headers=headers,
        body_bytes=body_bytes,
        query_params=query_params,
        secret=secret,
    )

    if not evidence.is_valid:
        record_event_rejected("MICROSOFT", evidence.error_message or "verification_failed")
        raise HTTPException(status_code=401, detail=evidence.error_message or "Verification failed")

    record_event_verified("MICROSOFT")
    repo = EventRepository(db)

    # Resolve mailbox server-side
    mailbox = None
    if evidence.mailbox_email:
        mailbox = repo.resolve_mailbox_by_email_or_account(
            provider="MICROSOFT",
            email_address=evidence.mailbox_email,
        )

    if mailbox is None:
        logger.info("Microsoft Graph event for unmapped mailbox", extra={"email": evidence.mailbox_email})
        return Response(status_code=202)

    workspace_id = UUID(str(mailbox["workspace_id"]))
    mailbox_id = UUID(str(mailbox["id"]))
    set_transaction_context(db, workspace_id=workspace_id)

    payload_digest = hashlib.sha256(body_bytes).hexdigest()

    receipt_id, was_inserted = repo.store_raw_receipt(
        workspace_id=workspace_id,
        mailbox_id=mailbox_id,
        provider="MICROSOFT",
        scope_kind=ScopeKind.MAILBOX,
        event_identity=evidence.event_identity,
        source_schema=evidence.source_schema,
        payload_digest=payload_digest,
        payload_ref=json.dumps(evidence.raw_data),
        verified_at=evidence.occurred_at,
    )

    if not was_inserted:
        record_event_deduplicated("MICROSOFT")
        db.commit()
        return Response(status_code=202)

    db.commit()
    _dispatch_event_task(receipt_id, workspace_id)
    return Response(status_code=202)


@router.post("/events")
async def generic_event_webhook(
    request: Request,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    """Authenticated generic provider webhook endpoint for bounces, complaints, and unsubscribes."""
    record_event_received("GENERIC")

    body_bytes = await request.body()
    if len(body_bytes) > _MAX_PAYLOAD_BYTES:
        record_event_rejected("GENERIC", "payload_too_large")
        raise HTTPException(status_code=413, detail="Payload exceeds maximum permitted size")

    secret = _require_secret(Settings.current().event_webhook_secret)

    headers = {k.lower(): v for k, v in request.headers.items()}
    query_params = dict(request.query_params)

    adapter = GenericWebhookAdapter()
    evidence = adapter.verify_and_parse(
        headers=headers,
        body_bytes=body_bytes,
        query_params=query_params,
        secret=secret,
    )

    if not evidence.is_valid:
        record_event_rejected("GENERIC", evidence.error_message or "verification_failed")
        raise HTTPException(status_code=401, detail=evidence.error_message or "Verification failed")

    record_event_verified("GENERIC")
    repo = EventRepository(db)

    # Resolve mailbox and workspace server-side
    mailbox = repo.resolve_mailbox_by_email_or_account(
        provider=evidence.provider,
        email_address=evidence.mailbox_email,
        provider_account_id=evidence.provider_account_id,
    )

    if mailbox is None:
        record_event_rejected("GENERIC", "unmapped_mailbox")
        raise HTTPException(status_code=404, detail="Mailbox could not be resolved from event evidence")

    workspace_id = UUID(str(mailbox["workspace_id"]))
    mailbox_id = UUID(str(mailbox["id"]))
    set_transaction_context(db, workspace_id=workspace_id)

    payload_digest = hashlib.sha256(body_bytes).hexdigest()

    # Store raw receipt
    receipt_id, was_inserted = repo.store_raw_receipt(
        workspace_id=workspace_id,
        mailbox_id=mailbox_id,
        provider=evidence.provider,
        scope_kind=ScopeKind.MAILBOX,
        event_identity=evidence.event_identity,
        source_schema=evidence.source_schema,
        payload_digest=payload_digest,
        payload_ref=json.dumps(
            {
                "event_type": evidence.event_type.value,
                "recipient_email": evidence.recipient_email,
                "provider_message_id": evidence.provider_message_id,
                "bounce_classification": (
                    evidence.bounce_classification.value if evidence.bounce_classification else None
                ),
                "reason": evidence.reason,
                "raw": evidence.raw_data,
            }
        ),
        verified_at=evidence.occurred_at,
    )

    if not was_inserted:
        record_event_deduplicated("GENERIC")
        db.commit()
        return {"status": "accepted", "receipt_id": str(receipt_id), "duplicate": True}

    # For safety events (BOUNCE, COMPLAINT, UNSUBSCRIBE), place active safety hold immediately
    if evidence.event_type in (
        InboundEventType.BOUNCE,
        InboundEventType.COMPLAINT,
        InboundEventType.UNSUBSCRIBE,
    ):
        repo.create_safety_hold(
            workspace_id=workspace_id,
            source_receipt_id=receipt_id,
            source_work_identity=f"receipt:{receipt_id}:safety",
            target_kind=ScopeKind.MAILBOX,
            target_mailbox_id=mailbox_id,
            reason=f"inbound_safety_event:{evidence.event_type.value.lower()}",
        )

    db.commit()
    _dispatch_event_task(receipt_id, workspace_id)

    return {"status": "accepted", "receipt_id": str(receipt_id), "duplicate": False}
