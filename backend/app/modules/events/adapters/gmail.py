from __future__ import annotations

import base64
import hmac
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from app.modules.events.adapters.base import VerifiedEventEvidence
from app.modules.events.schemas import InboundEventType


class GmailEventAdapter:
    """Adapter for Google Cloud Pub/Sub push notifications for Gmail API changes.

    Google Cloud Pub/Sub wraps the Gmail notification in a push envelope:
    {
        "message": {
            "data": "<base64 encoded JSON: {'emailAddress': '...', 'historyId': '...'}>",
            "messageId": "2070443601311540",
            "publishTime": "2026-09-23T12:00:00.000Z"
        },
        "subscription": "projects/my-project/subscriptions/gmail-push"
    }
    """

    @property
    def provider_name(self) -> str:
        return "GMAIL"

    def verify_and_parse(
        self,
        *,
        headers: Mapping[str, str],
        body_bytes: bytes,
        query_params: Mapping[str, str],
        secret: str | None = None,
    ) -> VerifiedEventEvidence:
        # 1. Verification of Bearer token or token query param
        if secret:
            auth_header = headers.get("authorization", "")
            expected_auth = f"Bearer {secret}"
            token_param = query_params.get("token", "")

            valid_header = hmac.compare_digest(auth_header, expected_auth)
            valid_param = hmac.compare_digest(token_param, secret)

            if not (valid_header or valid_param):
                return VerifiedEventEvidence(
                    is_valid=False,
                    provider="GMAIL",
                    source_schema="gmail.pubsub.v1",
                    event_identity="unknown",
                    error_message="Invalid or missing authentication secret",
                )

        # 2. Parse JSON push envelope
        try:
            payload: dict[str, Any] = json.loads(body_bytes.decode("utf-8"))
        except Exception:
            return VerifiedEventEvidence(
                is_valid=False,
                provider="GMAIL",
                source_schema="gmail.pubsub.v1",
                event_identity="malformed",
                error_message="Invalid JSON payload",
            )

        if not isinstance(payload, dict):
            return VerifiedEventEvidence(
                is_valid=False,
                provider="GMAIL",
                source_schema="gmail.pubsub.v1",
                event_identity="malformed",
                error_message="Payload must be a JSON object",
            )

        message_obj = payload.get("message")
        if not isinstance(message_obj, dict):
            return VerifiedEventEvidence(
                is_valid=False,
                provider="GMAIL",
                source_schema="gmail.pubsub.v1",
                event_identity="malformed",
                error_message="Missing message object in Pub/Sub envelope",
            )

        message_id = str(message_obj.get("messageId", "")).strip()
        if not message_id:
            return VerifiedEventEvidence(
                is_valid=False,
                provider="GMAIL",
                source_schema="gmail.pubsub.v1",
                event_identity="malformed",
                error_message="Missing messageId in Pub/Sub message",
            )

        data_b64 = message_obj.get("data")
        if not data_b64 or not isinstance(data_b64, str):
            return VerifiedEventEvidence(
                is_valid=False,
                provider="GMAIL",
                source_schema="gmail.pubsub.v1",
                event_identity=f"gmail:pubsub:{message_id}",
                error_message="Missing base64 data in Pub/Sub message",
            )

        # 3. Decode base64 data
        try:
            decoded_bytes = base64.b64decode(data_b64)
            data_json = json.loads(decoded_bytes.decode("utf-8"))
        except Exception:
            return VerifiedEventEvidence(
                is_valid=False,
                provider="GMAIL",
                source_schema="gmail.pubsub.v1",
                event_identity=f"gmail:pubsub:{message_id}",
                error_message="Failed to decode or parse inner message data",
            )

        email_address = str(data_json.get("emailAddress", "")).strip().lower()
        if not email_address or "@" not in email_address:
            return VerifiedEventEvidence(
                is_valid=False,
                provider="GMAIL",
                source_schema="gmail.pubsub.v1",
                event_identity=f"gmail:pubsub:{message_id}",
                error_message="Missing or invalid emailAddress in message data",
            )

        occurred_at = datetime.now(UTC)
        publish_time_str = message_obj.get("publishTime")
        if publish_time_str:
            try:
                # e.g. 2026-09-23T12:00:00.000Z
                occurred_at = datetime.fromisoformat(publish_time_str.replace("Z", "+00:00"))
            except Exception:
                pass

        return VerifiedEventEvidence(
            is_valid=True,
            provider="GMAIL",
            source_schema="gmail.pubsub.v1",
            event_identity=f"gmail:pubsub:{message_id}",
            mailbox_email=email_address,
            event_type=InboundEventType.NOTIFICATION,
            occurred_at=occurred_at,
            raw_data={
                "messageId": message_id,
                "historyId": data_json.get("historyId"),
                "emailAddress": email_address,
                "subscription": payload.get("subscription"),
            },
        )
