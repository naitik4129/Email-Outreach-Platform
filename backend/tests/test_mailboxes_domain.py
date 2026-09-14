from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest

from app.core.crypto import encrypt_credentials, encrypt_verifier
from app.core.errors import AppError
from app.modules.mailboxes.providers.base import (
    ConnectionValidationResult,
    EmailProvider,
    OutboundMessageEnvelope,
    ProviderAccountIdentity,
    ProviderSendResult,
    TokenExchangeResult,
    TokenRefreshResult,
)
from app.modules.mailboxes.providers.registry import ProviderRegistry
from app.modules.mailboxes.repository import MailboxRepository
from app.modules.mailboxes.service import MailboxService


class FakeDomainTestProvider(EmailProvider):
    def __init__(self, email: str = "sender@example.com") -> None:
        self.email = email
        self.sent_envelopes: list[OutboundMessageEnvelope] = []
        self.revoked_tokens: list[str] = []

    def get_authorization_url(
        self,
        state: str,
        redirect_uri: str,
        login_hint: str | None = None,
        code_challenge: str | None = None,
    ) -> str:
        return f"https://accounts.google.com/o/oauth2/v2/auth?state={state}&code_challenge={code_challenge}"

    def exchange_code(
        self, code: str, redirect_uri: str, code_verifier: str
    ) -> TokenExchangeResult:
        return TokenExchangeResult(
            access_token="fake-access-token",
            refresh_token="fake-refresh-token",
            expires_in=3600,
            token_type="Bearer",
            granted_scopes=["https://www.googleapis.com/auth/gmail.send"],
        )

    def refresh_token(self, refresh_token: str) -> TokenRefreshResult:
        return TokenRefreshResult(
            access_token="refreshed-access-token",
            expires_in=3600,
            token_type="Bearer",
            refresh_token="fake-refresh-token",
            granted_scopes=["https://www.googleapis.com/auth/gmail.send"],
        )

    def get_identity(self, access_token: str) -> ProviderAccountIdentity:
        return ProviderAccountIdentity(
            email_address=self.email,
            display_name="Sender Name",
            provider_account_id=f"google-{self.email}",
        )

    def validate_connection(self, access_token: str) -> ConnectionValidationResult:
        return ConnectionValidationResult(is_valid=True)

    def send_message(
        self, access_token: str, envelope: OutboundMessageEnvelope
    ) -> ProviderSendResult:
        self.sent_envelopes.append(envelope)
        return ProviderSendResult(
            status="ACCEPTED",
            provider_message_id=f"msg-{uuid.uuid4().hex[:8]}",
            provider_thread_id="thread-123",
            accepted_at=datetime.now(UTC),
        )

    def revoke_token(self, token: str) -> None:
        self.revoked_tokens.append(token)

    def classify_error(self, exc: Exception) -> None:
        return None


@pytest.fixture
def mock_repo() -> MagicMock:
    return MagicMock(spec=MailboxRepository)


@pytest.fixture
def mailbox_service(mock_repo: MagicMock) -> MailboxService:
    return MailboxService(repo=mock_repo)


@pytest.fixture(autouse=True)
def register_fake_provider() -> None:
    fake = FakeDomainTestProvider()
    ProviderRegistry.register("GMAIL", fake)
    yield
    ProviderRegistry.reset()


def test_start_gmail_oauth_creates_flow_and_url(
    mailbox_service: MailboxService, mock_repo: MagicMock
) -> None:
    workspace_id = uuid.uuid4()
    actor_id = uuid.uuid4()

    resp = mailbox_service.start_gmail_oauth(
        workspace_id=workspace_id,
        user_id=actor_id,
        return_path="/app/mailboxes",
    )

    assert "https://accounts.google.com/o/oauth2/v2/auth" in resp.authorization_url
    assert "code_challenge=" in resp.authorization_url
    assert mock_repo.insert_oauth_flow.called


def test_complete_gmail_oauth_invalid_or_consumed_state_rejected(
    mailbox_service: MailboxService, mock_repo: MagicMock
) -> None:
    actor_id = uuid.uuid4()

    # When state is not found, expired, or already claimed
    mock_repo.get_and_claim_oauth_flow.return_value = None

    with pytest.raises(AppError) as exc_info:
        mailbox_service.complete_gmail_oauth(
            user_id=actor_id,
            code="auth-code",
            state_token="invalid-or-consumed-state",
        )
    assert exc_info.value.code == "invalid_oauth_state"
    assert exc_info.value.status_code == 400


def test_complete_gmail_oauth_actor_mismatch_rejected(
    mailbox_service: MailboxService, mock_repo: MagicMock
) -> None:
    workspace_id = uuid.uuid4()
    actor_id = uuid.uuid4()
    other_user_id = uuid.uuid4()
    flow_id = uuid.uuid4()

    mock_repo.get_and_claim_oauth_flow.return_value = {
        "id": flow_id,
        "workspace_id": workspace_id,
        "actor_id": other_user_id,  # Started by someone else
        "return_path": "/app/mailboxes",
    }

    with pytest.raises(AppError) as exc_info:
        mailbox_service.complete_gmail_oauth(
            user_id=actor_id,
            code="auth-code",
            state_token="valid-state",
        )
    assert exc_info.value.code == "forbidden"
    assert exc_info.value.status_code == 403
    assert mock_repo.fail_oauth_flow.called


def test_complete_gmail_oauth_reconnect_email_mismatch_rejected(
    mailbox_service: MailboxService, mock_repo: MagicMock
) -> None:
    workspace_id = uuid.uuid4()
    actor_id = uuid.uuid4()
    flow_id = uuid.uuid4()
    target_mailbox_id = uuid.uuid4()

    raw_verifier = "a" * 50
    enc_verifier, key_id, nonce = encrypt_verifier(
        raw_verifier, workspace_id, actor_id, "GMAIL"
    )

    mock_repo.get_and_claim_oauth_flow.return_value = {
        "id": flow_id,
        "workspace_id": workspace_id,
        "actor_id": actor_id,
        "return_path": f"/app/mailboxes/{target_mailbox_id}",
        "encrypted_verifier": enc_verifier,
        "verifier_nonce": nonce,
        "verifier_key_id": key_id,
    }
    mock_repo.get_active_mailbox_by_provider_account_global.return_value = None

    # Target mailbox has a different address
    mock_repo.get_mailbox.return_value = {
        "id": target_mailbox_id,
        "original_address": "other-address@example.com",
        "provider_account_id": "google-other-address@example.com",
    }

    with pytest.raises(AppError) as exc_info:
        mailbox_service.complete_gmail_oauth(
            user_id=actor_id,
            code="auth-code",
            state_token="valid-state",
        )
    assert exc_info.value.code == "account_mismatch"
    assert exc_info.value.status_code == 400
    assert mock_repo.fail_oauth_flow.called


def test_complete_gmail_oauth_success_new_mailbox(
    mailbox_service: MailboxService, mock_repo: MagicMock
) -> None:
    workspace_id = uuid.uuid4()
    actor_id = uuid.uuid4()
    flow_id = uuid.uuid4()

    raw_verifier = "b" * 50
    enc_verifier, key_id, nonce = encrypt_verifier(
        raw_verifier, workspace_id, actor_id, "GMAIL"
    )

    mock_repo.get_and_claim_oauth_flow.return_value = {
        "id": flow_id,
        "workspace_id": workspace_id,
        "actor_id": actor_id,
        "return_path": "/app/mailboxes",
        "encrypted_verifier": enc_verifier,
        "verifier_nonce": nonce,
        "verifier_key_id": key_id,
    }
    mock_repo.get_active_mailbox_by_provider_account_global.return_value = None
    mock_repo.get_mailbox_by_provider_account.return_value = None  # New mailbox

    result = mailbox_service.complete_gmail_oauth(
        user_id=actor_id,
        code="auth-code",
        state_token="valid-state-2",
    )

    assert result.email_address == "sender@example.com"
    assert result.connection_state == "CONNECTED"
    assert result.health_state == "HEALTHY"
    assert mock_repo.insert_mailbox_connection.called
    assert mock_repo.insert_mailbox.called
    assert mock_repo.complete_oauth_flow.called


def test_complete_gmail_oauth_identity_failure_fails_flow(
    mailbox_service: MailboxService, mock_repo: MagicMock
) -> None:
    workspace_id = uuid.uuid4()
    actor_id = uuid.uuid4()
    flow_id = uuid.uuid4()

    raw_verifier = "c" * 50
    enc_verifier, key_id, nonce = encrypt_verifier(
        raw_verifier, workspace_id, actor_id, "GMAIL"
    )

    mock_repo.get_and_claim_oauth_flow.return_value = {
        "id": flow_id,
        "workspace_id": workspace_id,
        "actor_id": actor_id,
        "return_path": "/app/mailboxes",
        "encrypted_verifier": enc_verifier,
        "verifier_nonce": nonce,
        "verifier_key_id": key_id,
    }

    failing_provider = FakeDomainTestProvider()

    def _raise_identity_error(access_token: str) -> ProviderAccountIdentity:
        raise AppError(
            "provider_error",
            "Failed to retrieve Gmail identity: Google did not grant the "
            "permissions this connection needs. Please reconnect and approve "
            "all requested access.",
            status_code=401,
        )

    failing_provider.get_identity = _raise_identity_error  # type: ignore[method-assign]
    ProviderRegistry.register("GMAIL", failing_provider)

    with pytest.raises(AppError) as exc_info:
        mailbox_service.complete_gmail_oauth(
            user_id=actor_id,
            code="auth-code",
            state_token="valid-state-3",
        )
    assert exc_info.value.status_code == 401
    assert mock_repo.fail_oauth_flow.called
    assert mock_repo.fail_oauth_flow.call_args[0][0] == flow_id
    assert not mock_repo.insert_mailbox.called


def test_disconnect_mailbox_clears_connection_and_revokes(
    mailbox_service: MailboxService, mock_repo: MagicMock
) -> None:
    workspace_id = uuid.uuid4()
    actor_id = uuid.uuid4()
    mailbox_id = uuid.uuid4()

    mock_repo.get_mailbox.return_value = {
        "id": mailbox_id,
        "original_address": "sender@example.com",
        "provider": "GMAIL",
        "connection_state": "CONNECTED",
        "current_connection_generation": 1,
    }
    mock_repo.get_mailbox_connection.return_value = {
        "credential_ciphertext": None,
        "nonce": None,
        "encryption_key_id": "k1",
    }

    resp = mailbox_service.disconnect_mailbox(workspace_id, actor_id, mailbox_id)

    assert resp.mailbox_id == mailbox_id
    assert resp.connection_state == "DISCONNECTED"
    assert mock_repo.destroy_mailbox_connection.called
    assert mock_repo.update_mailbox_connection_state.called


def test_send_controlled_test_email_suppression_blocked(
    mailbox_service: MailboxService, mock_repo: MagicMock
) -> None:
    workspace_id = uuid.uuid4()
    actor_id = uuid.uuid4()
    mailbox_id = uuid.uuid4()
    address_id = uuid.uuid4()

    mock_repo.get_mailbox.return_value = {
        "id": mailbox_id,
        "original_address": "sender@example.com",
        "sender_display_name": "Sender Name",
        "signature_html": None,
        "connection_state": "CONNECTED",
        "health_state": "HEALTHY",
        "policy_state": "ENABLED",
        "policy_reason": None,
    }
    mock_repo.ensure_recipient_address.return_value = address_id
    # Recipient is suppressed!
    mock_repo.is_address_suppressed.return_value = True

    with pytest.raises(AppError) as exc_info:
        mailbox_service.send_controlled_test_email(
            workspace_id=workspace_id,
            user_id=actor_id,
            mailbox_id=mailbox_id,
            recipient_email="blocked@example.com",
        )
    assert exc_info.value.code == "suppressed_recipient"
    assert exc_info.value.status_code == 400


def test_send_controlled_test_email_unhealthy_mailbox_rejected(
    mailbox_service: MailboxService, mock_repo: MagicMock
) -> None:
    workspace_id = uuid.uuid4()
    actor_id = uuid.uuid4()
    mailbox_id = uuid.uuid4()

    # Mailbox is disconnected
    mock_repo.get_mailbox.return_value = {
        "id": mailbox_id,
        "original_address": "sender@example.com",
        "sender_display_name": "Sender Name",
        "signature_html": None,
        "connection_state": "DISCONNECTED",
        "health_state": "UNHEALTHY",
        "policy_state": "ENABLED",
        "policy_reason": None,
    }

    with pytest.raises(AppError) as exc_info:
        mailbox_service.send_controlled_test_email(
            workspace_id=workspace_id,
            user_id=actor_id,
            mailbox_id=mailbox_id,
            recipient_email="ok@example.com",
        )
    assert exc_info.value.code == "conflict"
    assert exc_info.value.status_code == 409


def test_send_controlled_test_email_success(
    mailbox_service: MailboxService, mock_repo: MagicMock
) -> None:
    workspace_id = uuid.uuid4()
    actor_id = uuid.uuid4()
    mailbox_id = uuid.uuid4()
    address_id = uuid.uuid4()
    membership_id = uuid.uuid4()
    receipt_id = uuid.uuid4()

    mock_repo.get_mailbox.return_value = {
        "id": mailbox_id,
        "original_address": "sender@example.com",
        "sender_display_name": "Sender Name",
        "signature_html": "<p>Best regards</p>",
        "connection_state": "CONNECTED",
        "health_state": "HEALTHY",
        "policy_state": "ENABLED",
        "policy_reason": None,
        "current_connection_generation": 1,
        "provider": "GMAIL",
    }
    mock_repo.ensure_recipient_address.return_value = address_id
    mock_repo.is_address_suppressed.return_value = False
    mock_repo.get_user_membership_id.return_value = membership_id
    mock_repo.ensure_command_receipt.return_value = receipt_id

    creds_payload = {
        "access_token": "valid-token",
        "refresh_token": "valid-refresh",
        "token_type": "Bearer",
    }
    enc_creds, key_id, nonce = encrypt_credentials(
        creds_payload, workspace_id, mailbox_id, "GMAIL"
    )

    mock_repo.get_mailbox_connection.return_value = {
        "credential_ciphertext": enc_creds,
        "nonce": nonce,
        "encryption_key_id": key_id,
        "expires_at": datetime.now(UTC) + timedelta(hours=1),
    }

    result = mailbox_service.send_controlled_test_email(
        workspace_id=workspace_id,
        user_id=actor_id,
        mailbox_id=mailbox_id,
        recipient_email="recipient@example.com",
    )

    assert result.status == "SENT"
    assert result.provider_message_id is not None
    assert mock_repo.insert_test_message.called
    assert mock_repo.insert_message_attempt.called
    assert mock_repo.update_message_and_attempt_result.called
