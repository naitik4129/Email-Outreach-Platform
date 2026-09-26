from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_db
from app.main import app
from app.modules.events.repository import EventRepository


class TestEventSecurityAndValidation:
    @pytest.fixture
    def client(self) -> TestClient:
        mock_db = MagicMock()
        app.dependency_overrides[get_db] = lambda: mock_db
        test_client = TestClient(app)
        yield test_client
        app.dependency_overrides.clear()

    def test_oversized_payload_rejected_with_413_gmail(self, client: TestClient) -> None:
        """Any webhook payload exceeding 1MB must be rejected immediately with 413."""
        huge_payload = b"x" * (1024 * 1024 + 10)  # > 1MB
        response = client.post(
            "/api/v1/webhooks/gmail",
            content=huge_payload,
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 413
        assert "exceeds maximum permitted size" in response.text

    def test_oversized_payload_rejected_with_413_microsoft(self, client: TestClient) -> None:
        huge_payload = b"x" * (1024 * 1024 + 10)
        response = client.post(
            "/api/v1/webhooks/microsoft",
            content=huge_payload,
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 413
        assert "exceeds maximum permitted size" in response.text

    def test_oversized_payload_rejected_with_413_events(self, client: TestClient) -> None:
        huge_payload = b"x" * (1024 * 1024 + 10)
        response = client.post(
            "/api/v1/webhooks/events",
            content=huge_payload,
            headers={"content-type": "application/json"},
        )
        assert response.status_code == 413
        assert "exceeds maximum permitted size" in response.text

    def test_forged_generic_signature_rejected_with_401(self, client: TestClient) -> None:
        body = json.dumps({"event_type": "BOUNCE"}).encode("utf-8")
        with patch("app.api.v1.webhooks.Settings.current") as mock_settings:
            settings_mock = MagicMock()
            settings_mock.event_webhook_secret = "secret-key-123"
            mock_settings.return_value = settings_mock

            # Provide forged signature
            headers = {
                "x-webhook-signature": "sha256=forged_signature_hex_value",
                "x-webhook-timestamp": str(int(time.time())),
            }
            response = client.post(
                "/api/v1/webhooks/events",
                content=body,
                headers=headers,
            )
            assert response.status_code == 401

    def test_forged_gmail_bearer_token_rejected_with_401(self, client: TestClient) -> None:
        body = json.dumps({"message": {}}).encode("utf-8")
        with patch("app.api.v1.webhooks.Settings.current") as mock_settings:
            settings_mock = MagicMock()
            settings_mock.gmail_webhook_secret = "correct-token"
            mock_settings.return_value = settings_mock

            response = client.post(
                "/api/v1/webhooks/gmail",
                content=body,
                headers={"authorization": "Bearer wrong-token"},
            )
            assert response.status_code == 401

    def test_forged_microsoft_client_state_rejected_with_401(self, client: TestClient) -> None:
        payload = {
            "value": [
                {
                    "subscriptionId": "sub-1",
                    "clientState": "forged-client-state",
                    "changeType": "created",
                    "resource": "Users('u@ex.com')",
                }
            ]
        }
        with patch("app.api.v1.webhooks.Settings.current") as mock_settings:
            settings_mock = MagicMock()
            settings_mock.microsoft_webhook_client_state = "expected-client-state"
            mock_settings.return_value = settings_mock

            response = client.post(
                "/api/v1/webhooks/microsoft",
                content=json.dumps(payload).encode("utf-8"),
                headers={"content-type": "application/json"},
            )
            assert response.status_code == 401

    def test_unmapped_mailbox_event_rejected_safely(self, client: TestClient) -> None:
        """Events for email addresses or accounts that do not exist in any workspace
        must be rejected with 404 and never routed to another tenant.
        """
        body_dict = {
            "event_type": "BOUNCE",
            "provider_event_id": "evt-unmapped-01",
            "recipient_email": "target@domain.com",
            "mailbox_email": "unknown_mailbox@nowhere.com",
            "provider": "SMTP",
            "bounce_type": "HARD",
        }
        raw_body = json.dumps(body_dict).encode("utf-8")

        secret = "unmapped-test-secret"
        signature = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
        with (
            patch("app.api.v1.webhooks.Settings.current") as mock_settings,
            patch.object(
                EventRepository,
                "resolve_mailbox_by_email_or_account",
                return_value=None,
            ),
        ):
            mock_settings.return_value = MagicMock(event_webhook_secret=secret)
            # Correctly signed, so authentication passes, but mailbox resolution fails
            response = client.post(
                "/api/v1/webhooks/events",
                content=raw_body,
                headers={
                    "content-type": "application/json",
                    "x-webhook-signature": f"sha256={signature}",
                },
            )
            assert response.status_code == 404
            assert "Mailbox could not be resolved" in response.text

    @pytest.mark.parametrize(
        ("path", "secret_attr"),
        [
            ("/api/v1/webhooks/events", "event_webhook_secret"),
            ("/api/v1/webhooks/gmail", "gmail_webhook_secret"),
            ("/api/v1/webhooks/microsoft", "microsoft_webhook_client_state"),
        ],
    )
    def test_webhook_is_disabled_until_a_secret_is_configured(
        self, client: TestClient, path: str, secret_attr: str
    ) -> None:
        """An unconfigured shared secret must close the endpoint (503), never
        leave it accepting unauthenticated events."""
        with patch("app.api.v1.webhooks.Settings.current") as mock_settings:
            mock_settings.return_value = MagicMock(**{secret_attr: ""})
            response = client.post(
                path,
                content=b"{}",
                headers={"content-type": "application/json"},
            )
        assert response.status_code == 503
