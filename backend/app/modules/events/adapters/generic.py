from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from app.modules.events.adapters.base import VerifiedEventEvidence
from app.modules.events.schemas import BounceClassification, InboundEventType

_STATUS_CODE_RE = re.compile(r"(?<![\d.])([245])\.(\d{1,3})\.(\d{1,3})(?![\d.])")


def _classify_bounce(explicit: str, reason: str) -> BounceClassification:
    """Hard/soft from what the provider said; UNKNOWN when it said nothing.

    An explicit provider verdict wins. Otherwise the RFC 3463 status class in
    the diagnostic decides (5.x.x permanent, 4.x.x transient); a substring such
    as "4." must not, because it also appears inside 5.4.x codes. 5.2.2
    (mailbox full) is reported as permanent but is recoverable.
    """
    if explicit in ("HARD", "PERMANENT"):
        return BounceClassification.HARD
    if explicit in ("SOFT", "TEMPORARY", "TRANSIENT"):
        return BounceClassification.SOFT
    match = _STATUS_CODE_RE.search(reason)
    if match:
        status_class, subject = match.group(1), match.group(2)
        if status_class == "5":
            return BounceClassification.SOFT if subject == "2" else BounceClassification.HARD
        if status_class == "4":
            return BounceClassification.SOFT
    lowered = reason.lower()
    if any(k in lowered for k in ("does not exist", "user unknown", "no such user")):
        return BounceClassification.HARD
    if any(k in lowered for k in ("mailbox full", "over quota", "try again later")):
        return BounceClassification.SOFT
    return BounceClassification.UNKNOWN


class GenericWebhookAdapter:
    """Adapter for signed inbound event webhooks (bounces, complaints, unsubscribes).

    Supports cryptographic signature verification (HMAC-SHA256) with timestamp-based
    replay prevention (default 5-minute tolerance window).
    """

    @property
    def provider_name(self) -> str:
        return "GENERIC"

    def verify_and_parse(
        self,
        *,
        headers: Mapping[str, str],
        body_bytes: bytes,
        query_params: Mapping[str, str],
        secret: str | None = None,
        max_age_seconds: int = 300,
    ) -> VerifiedEventEvidence:
        # 1. HMAC Signature & Timestamp verification if secret is configured
        if secret:
            signature = headers.get("x-webhook-signature") or headers.get("x-signature")
            timestamp_str = headers.get("x-webhook-timestamp") or headers.get("x-timestamp")

            if not signature:
                return VerifiedEventEvidence(
                    is_valid=False,
                    provider="GENERIC",
                    source_schema="provider.event.v1",
                    event_identity="unauthorized",
                    error_message="Missing signature header (X-Webhook-Signature)",
                )

            # Replay protection check if timestamp header is provided
            if timestamp_str:
                try:
                    ts = int(timestamp_str)
                    now_ts = int(time.time())
                    if abs(now_ts - ts) > max_age_seconds:
                        return VerifiedEventEvidence(
                            is_valid=False,
                            provider="GENERIC",
                            source_schema="provider.event.v1",
                            event_identity="unauthorized",
                            error_message="Signature timestamp expired or skewed beyond tolerance",
                        )
                    signed_content = f"{timestamp_str}.".encode() + body_bytes
                except ValueError:
                    return VerifiedEventEvidence(
                        is_valid=False,
                        provider="GENERIC",
                        source_schema="provider.event.v1",
                        event_identity="unauthorized",
                        error_message="Invalid timestamp header format",
                    )
            else:
                signed_content = body_bytes

            expected_sig = hmac.new(
                secret.encode("utf-8"), signed_content, hashlib.sha256
            ).hexdigest()

            # Support prefix e.g. sha256=... or raw hex
            clean_sig = signature.removeprefix("sha256=").strip()
            if not hmac.compare_digest(clean_sig, expected_sig):
                return VerifiedEventEvidence(
                    is_valid=False,
                    provider="GENERIC",
                    source_schema="provider.event.v1",
                    event_identity="unauthorized",
                    error_message="Cryptographic signature verification failed",
                )

        # 2. Parse JSON payload
        try:
            payload: dict[str, Any] = json.loads(body_bytes.decode("utf-8"))
        except Exception:
            return VerifiedEventEvidence(
                is_valid=False,
                provider="GENERIC",
                source_schema="provider.event.v1",
                event_identity="malformed",
                error_message="Invalid JSON payload",
            )

        if not isinstance(payload, dict):
            return VerifiedEventEvidence(
                is_valid=False,
                provider="GENERIC",
                source_schema="provider.event.v1",
                event_identity="malformed",
                error_message="Payload must be a JSON object",
            )

        # 3. Extract and normalize fields
        raw_event_type = str(payload.get("event_type", "")).strip().upper()
        try:
            event_type = InboundEventType(raw_event_type)
        except ValueError:
            return VerifiedEventEvidence(
                is_valid=False,
                provider="GENERIC",
                source_schema="provider.event.v1",
                event_identity="unsupported_type",
                error_message=f"Unsupported event_type: {raw_event_type}",
            )

        provider_name = str(payload.get("provider", "SMTP")).strip().upper()
        if provider_name not in ("GMAIL", "MICROSOFT", "SMTP"):
            provider_name = "SMTP"

        provider_event_id = str(payload.get("provider_event_id", "")).strip()
        recipient_email = str(payload.get("recipient_email", "")).strip().lower()
        mailbox_email = str(payload.get("mailbox_email", "")).strip().lower() or None
        provider_message_id = str(payload.get("provider_message_id", "")).strip() or None
        provider_account_id = str(payload.get("provider_account_id", "")).strip() or None

        # Bounce classification
        bounce_classification = None
        if event_type == InboundEventType.BOUNCE:
            raw_bounce = str(payload.get("bounce_type", "")).strip().upper()
            bounce_classification = _classify_bounce(
                raw_bounce, str(payload.get("reason", ""))
            )

        # Stable event identity for durable receipt deduplication
        if provider_event_id:
            event_identity = f"{provider_name.lower()}:{event_type.value.lower()}:{provider_event_id}"
        else:
            # Deterministic fingerprint if provider event ID not provided
            fp_content = f"{provider_name}:{event_type.value}:{recipient_email}:{provider_message_id or payload.get('timestamp', '')}"
            fp_hash = hashlib.sha256(fp_content.encode("utf-8")).hexdigest()[:32]
            event_identity = f"{provider_name.lower()}:{event_type.value.lower()}:{fp_hash}"

        occurred_at = datetime.now(UTC)
        ts_field = payload.get("timestamp") or payload.get("occurred_at")
        if ts_field:
            if isinstance(ts_field, (int, float)):
                try:
                    occurred_at = datetime.fromtimestamp(ts_field, tz=UTC)
                except Exception:
                    pass
            elif isinstance(ts_field, str):
                try:
                    occurred_at = datetime.fromisoformat(ts_field.replace("Z", "+00:00"))
                except Exception:
                    pass

        return VerifiedEventEvidence(
            is_valid=True,
            provider=provider_name,
            source_schema="provider.event.v1",
            event_identity=event_identity,
            provider_account_id=provider_account_id,
            mailbox_email=mailbox_email,
            event_type=event_type,
            occurred_at=occurred_at,
            recipient_email=recipient_email if recipient_email else None,
            provider_message_id=provider_message_id,
            bounce_classification=bounce_classification,
            reason=str(payload.get("reason", "")) or None,
            raw_data=payload,
        )
