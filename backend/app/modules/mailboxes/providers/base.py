from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Literal, Protocol


class ErrorCategory(StrEnum):
    AUTH_FAILURE = "AUTH_FAILURE"
    INSUFFICIENT_SCOPE = "INSUFFICIENT_SCOPE"
    RATE_LIMIT = "RATE_LIMIT"
    TEMPORARY_PROVIDER_ERROR = "TEMPORARY_PROVIDER_ERROR"
    PERMANENT_RECIPIENT_FAILURE = "PERMANENT_RECIPIENT_FAILURE"
    POLICY_REJECTION = "POLICY_REJECTION"
    NETWORK_ERROR = "NETWORK_ERROR"
    UNKNOWN_OUTCOME = "UNKNOWN_OUTCOME"


@dataclass(frozen=True)
class TokenExchangeResult:
    access_token: str
    refresh_token: str | None
    token_type: str
    expires_in: int
    granted_scopes: list[str]
    id_token: str | None = None


@dataclass(frozen=True)
class TokenRefreshResult:
    access_token: str
    refresh_token: str | None
    token_type: str
    expires_in: int
    granted_scopes: list[str]


@dataclass(frozen=True)
class ProviderAccountIdentity:
    provider_account_id: str
    email_address: str
    display_name: str | None = None


@dataclass(frozen=True)
class ConnectionValidationResult:
    is_valid: bool
    email_address: str
    provider_account_id: str
    scopes: list[str]


@dataclass(frozen=True)
class OutboundMessageEnvelope:
    to_address: str
    from_address: str
    from_name: str | None = None
    subject: str = ""
    body_html: str = ""
    body_text: str | None = None
    rfc_message_id: str | None = None


@dataclass(frozen=True)
class ProviderSendResult:
    status: Literal["ACCEPTED", "DEFINITIVELY_REJECTED", "UNKNOWN"]
    provider_message_id: str | None = None
    provider_thread_id: str | None = None
    accepted_at: datetime | None = None
    error_category: str | None = None
    error_code: str | None = None
    raw_response: dict[str, Any] | None = None


@dataclass(frozen=True)
class ClassifiedProviderError:
    category: ErrorCategory
    is_retryable: bool
    requires_reconnect: bool
    safe_message: str
    provider_code: str | None = None


class EmailProvider(Protocol):
    """Generic abstraction for email provider integrations."""

    def get_authorization_url(
        self,
        state: str,
        redirect_uri: str,
        login_hint: str | None = None,
        code_challenge: str | None = None,
    ) -> str: ...

    def exchange_code(
        self,
        code: str,
        redirect_uri: str,
        code_verifier: str | None = None,
    ) -> TokenExchangeResult: ...

    def refresh_token(self, refresh_token: str) -> TokenRefreshResult: ...

    def get_identity(self, access_token: str) -> ProviderAccountIdentity: ...

    def validate_connection(self, access_token: str) -> ConnectionValidationResult: ...

    def send_message(
        self,
        access_token: str,
        envelope: OutboundMessageEnvelope,
    ) -> ProviderSendResult: ...

    def classify_error(
        self,
        error: Exception | int | dict[str, Any],
    ) -> ClassifiedProviderError: ...

    def revoke_token(self, token: str) -> bool: ...
