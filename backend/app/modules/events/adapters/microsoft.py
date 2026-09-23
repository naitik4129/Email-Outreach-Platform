from __future__ import annotations

import hmac
import json
import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from app.modules.events.adapters.base import VerifiedEventEvidence
from app.modules.events.schemas import InboundEventType

_USER_EMAIL_PATTERN = re.compile(r"Users\('([^']+)'\)", re.IGNORECASE)


class MicrosoftGraphEventAdapter:
    """Adapter for Microsoft Graph Change Notifications.

    Microsoft Graph change notification lifecycle:
    1. Validation challenge: POST with ?validationToken=... -> responds with plain text validationToken.
    2. Notifications: POST with body {"value": [{"subscriptionId": "...", "clientState": "...", ...}]}.
    """

    @property
    def provider_name(self) -> str:
        return "MICROSOFT"

    def verify_and_parse(
        self,
        *,
        headers: Mapping[str, str],
        body_bytes: bytes,
        query_params: Mapping[str, str],
        secret: str | None = None,
    ) -> VerifiedEventEvidence:
        # 1. Check if this is a validation handshake challenge
        validation_token = query_params.get("validationToken")
        if validation_token:
            return VerifiedEventEvidence(
                is_valid=True,
                provider="MICROSOFT",
                source_schema="microsoft.graph.v1",
                event_identity=f"microsoft:challenge:{validation_token[:32]}",
                event_type=InboundEventType.NOTIFICATION,
                raw_data={"validationToken": validation_token, "is_challenge": True},
            )

        # 2. Parse JSON payload
        try:
            payload: dict[str, Any] = json.loads(body_bytes.decode("utf-8"))
        except Exception:
            return VerifiedEventEvidence(
                is_valid=False,
                provider="MICROSOFT",
                source_schema="microsoft.graph.v1",
                event_identity="malformed",
                error_message="Invalid JSON payload",
            )

        if not isinstance(payload, dict) or "value" not in payload:
            return VerifiedEventEvidence(
                is_valid=False,
                provider="MICROSOFT",
                source_schema="microsoft.graph.v1",
                event_identity="malformed",
                error_message="Payload must contain a 'value' array",
            )

        items = payload.get("value")
        if not isinstance(items, list) or len(items) == 0:
            return VerifiedEventEvidence(
                is_valid=False,
                provider="MICROSOFT",
                source_schema="microsoft.graph.v1",
                event_identity="empty",
                error_message="Notification 'value' array is empty",
            )

        # We inspect the primary notification item
        item = items[0]
        if not isinstance(item, dict):
            return VerifiedEventEvidence(
                is_valid=False,
                provider="MICROSOFT",
                source_schema="microsoft.graph.v1",
                event_identity="malformed",
                error_message="Notification item must be an object",
            )

        subscription_id = str(item.get("subscriptionId", "")).strip()
        client_state = str(item.get("clientState", ""))

        # 3. Verify clientState shared secret if configured
        if secret:
            if not hmac.compare_digest(client_state, secret):
                return VerifiedEventEvidence(
                    is_valid=False,
                    provider="MICROSOFT",
                    source_schema="microsoft.graph.v1",
                    event_identity=f"microsoft:graph:{subscription_id or 'unknown'}",
                    error_message="clientState verification failed",
                )

        resource = str(item.get("resource", ""))
        change_type = str(item.get("changeType", "unknown"))
        resource_data = item.get("resourceData", {})
        resource_id = str(resource_data.get("id", "")) if isinstance(resource_data, dict) else ""

        # Attempt to extract user email from resource string: Users('mailbox@example.com')/...
        mailbox_email = None
        match = _USER_EMAIL_PATTERN.search(resource)
        if match:
            mailbox_email = match.group(1).lower()

        # Build stable event identity
        event_ident = f"microsoft:graph:{subscription_id}:{resource_id or resource}:{change_type}"

        return VerifiedEventEvidence(
            is_valid=True,
            provider="MICROSOFT",
            source_schema="microsoft.graph.v1",
            event_identity=event_ident,
            mailbox_email=mailbox_email,
            event_type=InboundEventType.NOTIFICATION,
            occurred_at=datetime.now(UTC),
            raw_data={
                "subscriptionId": subscription_id,
                "changeType": change_type,
                "resource": resource,
                "resourceData": resource_data,
                "tenantId": item.get("tenantId"),
            },
        )
