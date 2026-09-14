"""Shared provider-contract test suite.

Runs the same guarantees against Gmail, Microsoft Graph, and SMTP fake/mocked
adapters to prove the EmailProvider abstraction is not secretly Gmail-shaped.
Never requires a provider to fake a capability it doesn't have -- capability
gaps are asserted explicitly instead (see test_unsupported_capability_*).
"""

from __future__ import annotations

import httpx
import pytest

from app.modules.mailboxes.providers.base import (
    ErrorCategory,
    OutboundMessageEnvelope,
    ProviderCapability,
    UnsupportedCapabilityError,
)
from app.modules.mailboxes.providers.gmail import GmailProvider
from app.modules.mailboxes.providers.microsoft import MicrosoftGraphProvider
from app.modules.mailboxes.providers.smtp import SmtpProvider
from tests.support.fake_smtp_server import FakeSmtpServer
from tests.test_smtp_provider import _credential as smtp_credential
from tests.test_smtp_provider import _resolver_for, _trust_server_cert

ENVELOPE = OutboundMessageEnvelope(
    to_address="lead@example.com",
    from_address="sales@company.com",
    from_name="Sales Team",
    subject="Introduction",
    body_html="<p>Hello world</p>",
)

ALL_PROVIDERS = ["gmail", "microsoft", "smtp"]


def _gmail(handler) -> tuple[GmailProvider, dict]:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = GmailProvider(client_id="id", client_secret="secret", http_client=client)
    return provider, {"access_token": "fake-token"}


def _microsoft(handler) -> tuple[MicrosoftGraphProvider, dict]:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = MicrosoftGraphProvider(
        client_id="id", client_secret="secret", http_client=client
    )
    return provider, {"access_token": "fake-token"}


def _smtp(monkeypatch, *, auth_ok=True, recipient_ok=True) -> tuple[SmtpProvider, dict]:
    server = FakeSmtpServer(mode="starttls", auth_ok=auth_ok, recipient_ok=recipient_ok)
    _trust_server_cert(monkeypatch, server)
    provider = SmtpProvider(resolver=_resolver_for(server), connect_timeout=5.0)
    return provider, smtp_credential()


# -- Normalized success -------------------------------------------------


def test_gmail_send_success_normalizes_to_accepted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "msg-1", "threadId": "t-1"})

    provider, credential = _gmail(handler)
    result = provider.send_message(credential, ENVELOPE)
    assert result.status == "ACCEPTED"


def test_microsoft_send_success_normalizes_to_accepted() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(202)

    provider, credential = _microsoft(handler)
    result = provider.send_message(credential, ENVELOPE)
    assert result.status == "ACCEPTED"


def test_smtp_send_success_normalizes_to_accepted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider, credential = _smtp(monkeypatch)
    result = provider.send_message(credential, ENVELOPE)
    assert result.status == "ACCEPTED"


# -- Normalized auth failure (requires_reconnect) ------------------------


def test_gmail_auth_failure_requires_reconnect() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": "Invalid Credentials"}})

    provider, credential = _gmail(handler)
    result = provider.send_message(credential, ENVELOPE)
    assert result.status == "DEFINITIVELY_REJECTED"
    assert result.error_category == ErrorCategory.AUTH_FAILURE


def test_microsoft_auth_failure_requires_reconnect() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            401, json={"error": {"code": "InvalidAuthenticationToken", "message": "x"}}
        )

    provider, credential = _microsoft(handler)
    result = provider.send_message(credential, ENVELOPE)
    assert result.status == "DEFINITIVELY_REJECTED"
    assert result.error_category == ErrorCategory.AUTH_FAILURE


def test_smtp_auth_failure_requires_reconnect(monkeypatch: pytest.MonkeyPatch) -> None:
    provider, credential = _smtp(monkeypatch, auth_ok=False)
    result = provider.send_message(credential, ENVELOPE)
    assert result.status == "DEFINITIVELY_REJECTED"
    assert result.error_category == ErrorCategory.AUTH_FAILURE

    classified = provider.classify_error(535)
    assert classified.requires_reconnect is True


def test_gmail_classify_error_401_requires_reconnect() -> None:
    provider = GmailProvider()
    classified = provider.classify_error(401, {"error": {"message": "x"}})
    assert classified.requires_reconnect is True


def test_microsoft_classify_error_401_requires_reconnect() -> None:
    provider = MicrosoftGraphProvider()
    classified = provider.classify_error(
        401, {"error": {"code": "InvalidAuthenticationToken"}}
    )
    assert classified.requires_reconnect is True


# -- Normalized temporary/retryable error --------------------------------


def test_gmail_5xx_is_retryable_temporary() -> None:
    provider = GmailProvider()
    classified = provider.classify_error(503, {"error": {"message": "x"}})
    assert classified.category == ErrorCategory.TEMPORARY_PROVIDER_ERROR
    assert classified.is_retryable is True


def test_microsoft_5xx_is_retryable_temporary() -> None:
    provider = MicrosoftGraphProvider()
    classified = provider.classify_error(503, {"error": {"message": "x"}})
    assert classified.category == ErrorCategory.TEMPORARY_PROVIDER_ERROR
    assert classified.is_retryable is True


def test_smtp_4xx_is_retryable_temporary() -> None:
    provider = SmtpProvider()
    classified = provider.classify_error(450)
    assert classified.category == ErrorCategory.TEMPORARY_PROVIDER_ERROR
    assert classified.is_retryable is True


# -- Normalized permanent recipient failure ------------------------------


def test_gmail_permanent_recipient_failure_never_requires_reconnect() -> None:
    provider = GmailProvider()
    classified = provider.classify_error(
        400, {"error": {"message": "Invalid recipient address"}}
    )
    assert classified.category == ErrorCategory.PERMANENT_RECIPIENT_FAILURE
    assert classified.requires_reconnect is False


def test_microsoft_permanent_recipient_failure_never_requires_reconnect() -> None:
    provider = MicrosoftGraphProvider()
    classified = provider.classify_error(
        400, {"error": {"code": "ErrorInvalidRecipients", "message": "bad"}}
    )
    assert classified.category == ErrorCategory.PERMANENT_RECIPIENT_FAILURE
    assert classified.requires_reconnect is False


def test_smtp_permanent_recipient_failure_never_requires_reconnect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider, credential = _smtp(monkeypatch, recipient_ok=False)
    result = provider.send_message(credential, ENVELOPE)
    assert result.status == "DEFINITIVELY_REJECTED"
    assert result.error_category == ErrorCategory.PERMANENT_RECIPIENT_FAILURE


# -- Ambiguous / UNKNOWN outcome -- never guessed as safe ------------------


def test_gmail_timeout_is_unknown_not_guessed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout")

    provider, credential = _gmail(handler)
    result = provider.send_message(credential, ENVELOPE)
    assert result.status == "UNKNOWN"
    assert result.error_category == ErrorCategory.UNKNOWN_OUTCOME


def test_microsoft_timeout_is_unknown_not_guessed() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("timeout")

    provider, credential = _microsoft(handler)
    result = provider.send_message(credential, ENVELOPE)
    assert result.status == "UNKNOWN"
    assert result.error_category == ErrorCategory.UNKNOWN_OUTCOME


def test_smtp_connection_lost_mid_submission_is_unknown_not_guessed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    server = FakeSmtpServer(
        mode="starttls", auth_ok=True, recipient_ok=True, hang_after_data_prompt=True
    )
    _trust_server_cert(monkeypatch, server)
    provider = SmtpProvider(resolver=_resolver_for(server), connect_timeout=1.0)

    result = provider.send_message(smtp_credential(), ENVELOPE)
    assert result.status == "UNKNOWN"
    assert result.error_category == ErrorCategory.UNKNOWN_OUTCOME
    server.stop()


# -- Unsupported-capability behavior: explicit, never a silent no-op ------


def test_smtp_lacks_oauth_flow_capability() -> None:
    provider = SmtpProvider()
    assert ProviderCapability.OAUTH_FLOW not in provider.capabilities
    with pytest.raises(UnsupportedCapabilityError):
        provider.get_authorization_url("state", "http://cb")


def test_smtp_lacks_credential_refresh_capability() -> None:
    provider = SmtpProvider()
    assert ProviderCapability.CREDENTIAL_REFRESH not in provider.capabilities
    with pytest.raises(UnsupportedCapabilityError):
        provider.refresh_token("rt")


def test_microsoft_lacks_token_revocation_capability() -> None:
    provider = MicrosoftGraphProvider()
    assert ProviderCapability.TOKEN_REVOCATION not in provider.capabilities
    with pytest.raises(UnsupportedCapabilityError):
        provider.revoke_token("rt")


def test_gmail_supports_every_capability() -> None:
    # Gmail is the one provider that legitimately supports the full set --
    # this is not required of Microsoft or SMTP.
    provider = GmailProvider()
    assert provider.capabilities == frozenset(
        {
            ProviderCapability.OAUTH_FLOW,
            ProviderCapability.IDENTITY_DISCOVERY,
            ProviderCapability.CONNECTION_VALIDATION,
            ProviderCapability.SEND,
            ProviderCapability.CREDENTIAL_REFRESH,
            ProviderCapability.TOKEN_REVOCATION,
        }
    )


# -- Connection validation is supported by every provider -----------------


def test_all_providers_support_connection_validation_capability() -> None:
    for provider in (GmailProvider(), MicrosoftGraphProvider(), SmtpProvider()):
        assert ProviderCapability.CONNECTION_VALIDATION in provider.capabilities
        assert ProviderCapability.SEND in provider.capabilities
