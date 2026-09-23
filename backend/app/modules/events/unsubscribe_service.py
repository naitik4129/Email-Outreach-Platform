from __future__ import annotations

import hashlib
import json
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.metrics import (
    record_message_cancelled_due_to_event,
    record_suppression_created,
    record_unsubscribe_processed,
)
from app.modules.events.repository import EventRepository, _safe_set_workspace


class UnsubscribeService:
    """Service governing high-entropy unsubscribe token generation and consumption.

    Enforces:
    1. Opaque, high-entropy tokens mapped to exactly one workspace and address.
    2. SHA-256 storage digest (token plaintext is never stored or logged).
    3. Idempotent consumption (repeated requests succeed without duplicate side effects).
    4. Immediate durable suppression commit before acknowledgment.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = EventRepository(session)

    @staticmethod
    def hash_token(raw_token: str) -> str:
        return hashlib.sha256(raw_token.strip().encode("utf-8")).hexdigest()

    def generate_token(
        self,
        *,
        workspace_id: UUID,
        address_id: UUID,
        message_id: UUID | None = None,
        lifetime: timedelta = timedelta(days=30),
        now: datetime | None = None,
    ) -> str:
        now = now or datetime.now(UTC)
        expires_at = now + lifetime

        # 32 bytes URL-safe high-entropy token
        raw_token = secrets.token_urlsafe(32)
        token_digest = self.hash_token(raw_token)

        _safe_set_workspace(self.session, workspace_id)

        stmt = text(
            """
            INSERT INTO public.unsubscribe_tokens (
                workspace_id, address_id, message_id,
                token_digest, purpose, expires_at
            )
            VALUES (
                :workspace_id, :address_id, :message_id,
                :token_digest, 'UNSUBSCRIBE', :expires_at
            )
            RETURNING id
            """
        )
        self.session.execute(
            stmt,
            {
                "workspace_id": str(workspace_id),
                "address_id": str(address_id),
                "message_id": str(message_id) if message_id else None,
                "token_digest": token_digest,
                "expires_at": expires_at,
            },
        )
        return raw_token

    def resolve_token(
        self,
        raw_token: str,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any] | None:
        now = now or datetime.now(UTC)
        token_digest = self.hash_token(raw_token)

        # Look up token record by digest
        stmt = text(
            """
            SELECT id, workspace_id, address_id, message_id,
                   expires_at, revoked_at
            FROM public.unsubscribe_tokens
            WHERE token_digest = :token_digest
            LIMIT 1
            """
        )
        row = self.session.execute(stmt, {"token_digest": token_digest}).mappings().first()
        if row is None:
            return None

        return dict(row)

    def execute_unsubscribe(
        self,
        raw_token: str,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Atomically consume unsubscribe token, commit suppression, and stop enrollments."""
        now = now or datetime.now(UTC)
        token_info = self.resolve_token(raw_token, now=now)

        if token_info is None:
            return {
                "success": False,
                "status": "invalid_token",
                "message": "The unsubscribe link is invalid or has expired.",
            }

        workspace_id = UUID(str(token_info["workspace_id"]))
        address_id = UUID(str(token_info["address_id"]))
        token_id = UUID(str(token_info["id"]))
        expires_at = token_info["expires_at"]
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)

        # Check expiry
        if now > expires_at:
            return {
                "success": False,
                "status": "expired_token",
                "message": "The unsubscribe link has expired.",
            }

        # Check if already revoked (idempotent success)
        if token_info["revoked_at"] is not None:
            return {
                "success": True,
                "status": "already_unsubscribed",
                "message": "You have already been unsubscribed from this workspace.",
                "suppression_created": False,
            }

        token_digest = self.hash_token(raw_token)

        # 1. Upsert durable suppression
        _, suppression_created = self.repo.upsert_suppression(
            workspace_id=workspace_id,
            address_id=address_id,
            reason="UNSUBSCRIBE",
            source_key=token_digest,
            evidence={"action": "one_click_unsubscribe", "token_id": str(token_id)},
        )

        # 2. Stop active enrollments
        stopped_ids = self.repo.stop_active_enrollments(
            workspace_id=workspace_id,
            address_id=address_id,
            stop_reason="UNSUBSCRIBED",
        )
        for eid in stopped_ids:
            self.repo.record_recipient_outcome(
                workspace_id=workspace_id,
                enrollment_id=eid,
                kind="UNSUBSCRIBED",
                source_key=token_digest,
                occurred_at=now,
            )

        # 3. Cancel non-terminal future messages
        messages_cancelled = self.repo.cancel_non_terminal_messages(
            workspace_id=workspace_id,
            address_id=address_id,
            terminal_reason="unsubscribed",
        )

        # 4. Revoke token
        revoke_stmt = text(
            """
            UPDATE public.unsubscribe_tokens
            SET revoked_at = :now,
                version = version + 1
            WHERE id = :token_id
              AND revoked_at IS NULL
            """
        )
        self.session.execute(revoke_stmt, {"token_id": str(token_id), "now": now})

        # 5. Record domain event
        self.repo.record_domain_event(
            workspace_id=workspace_id,
            event_type="recipient.suppressed",
            aggregate_type="recipient_address",
            aggregate_id=address_id,
            semantic_key=f"unsubscribe_token:{token_digest}",
            occurred_at=now,
            payload={"reason": "UNSUBSCRIBE", "token_id": str(token_id)},
        )

        self.session.commit()

        record_unsubscribe_processed("TOKEN")
        if suppression_created:
            record_suppression_created("UNSUBSCRIBE")
        if messages_cancelled > 0:
            record_message_cancelled_due_to_event("unsubscribed")

        return {
            "success": True,
            "status": "unsubscribed",
            "message": "You have been successfully unsubscribed.",
            "suppression_created": suppression_created,
            "enrollments_stopped": len(stopped_ids),
            "messages_cancelled": messages_cancelled,
        }
