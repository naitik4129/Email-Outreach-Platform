from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from datetime import UTC, datetime

import pytest

from app.modules.events.adapters.generic import GenericWebhookAdapter
from app.modules.events.adapters.gmail import GmailEventAdapter
from app.modules.events.adapters.microsoft import MicrosoftGraphEventAdapter
from app.modules.events.schemas import (
    BounceClassification,
    InboundEventType,
)


class TestGmailEventAdapter:
    def test_verify_and_parse_valid_bearer_token(self) -> None:
        adapter = GmailEventAdapter()
        pubsub_data = json.dumps({
            "emailAddress": "mailbox@example.com",
            "historyId": "987654321",
        }).encode("utf-8")
        b64_data = base64.b64encode(pubsub_data).decode("ascii")

        body = {
            "message": {
                "data": b64_data,
                "messageId": "pubsub-msg-101",
                "publishTime": "2026-09-23T12:00:00.000Z",
            },
            "subscription": "projects/p/subscriptions/sub-1",
        }
        raw_bytes = json.dumps(body).encode("utf-8")

        evidence = adapter.verify_and_parse(
            headers={"authorization": "Bearer secret-token-123"},
            body_bytes=raw_bytes,
            query_params={},
            secret="secret-token-123",
        )
        assert evidence.is_valid is True
        assert evidence.provider == "GMAIL"
        assert evidence.mailbox_email == "mailbox@example.com"
        assert evidence.event_identity == "gmail:pubsub:pubsub-msg-101"
        assert evidence.event_type == InboundEventType.NOTIFICATION
        assert evidence.raw_data["historyId"] == "987654321"

    def test_verify_and_parse_query_token(self) -> None:
        adapter = GmailEventAdapter()
        pubsub_data = json.dumps({
            "emailAddress": "mailbox@example.com",
            "historyId": "987654321",
        }).encode("utf-8")
        b64_data = base64.b64encode(pubsub_data).decode("ascii")

        body = {
            "message": {
                "data": b64_data,
                "messageId": "pubsub-msg-102",
            }
        }
        raw_bytes = json.dumps(body).encode("utf-8")

        evidence = adapter.verify_and_parse(
            headers={},
            body_bytes=raw_bytes,
            query_params={"token": "secret-token-123"},
            secret="secret-token-123",
        )
        assert evidence.is_valid is True
        assert evidence.mailbox_email == "mailbox@example.com"

    def test_verify_and_parse_invalid_token(self) -> None:
        adapter = GmailEventAdapter()
        raw_bytes = b'{"message": {}}'

        evidence = adapter.verify_and_parse(
            headers={"authorization": "Bearer wrong-token"},
            body_bytes=raw_bytes,
            query_params={},
            secret="expected-secret",
        )
        assert evidence.is_valid is False
        assert "Invalid or missing authentication secret" in str(evidence.error_message)

    def test_normalize_pubsub_malformed_base64(self) -> None:
        adapter = GmailEventAdapter()
        body = {
            "message": {
                "data": "not-valid-base64!!!",
                "messageId": "pubsub-msg-bad",
            },
        }
        evidence = adapter.verify_and_parse(
            headers={},
            body_bytes=json.dumps(body).encode("utf-8"),
            query_params={},
            secret=None,
        )
        assert evidence.is_valid is False
        assert "Failed to decode" in str(evidence.error_message)

    def test_normalize_empty_or_non_json_body(self) -> None:
        adapter = GmailEventAdapter()
        evidence = adapter.verify_and_parse(
            headers={},
            body_bytes=b"not-json",
            query_params={},
            secret=None,
        )
        assert evidence.is_valid is False
        assert "Invalid JSON payload" in str(evidence.error_message)


class TestMicrosoftGraphEventAdapter:
    def test_challenge_handshake_via_query_params(self) -> None:
        adapter = MicrosoftGraphEventAdapter()
        evidence = adapter.verify_and_parse(
            headers={"content-type": "text/plain"},
            body_bytes=b"",
            query_params={"validationToken": "test-challenge-token-xyz"},
            secret="expected-secret",
        )
        assert evidence.is_valid is True
        assert evidence.raw_data.get("is_challenge") is True
        assert evidence.raw_data.get("validationToken") == "test-challenge-token-xyz"

    def test_verify_notification_valid_client_state(self) -> None:
        adapter = MicrosoftGraphEventAdapter()
        payload = {
            "value": [
                {
                    "subscriptionId": "sub-graph-1",
                    "clientState": "expected-secret",
                    "changeType": "created",
                    "resource": "Users('user@tenant.com')/Messages('msg-456')",
                    "resourceData": {"id": "msg-456"},
                }
            ]
        }
        evidence = adapter.verify_and_parse(
            headers={"content-type": "application/json"},
            body_bytes=json.dumps(payload).encode("utf-8"),
            query_params={},
            secret="expected-secret",
        )
        assert evidence.is_valid is True
        assert evidence.mailbox_email == "user@tenant.com"
        assert evidence.event_identity == "microsoft:graph:sub-graph-1:msg-456:created"

    def test_verify_notification_mismatched_client_state(self) -> None:
        adapter = MicrosoftGraphEventAdapter()
        payload = {
            "value": [
                {
                    "subscriptionId": "sub-graph-1",
                    "clientState": "wrong-state",
                    "changeType": "created",
                    "resource": "Users('user@tenant.com')/Messages('msg-456')",
                }
            ]
        }
        evidence = adapter.verify_and_parse(
            headers={"content-type": "application/json"},
            body_bytes=json.dumps(payload).encode("utf-8"),
            query_params={},
            secret="expected-secret",
        )
        assert evidence.is_valid is False
        assert "clientState verification failed" in str(evidence.error_message)

    def test_normalize_empty_value_array(self) -> None:
        adapter = MicrosoftGraphEventAdapter()
        payload = {"value": []}
        evidence = adapter.verify_and_parse(
            headers={"content-type": "application/json"},
            body_bytes=json.dumps(payload).encode("utf-8"),
            query_params={},
            secret=None,
        )
        assert evidence.is_valid is False
        assert "Notification 'value' array is empty" in str(evidence.error_message)


class TestGenericWebhookAdapter:
    def _generate_signature_and_headers(
        self, secret: str, body: bytes, timestamp: int | None = None
    ) -> dict[str, str]:
        if timestamp is None:
            timestamp = int(time.time())
        ts_str = str(timestamp)
        signed_payload = f"{ts_str}.".encode("utf-8") + body
        sig = hmac.new(secret.encode("utf-8"), signed_payload, hashlib.sha256).hexdigest()
        return {
            "x-webhook-signature": f"sha256={sig}",
            "x-webhook-timestamp": ts_str,
        }

    def test_valid_signature_and_timestamp(self) -> None:
        adapter = GenericWebhookAdapter()
        body_dict = {
            "event_type": "BOUNCE",
            "provider_event_id": "evt-bounce-1",
            "recipient_email": "test@lead.com",
            "bounce_type": "HARD",
            "reason": "5.1.1 User unknown",
        }
        raw_body = json.dumps(body_dict).encode("utf-8")
        headers = self._generate_signature_and_headers("webhook-secret-999", raw_body)

        evidence = adapter.verify_and_parse(
            headers=headers,
            body_bytes=raw_body,
            query_params={},
            secret="webhook-secret-999",
        )
        assert evidence.is_valid is True
        assert evidence.event_type == InboundEventType.BOUNCE
        assert evidence.bounce_classification == BounceClassification.HARD
        assert evidence.recipient_email == "test@lead.com"
        assert evidence.event_identity == "smtp:bounce:evt-bounce-1"

    def test_expired_timestamp_replay_rejected(self) -> None:
        adapter = GenericWebhookAdapter()
        body_dict = {"event_type": "BOUNCE", "recipient_email": "test@lead.com"}
        raw_body = json.dumps(body_dict).encode("utf-8")
        # 6 minutes ago (exceeds 300s replay window)
        past_ts = int(time.time()) - 360
        headers = self._generate_signature_and_headers("webhook-secret-999", raw_body, timestamp=past_ts)

        evidence = adapter.verify_and_parse(
            headers=headers,
            body_bytes=raw_body,
            query_params={},
            secret="webhook-secret-999",
        )
        assert evidence.is_valid is False
        assert "timestamp expired" in str(evidence.error_message)

    def test_future_timestamp_rejected(self) -> None:
        adapter = GenericWebhookAdapter()
        body_dict = {"event_type": "BOUNCE", "recipient_email": "test@lead.com"}
        raw_body = json.dumps(body_dict).encode("utf-8")
        # 6 minutes in future
        future_ts = int(time.time()) + 360
        headers = self._generate_signature_and_headers("webhook-secret-999", raw_body, timestamp=future_ts)

        evidence = adapter.verify_and_parse(
            headers=headers,
            body_bytes=raw_body,
            query_params={},
            secret="webhook-secret-999",
        )
        assert evidence.is_valid is False
        assert "timestamp expired or skewed" in str(evidence.error_message)

    def test_tampered_body_fails_signature(self) -> None:
        adapter = GenericWebhookAdapter()
        raw_body = json.dumps({"event_type": "BOUNCE"}).encode("utf-8")
        headers = self._generate_signature_and_headers("webhook-secret-999", raw_body)

        tampered_body = json.dumps({"event_type": "BOUNCE", "tampered": True}).encode("utf-8")
        evidence = adapter.verify_and_parse(
            headers=headers,
            body_bytes=tampered_body,
            query_params={},
            secret="webhook-secret-999",
        )
        assert evidence.is_valid is False
        assert "signature verification failed" in str(evidence.error_message)

    def test_normalize_soft_bounce(self) -> None:
        adapter = GenericWebhookAdapter()
        body_dict = {
            "event_type": "BOUNCE",
            "provider_event_id": "evt-bounce-soft-1",
            "recipient_email": "full@target.org",
            "bounce_type": "SOFT",
            "reason": "4.2.2 Mailbox quota exceeded",
        }
        raw_body = json.dumps(body_dict).encode("utf-8")
        evidence = adapter.verify_and_parse(
            headers={},
            body_bytes=raw_body,
            query_params={},
            secret=None,
        )
        assert evidence.is_valid is True
        assert evidence.event_type == InboundEventType.BOUNCE
        assert evidence.bounce_classification == BounceClassification.SOFT
        assert evidence.recipient_email == "full@target.org"

    def test_normalize_complaint(self) -> None:
        adapter = GenericWebhookAdapter()
        body_dict = {
            "event_type": "COMPLAINT",
            "provider_event_id": "evt-comp-1",
            "recipient_email": "complainer@target.org",
        }
        raw_body = json.dumps(body_dict).encode("utf-8")
        evidence = adapter.verify_and_parse(
            headers={},
            body_bytes=raw_body,
            query_params={},
            secret=None,
        )
        assert evidence.is_valid is True
        assert evidence.event_type == InboundEventType.COMPLAINT
        assert evidence.recipient_email == "complainer@target.org"

    def test_normalize_unsubscribe(self) -> None:
        adapter = GenericWebhookAdapter()
        body_dict = {
            "event_type": "UNSUBSCRIBE",
            "provider_event_id": "evt-unsub-1",
            "recipient_email": "optout@target.org",
        }
        raw_body = json.dumps(body_dict).encode("utf-8")
        evidence = adapter.verify_and_parse(
            headers={},
            body_bytes=raw_body,
            query_params={},
            secret=None,
        )
        assert evidence.is_valid is True
        assert evidence.event_type == InboundEventType.UNSUBSCRIBE
        assert evidence.recipient_email == "optout@target.org"
