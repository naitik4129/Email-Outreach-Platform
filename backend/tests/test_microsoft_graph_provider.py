from __future__ import annotations

import httpx
import pytest

from app.core.errors import AppError
from app.modules.mailboxes.providers.base import (
    ErrorCategory,
    OutboundMessageEnvelope,
    ProviderCapability,
    UnsupportedCapabilityError,
)
from app.modules.mailboxes.providers.microsoft import MicrosoftGraphProvider
from app.modules.mailboxes.providers.registry import ProviderRegistry


def test_capabilities_exclude_token_revocation() -> None:
    provider = MicrosoftGraphProvider(client_id="id", client_secret="secret")
    assert ProviderCapability.TOKEN_REVOCATION not in provider.capabilities
    assert ProviderCapability.SEND in provider.capabilities
    assert ProviderCapability.OAUTH_FLOW in provider.capabilities


def test_get_authorization_url_uses_common_authority_and_scopes() -> None:
    provider = MicrosoftGraphProvider(
        client_id="test-client-id", client_secret="secret"
    )
    url = provider.get_authorization_url(
        state="random-state-123",
        redirect_uri="http://localhost:8000/callback",
        code_challenge="test-challenge",
    )

    assert "https://login.microsoftonline.com/common/oauth2/v2.0/authorize" in url
    assert "client_id=test-client-id" in url
    assert "state=random-state-123" in url
    assert "code_challenge=test-challenge" in url
    assert "code_challenge_method=S256" in url
    assert "offline_access" in url
    assert "Mail.Send" in url
    # Least privilege: never request broad mail/contacts/calendar scopes.
    assert "Mail.Read" not in url
    assert "Contacts" not in url
    assert "Calendars" not in url


def test_exchange_code_success() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "login.microsoftonline.com" in str(request.url)
        return httpx.Response(
            200,
            json={
                "access_token": "at-123",
                "refresh_token": "rt-123",
                "token_type": "Bearer",
                "expires_in": 3600,
                "scope": "openid profile email offline_access",
            },
        )

    mock_client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = MicrosoftGraphProvider(
        client_id="id", client_secret="secret", http_client=mock_client
    )

    result = provider.exchange_code(code="auth-code", redirect_uri="http://localhost/cb")
    assert result.access_token == "at-123"
    assert result.refresh_token == "rt-123"


def test_exchange_code_failure_raises_provider_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400, json={"error": {"code": "invalid_grant", "message": "Bad code"}}
        )

    mock_client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = MicrosoftGraphProvider(
        client_id="id", client_secret="secret", http_client=mock_client
    )

    with pytest.raises(AppError) as exc_info:
        provider.exchange_code(code="bad-code", redirect_uri="http://localhost/cb")
    assert exc_info.value.code == "provider_error"


def test_get_identity_uses_mail_field() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "graph.microsoft.com"
        assert request.headers["Authorization"] == "Bearer fake-token"
        return httpx.Response(
            200,
            json={
                "id": "aad-object-id-1",
                "mail": "User@Contoso.com",
                "userPrincipalName": "user@contoso.onmicrosoft.com",
                "displayName": "User Name",
            },
        )

    mock_client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = MicrosoftGraphProvider(
        client_id="id", client_secret="secret", http_client=mock_client
    )

    identity = provider.get_identity("fake-token")
    assert identity.email_address == "user@contoso.com"
    assert identity.provider_account_id == "aad-object-id-1"
    assert identity.display_name == "User Name"


def test_get_identity_falls_back_to_user_principal_name_when_mail_is_null() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": "aad-object-id-2",
                "mail": None,
                "userPrincipalName": "personal@outlook.com",
            },
        )

    mock_client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = MicrosoftGraphProvider(
        client_id="id", client_secret="secret", http_client=mock_client
    )

    identity = provider.get_identity("fake-token")
    assert identity.email_address == "personal@outlook.com"


def test_send_message_202_empty_body_is_accepted_with_no_message_id() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer fake-token"
        body = request.content
        assert b"sendMail" not in body  # sanity: body is JSON, not a URL echo
        return httpx.Response(202)

    mock_client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = MicrosoftGraphProvider(
        client_id="id", client_secret="secret", http_client=mock_client
    )

    envelope = OutboundMessageEnvelope(
        to_address="lead@example.com",
        from_address="sales@company.com",
        from_name="Sales Team",
        subject="Introduction",
        body_html="<p>Hello world</p>",
    )

    result = provider.send_message({"access_token": "fake-token"}, envelope)
    assert result.status == "ACCEPTED"
    # Graph's sendMail returns no message id synchronously -- absence must
    # never be treated as rejection.
    assert result.provider_message_id is None


def test_send_message_header_injection_rejected() -> None:
    provider = MicrosoftGraphProvider(client_id="id", client_secret="secret")
    envelope = OutboundMessageEnvelope(
        to_address="victim@example.com",
        from_address="sender@example.com",
        subject="Hello\r\nBcc: evil@example.com",
        body_html="<p>test</p>",
    )
    with pytest.raises(AppError) as exc_info:
        provider.send_message({"access_token": "fake-token"}, envelope)
    assert exc_info.value.code == "header_injection"


def test_send_message_timeout_becomes_unknown() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("Connection timed out")

    mock_client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = MicrosoftGraphProvider(
        client_id="id", client_secret="secret", http_client=mock_client
    )
    envelope = OutboundMessageEnvelope(
        to_address="lead@example.com",
        from_address="sales@company.com",
        subject="Test",
        body_html="<p>Test</p>",
    )

    result = provider.send_message({"access_token": "fake-token"}, envelope)
    assert result.status == "UNKNOWN"
    assert result.error_category == ErrorCategory.UNKNOWN_OUTCOME


def test_error_classification_table() -> None:
    provider = MicrosoftGraphProvider()

    c401 = provider.classify_error(
        401, {"error": {"code": "InvalidAuthenticationToken", "message": "Expired"}}
    )
    assert c401.category == ErrorCategory.AUTH_FAILURE
    assert c401.requires_reconnect is True

    c403 = provider.classify_error(
        403, {"error": {"code": "ErrorAccessDenied", "message": "Denied"}}
    )
    assert c403.category == ErrorCategory.INSUFFICIENT_SCOPE
    assert c403.requires_reconnect is True

    c429 = provider.classify_error(
        429, {"error": {"code": "TooManyRequests", "message": "Throttled"}}
    )
    assert c429.category == ErrorCategory.RATE_LIMIT
    assert c429.is_retryable is True

    c400 = provider.classify_error(
        400, {"error": {"code": "ErrorInvalidRecipients", "message": "bad recipient"}}
    )
    assert c400.category == ErrorCategory.PERMANENT_RECIPIENT_FAILURE

    c503 = provider.classify_error(503, {"error": {"message": "unavailable"}})
    assert c503.category == ErrorCategory.TEMPORARY_PROVIDER_ERROR
    assert c503.is_retryable is True

    c418 = provider.classify_error(418, {"error": {"message": "teapot"}})
    assert c418.category == ErrorCategory.POLICY_REJECTION


def test_revoke_token_raises_unsupported_capability() -> None:
    provider = MicrosoftGraphProvider()
    with pytest.raises(UnsupportedCapabilityError):
        provider.revoke_token("some-refresh-token")


def test_provider_registry_resolves_microsoft() -> None:
    ProviderRegistry.reset()
    provider = ProviderRegistry.get("MICROSOFT")
    assert isinstance(provider, MicrosoftGraphProvider)
    ProviderRegistry.reset()
