from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.modules.events.unsubscribe_service import UnsubscribeService


class TestUnsubscribeService:
    def test_hash_token_deterministic(self) -> None:
        token = "test-token-12345"
        expected = hashlib.sha256(token.encode("utf-8")).hexdigest()
        assert UnsubscribeService.hash_token(token) == expected

    def test_generate_token_executes_insert_with_digest(self) -> None:
        session = MagicMock()
        service = UnsubscribeService(session)

        workspace_id = uuid4()
        address_id = uuid4()

        raw_token = service.generate_token(
            workspace_id=workspace_id,
            address_id=address_id,
            lifetime=timedelta(days=7),
        )

        assert isinstance(raw_token, str)
        assert len(raw_token) > 20
        # Verify raw token is never passed to SQL, only the SHA-256 digest
        session.execute.assert_called()
        call_params = session.execute.call_args[0][1]
        assert call_params["token_digest"] == UnsubscribeService.hash_token(raw_token)
        assert call_params["workspace_id"] == str(workspace_id)
        assert call_params["address_id"] == str(address_id)

    def test_resolve_token_returns_record_if_present(self) -> None:
        session = MagicMock()
        service = UnsubscribeService(session)

        token = "some-random-token"
        token_id = uuid4()
        workspace_id = uuid4()
        address_id = uuid4()
        expires_at = datetime.now(UTC) + timedelta(days=5)

        mapping_result = {
            "id": token_id,
            "workspace_id": workspace_id,
            "address_id": address_id,
            "message_id": None,
            "expires_at": expires_at,
            "revoked_at": None,
        }
        mock_res = MagicMock()
        mock_res.mappings.return_value.first.return_value = mapping_result
        session.execute.return_value = mock_res

        resolved = service.resolve_token(token)
        assert resolved is not None
        assert resolved["id"] == token_id
        assert resolved["workspace_id"] == workspace_id

    def test_execute_unsubscribe_happy_path(self) -> None:
        session = MagicMock()
        service = UnsubscribeService(session)
        service.repo = MagicMock()

        token = "valid-unsub-token"
        token_id = uuid4()
        workspace_id = uuid4()
        address_id = uuid4()
        enrollment_id = uuid4()
        now = datetime.now(UTC)

        token_info = {
            "id": token_id,
            "workspace_id": workspace_id,
            "address_id": address_id,
            "expires_at": now + timedelta(days=2),
            "revoked_at": None,
        }
        with patch.object(service, "resolve_token", return_value=token_info):
            service.repo.upsert_suppression.return_value = (uuid4(), True)
            service.repo.stop_active_enrollments.return_value = [enrollment_id]
            service.repo.cancel_non_terminal_messages.return_value = 2

            result = service.execute_unsubscribe(token, now=now)

            assert result["success"] is True
            assert result["status"] == "unsubscribed"
            assert result["suppression_created"] is True
            assert result["enrollments_stopped"] == 1
            assert result["messages_cancelled"] == 2

            service.repo.upsert_suppression.assert_called_once()
            service.repo.stop_active_enrollments.assert_called_once()
            service.repo.cancel_non_terminal_messages.assert_called_once_with(
                workspace_id=workspace_id,
                address_id=address_id,
                terminal_reason="unsubscribed",
            )
            session.commit.assert_called_once()

    def test_execute_unsubscribe_expired_token(self) -> None:
        session = MagicMock()
        service = UnsubscribeService(session)

        token = "expired-unsub-token"
        now = datetime.now(UTC)
        token_info = {
            "id": uuid4(),
            "workspace_id": uuid4(),
            "address_id": uuid4(),
            "expires_at": now - timedelta(days=1),
            "revoked_at": None,
        }
        with patch.object(service, "resolve_token", return_value=token_info):
            result = service.execute_unsubscribe(token, now=now)
            assert result["success"] is False
            assert result["status"] == "expired_token"

    def test_execute_unsubscribe_already_revoked_idempotent(self) -> None:
        session = MagicMock()
        service = UnsubscribeService(session)
        service.repo = MagicMock()

        token = "already-revoked-token"
        now = datetime.now(UTC)
        token_info = {
            "id": uuid4(),
            "workspace_id": uuid4(),
            "address_id": uuid4(),
            "expires_at": now + timedelta(days=1),
            "revoked_at": now - timedelta(hours=1),
        }
        with patch.object(service, "resolve_token", return_value=token_info):
            result = service.execute_unsubscribe(token, now=now)
            assert result["success"] is True
            assert result["status"] == "already_unsubscribed"
            assert result["suppression_created"] is False
            service.repo.upsert_suppression.assert_not_called()


class TestUnsubscribeHttpEndpoints:
    @pytest.fixture
    def client(self) -> TestClient:
        mock_db = MagicMock()
        from app.api.deps import get_db

        app.dependency_overrides[get_db] = lambda: mock_db
        test_client = TestClient(app)
        yield test_client
        app.dependency_overrides.clear()

    def test_get_unsubscribe_page_scanner_safe(self, client: TestClient) -> None:
        """GET request must return confirmation UI and NEVER execute unsubscribe."""
        token = "sample-token-abc"
        with patch.object(
            UnsubscribeService,
            "resolve_token",
            return_value={"id": uuid4(), "revoked_at": None},
        ), patch.object(UnsubscribeService, "execute_unsubscribe") as mock_exec:
            response = client.get(f"/api/v1/unsubscribe/{token}")
            assert response.status_code == 200
            assert "Confirm Unsubscribe" in response.text
            # Crucial: GET never executes unsubscribe
            mock_exec.assert_not_called()

    def test_get_unsubscribe_page_not_found(self, client: TestClient) -> None:
        with patch.object(UnsubscribeService, "resolve_token", return_value=None):
            response = client.get("/api/v1/unsubscribe/invalid-token")
            assert response.status_code == 404
            assert "Invalid or Expired Link" in response.text

    def test_post_unsubscribe_executes_successfully(self, client: TestClient) -> None:
        token = "sample-token-xyz"
        with patch.object(
            UnsubscribeService,
            "execute_unsubscribe",
            return_value={
                "success": True,
                "status": "unsubscribed",
                "message": "You have been successfully unsubscribed.",
            },
        ):
            response = client.post(f"/api/v1/unsubscribe/{token}")
            assert response.status_code == 200
            data = response.json()
            assert data["success"] is True
            assert data["status"] == "unsubscribed"

    def test_post_unsubscribe_invalid_or_expired_returns_400(self, client: TestClient) -> None:
        token = "bad-token"
        with patch.object(
            UnsubscribeService,
            "execute_unsubscribe",
            return_value={
                "success": False,
                "status": "invalid_token",
                "message": "The unsubscribe link is invalid or has expired.",
            },
        ):
            response = client.post(f"/api/v1/unsubscribe/{token}")
            assert response.status_code == 400
            assert "invalid or has expired" in response.json()["error"]["message"]
