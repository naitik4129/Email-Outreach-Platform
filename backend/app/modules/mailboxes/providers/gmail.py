from __future__ import annotations

import base64
import re
from datetime import UTC, datetime
from email.message import EmailMessage
from typing import Any
from urllib.parse import urlencode

import httpx

from app.core.config import Settings
from app.core.errors import AppError
from app.modules.mailboxes.providers.base import (
    ClassifiedProviderError,
    ConnectionValidationResult,
    EmailProvider,
    ErrorCategory,
    OutboundMessageEnvelope,
    ProviderAccountIdentity,
    ProviderSendResult,
    TokenExchangeResult,
    TokenRefreshResult,
)

GMAIL_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GMAIL_TOKEN_URL = "https://oauth2.googleapis.com/token"
# Google's OIDC userinfo endpoint, not the Gmail API's own profile endpoint:
# it only requires the "openid"/"userinfo.email" scopes we already request,
# whereas Gmail's users.getProfile requires a mailbox-read scope we don't ask for.
GMAIL_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
GMAIL_SEND_URL = "https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
GMAIL_REVOKE_URL = "https://oauth2.googleapis.com/revoke"

GMAIL_DEFAULT_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/gmail.send",
]

_CRLF_PATTERN = re.compile(r"[\r\n]")


def validate_header_value(name: str, value: str | None) -> None:
    """Ensure no carriage return or newline characters exist in header values."""
    if value and _CRLF_PATTERN.search(value):
        raise AppError(
            "header_injection",
            f"Header injection detected: newline characters not permitted in {name}",
            status_code=422,
        )


class GmailProvider(EmailProvider):
    """Authoritative Gmail API and OAuth 2.0 provider adapter."""

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        settings = Settings.current()
        self.client_id = client_id or settings.google_client_id
        self.client_secret = client_secret or settings.google_client_secret
        self._http_client = http_client
        self._owned_http_client: httpx.Client | None = None

    def _get_client(self) -> httpx.Client:
        if self._http_client is not None:
            return self._http_client
        if self._owned_http_client is None:
            self._owned_http_client = httpx.Client(
                timeout=httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=15.0)
            )
        return self._owned_http_client

    def get_authorization_url(
        self,
        state: str,
        redirect_uri: str,
        login_hint: str | None = None,
        code_challenge: str | None = None,
    ) -> str:
        if not self.client_id:
            raise AppError(
                "configuration_error",
                "Gmail client ID is not configured",
                status_code=503,
            )

        params: dict[str, str] = {
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": " ".join(GMAIL_DEFAULT_SCOPES),
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
        if code_challenge:
            params["code_challenge"] = code_challenge
            params["code_challenge_method"] = "S256"
        if login_hint:
            params["login_hint"] = login_hint

        return f"{GMAIL_AUTH_URL}?{urlencode(params)}"

    def exchange_code(
        self,
        code: str,
        redirect_uri: str,
        code_verifier: str | None = None,
    ) -> TokenExchangeResult:
        if not self.client_id or not self.client_secret:
            raise AppError(
                "configuration_error",
                "Gmail OAuth credentials are not configured",
                status_code=503,
            )

        data: dict[str, str] = {
            "code": code,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }
        if code_verifier:
            data["code_verifier"] = code_verifier

        client = self._get_client()
        try:
            resp = client.post(GMAIL_TOKEN_URL, data=data)
        except httpx.TransportError as exc:
            raise AppError(
                "provider_error",
                "Failed to reach Google token endpoint",
                status_code=502,
            ) from exc

        if resp.status_code != 200:
            classified = self.classify_error(
                resp.status_code, resp.json() if resp.content else {}
            )
            raise AppError(
                "provider_error",
                classified.safe_message,
                status_code=400 if resp.status_code < 500 else 502,
            )

        payload = resp.json()
        if not payload.get("access_token"):
            raise AppError(
                "provider_error",
                "Google token endpoint did not return an access token",
                status_code=502,
            )
        scopes = (
            payload.get("scope", "").split()
            if payload.get("scope")
            else GMAIL_DEFAULT_SCOPES
        )
        return TokenExchangeResult(
            access_token=payload["access_token"],
            refresh_token=payload.get("refresh_token"),
            token_type=payload.get("token_type", "Bearer"),
            expires_in=int(payload.get("expires_in", 3600)),
            granted_scopes=scopes,
            id_token=payload.get("id_token"),
        )

    def refresh_token(self, refresh_token: str) -> TokenRefreshResult:
        if not self.client_id or not self.client_secret:
            raise AppError(
                "configuration_error",
                "Gmail OAuth credentials are not configured",
                status_code=503,
            )

        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }

        client = self._get_client()
        try:
            resp = client.post(GMAIL_TOKEN_URL, data=data)
        except httpx.TransportError as exc:
            raise AppError(
                "provider_error",
                "Failed to reach Google token endpoint during refresh",
                status_code=502,
            ) from exc

        if resp.status_code != 200:
            error_data = resp.json() if resp.content else {}
            classified = self.classify_error(resp.status_code, error_data)
            raise AppError(
                "auth_failure" if classified.requires_reconnect else "provider_error",
                classified.safe_message,
                status_code=401 if classified.requires_reconnect else 502,
            )

        payload = resp.json()
        if not payload.get("access_token"):
            raise AppError(
                "provider_error",
                "Google token endpoint did not return an access token during refresh",
                status_code=502,
            )
        scopes = (
            payload.get("scope", "").split()
            if payload.get("scope")
            else GMAIL_DEFAULT_SCOPES
        )
        return TokenRefreshResult(
            access_token=payload["access_token"],
            refresh_token=payload.get("refresh_token"),  # Google may omit on refresh
            token_type=payload.get("token_type", "Bearer"),
            expires_in=int(payload.get("expires_in", 3600)),
            granted_scopes=scopes,
        )

    def get_identity(self, access_token: str) -> ProviderAccountIdentity:
        client = self._get_client()
        headers = {"Authorization": f"Bearer {access_token}"}

        try:
            resp = client.get(GMAIL_USERINFO_URL, headers=headers)
        except httpx.TransportError as exc:
            raise AppError(
                "provider_error",
                "Failed to reach Google userinfo endpoint",
                status_code=502,
            ) from exc

        if resp.status_code != 200:
            classified = self.classify_error(
                resp.status_code, resp.json() if resp.content else {}
            )
            raise AppError(
                "provider_error",
                f"Failed to retrieve Gmail identity: {classified.safe_message}",
                status_code=401 if classified.requires_reconnect else 502,
            )

        payload = resp.json()
        email_address = payload.get("email")
        if not email_address:
            raise AppError(
                "provider_error",
                "Google userinfo did not return a valid email address",
                status_code=502,
            )

        return ProviderAccountIdentity(
            provider_account_id=email_address.lower(),
            email_address=email_address.lower(),
        )

    def validate_connection(self, access_token: str) -> ConnectionValidationResult:
        identity = self.get_identity(access_token)
        return ConnectionValidationResult(
            is_valid=True,
            email_address=identity.email_address,
            provider_account_id=identity.provider_account_id,
            scopes=GMAIL_DEFAULT_SCOPES,
        )

    def send_message(
        self,
        access_token: str,
        envelope: OutboundMessageEnvelope,
    ) -> ProviderSendResult:
        # 1. Header injection validation
        validate_header_value("To", envelope.to_address)
        validate_header_value("From", envelope.from_address)
        validate_header_value("Sender Name", envelope.from_name)
        validate_header_value("Subject", envelope.subject)

        # 2. Build RFC 5322 MIME message
        msg = EmailMessage()
        if envelope.from_name:
            msg["From"] = f"{envelope.from_name} <{envelope.from_address}>"
        else:
            msg["From"] = envelope.from_address
        msg["To"] = envelope.to_address
        msg["Subject"] = envelope.subject

        if envelope.rfc_message_id:
            rfc_id = envelope.rfc_message_id.strip("<>")
            msg["Message-ID"] = f"<{rfc_id}>"

        if envelope.body_html:
            msg.set_content(
                envelope.body_text
                or "This message requires an HTML-capable email client."
            )
            msg.add_alternative(envelope.body_html, subtype="html")
        else:
            msg.set_content(envelope.body_text or "")

        raw_bytes = msg.as_bytes()
        encoded_raw = base64.urlsafe_b64encode(raw_bytes).decode("ascii")

        # 3. Submit to Gmail API
        client = self._get_client()
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }
        body = {"raw": encoded_raw}

        try:
            resp = client.post(GMAIL_SEND_URL, headers=headers, json=body)
        except httpx.TimeoutException:
            # Ambiguous: submission may have succeeded remotely before
            # the connection dropped
            return ProviderSendResult(
                status="UNKNOWN",
                error_category=ErrorCategory.UNKNOWN_OUTCOME,
                error_code="request_timeout",
            )
        except httpx.TransportError as exc:
            return ProviderSendResult(
                status="DEFINITIVELY_REJECTED",
                error_category=ErrorCategory.NETWORK_ERROR,
                error_code="network_error",
                raw_response={"detail": str(exc)},
            )

        if resp.status_code == 200:
            data = resp.json()
            return ProviderSendResult(
                status="ACCEPTED",
                provider_message_id=data.get("id"),
                provider_thread_id=data.get("threadId"),
                accepted_at=datetime.now(UTC),
                raw_response=data,
            )

        # Non-200 response
        error_payload = resp.json() if resp.content else {}
        classified = self.classify_error(resp.status_code, error_payload)
        return ProviderSendResult(
            status="DEFINITIVELY_REJECTED",
            error_category=classified.category,
            error_code=classified.provider_code or str(resp.status_code),
            raw_response=error_payload,
        )

    def classify_error(
        self,
        error: Exception | int | dict[str, Any],
        details: dict[str, Any] | None = None,
    ) -> ClassifiedProviderError:
        status_code = error if isinstance(error, int) else None
        body = details or (error if isinstance(error, dict) else {})

        error_val = body.get("error") if isinstance(body, dict) else None
        if isinstance(error_val, dict):
            error_code = str(error_val.get("code", status_code or ""))
            error_message = str(error_val.get("message", ""))
        elif isinstance(error_val, str):
            error_code = error_val
            error_message = str(body.get("error_description", error_val))
        else:
            error_code = str(status_code or "")
            error_message = str(body)

        # Invalid grant / authorization revoked
        if (
            status_code == 401
            or "invalid_grant" in str(body).lower()
            or "token has been expired or revoked" in error_message.lower()
        ):
            return ClassifiedProviderError(
                category=ErrorCategory.AUTH_FAILURE,
                is_retryable=False,
                requires_reconnect=True,
                safe_message=(
                    "Gmail authorization has expired or been revoked. "
                    "Reconnection required."
                ),
                provider_code="invalid_grant",
            )

        # Insufficient scope / permission denied for the requested API
        if status_code == 403:
            return ClassifiedProviderError(
                category=ErrorCategory.INSUFFICIENT_SCOPE,
                is_retryable=False,
                requires_reconnect=True,
                safe_message=(
                    "Google did not grant the permissions this connection needs. "
                    "Please reconnect and approve all requested access."
                ),
                provider_code=error_code or "insufficient_scope",
            )

        # Rate limit / Quota exceeded
        if (
            status_code == 429
            or "ratelimit" in error_message.lower()
            or "quota" in error_message.lower()
            or "userRateLimitExceeded" in str(body)
        ):
            return ClassifiedProviderError(
                category=ErrorCategory.RATE_LIMIT,
                is_retryable=True,
                requires_reconnect=False,
                safe_message=(
                    "Gmail API rate limit exceeded. Please wait before retrying."
                ),
                provider_code="rate_limit_exceeded",
            )

        # Permanent recipient / invalid argument
        if status_code == 400 and (
            "invalid argument" in error_message.lower()
            or "recipient" in error_message.lower()
        ):
            return ClassifiedProviderError(
                category=ErrorCategory.PERMANENT_RECIPIENT_FAILURE,
                is_retryable=False,
                requires_reconnect=False,
                safe_message=(
                    "The recipient email address is invalid or was rejected by Gmail."
                ),
                provider_code="invalid_recipient",
            )

        # Server-side temporary errors (5xx)
        if status_code and status_code >= 500:
            return ClassifiedProviderError(
                category=ErrorCategory.TEMPORARY_PROVIDER_ERROR,
                is_retryable=True,
                requires_reconnect=False,
                safe_message=(
                    "Google service is temporarily unavailable. "
                    "Please try again later."
                ),
                provider_code=f"http_{status_code}",
            )

        return ClassifiedProviderError(
            category=ErrorCategory.POLICY_REJECTION,
            is_retryable=False,
            requires_reconnect=False,
            safe_message=("Google rejected the request due to a policy restriction."),
            provider_code=error_code or "unknown",
        )

    def revoke_token(self, token: str) -> bool:
        client = self._get_client()
        try:
            resp = client.post(GMAIL_REVOKE_URL, params={"token": token})
            return resp.status_code == 200
        except Exception:
            return False
