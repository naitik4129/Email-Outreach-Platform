from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode, urlparse

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
    ProviderCapability,
    ProviderInboundMessage,
    ProviderSendResult,
    SyncPageResult,
    TokenExchangeResult,
    TokenRefreshResult,
    UnsupportedCapabilityError,
)
from app.modules.mailboxes.providers.message_builder import validate_header_value

GRAPH_ME_URL = "https://graph.microsoft.com/v1.0/me"
GRAPH_SEND_URL = "https://graph.microsoft.com/v1.0/me/sendMail"

# Least-privilege scopes only: identity + offline refresh + send. No
# Mail.Read/Mail.ReadWrite/Contacts.Read/Calendars.Read -- reply sync and
# inbox are explicitly out of scope for this phase.
MICROSOFT_DEFAULT_SCOPES = [
    "openid",
    "profile",
    "email",
    "offline_access",
    "https://graph.microsoft.com/Mail.Send",
]


class MicrosoftGraphProvider(EmailProvider):
    """Microsoft Graph API and OAuth 2.0 provider adapter (v2.0 endpoint)."""

    capabilities = frozenset(
        {
            ProviderCapability.OAUTH_FLOW,
            ProviderCapability.IDENTITY_DISCOVERY,
            ProviderCapability.CONNECTION_VALIDATION,
            ProviderCapability.SEND,
            ProviderCapability.CREDENTIAL_REFRESH,
            ProviderCapability.LOOKUP_MESSAGE,
            ProviderCapability.REPLY_SYNC,
        }
    )

    def __init__(
        self,
        client_id: str | None = None,
        client_secret: str | None = None,
        authority: str | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        settings = Settings.current()
        self.client_id = client_id or settings.microsoft_client_id
        self.client_secret = client_secret or settings.microsoft_client_secret
        self.authority = authority or settings.microsoft_authority
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

    @property
    def _authorize_url(self) -> str:
        return f"{self.authority}/oauth2/v2.0/authorize"

    @property
    def _token_url(self) -> str:
        return f"{self.authority}/oauth2/v2.0/token"

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
                "Microsoft client ID is not configured",
                status_code=503,
            )

        params: dict[str, str] = {
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "response_mode": "query",
            "scope": " ".join(MICROSOFT_DEFAULT_SCOPES),
            "state": state,
        }
        if code_challenge:
            params["code_challenge"] = code_challenge
            params["code_challenge_method"] = "S256"
        if login_hint:
            params["login_hint"] = login_hint

        return f"{self._authorize_url}?{urlencode(params)}"

    def exchange_code(
        self,
        code: str,
        redirect_uri: str,
        code_verifier: str | None = None,
    ) -> TokenExchangeResult:
        if not self.client_id or not self.client_secret:
            raise AppError(
                "configuration_error",
                "Microsoft OAuth credentials are not configured",
                status_code=503,
            )

        data: dict[str, str] = {
            "code": code,
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
            "scope": " ".join(MICROSOFT_DEFAULT_SCOPES),
        }
        if code_verifier:
            data["code_verifier"] = code_verifier

        client = self._get_client()
        try:
            resp = client.post(self._token_url, data=data)
        except httpx.TransportError as exc:
            raise AppError(
                "provider_error",
                "Failed to reach Microsoft token endpoint",
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
                "Microsoft token endpoint did not return an access token",
                status_code=502,
            )
        scopes = (
            payload.get("scope", "").split()
            if payload.get("scope")
            else MICROSOFT_DEFAULT_SCOPES
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
                "Microsoft OAuth credentials are not configured",
                status_code=503,
            )

        data = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
            "scope": " ".join(MICROSOFT_DEFAULT_SCOPES),
        }

        client = self._get_client()
        try:
            resp = client.post(self._token_url, data=data)
        except httpx.TransportError as exc:
            raise AppError(
                "provider_error",
                "Failed to reach Microsoft token endpoint during refresh",
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
                "Microsoft token endpoint did not return an access token "
                "during refresh",
                status_code=502,
            )
        scopes = (
            payload.get("scope", "").split()
            if payload.get("scope")
            else MICROSOFT_DEFAULT_SCOPES
        )
        return TokenRefreshResult(
            access_token=payload["access_token"],
            refresh_token=payload.get("refresh_token"),
            token_type=payload.get("token_type", "Bearer"),
            expires_in=int(payload.get("expires_in", 3600)),
            granted_scopes=scopes,
        )

    def get_identity(self, access_token: str) -> ProviderAccountIdentity:
        client = self._get_client()
        headers = {"Authorization": f"Bearer {access_token}"}

        try:
            resp = client.get(GRAPH_ME_URL, headers=headers)
        except httpx.TransportError as exc:
            raise AppError(
                "provider_error",
                "Failed to reach Microsoft Graph /me endpoint",
                status_code=502,
            ) from exc

        if resp.status_code != 200:
            classified = self.classify_error(
                resp.status_code, resp.json() if resp.content else {}
            )
            raise AppError(
                "provider_error",
                f"Failed to retrieve Microsoft identity: {classified.safe_message}",
                status_code=401 if classified.requires_reconnect else 502,
            )

        payload = resp.json()
        # Some Microsoft account types (e.g. certain personal accounts)
        # return a null "mail" field; userPrincipalName is always present
        # and is the correct fallback identity.
        email_address = payload.get("mail") or payload.get("userPrincipalName")
        if not email_address:
            raise AppError(
                "provider_error",
                "Microsoft Graph did not return a valid account identity",
                status_code=502,
            )
        account_id = payload.get("id") or email_address

        return ProviderAccountIdentity(
            provider_account_id=str(account_id),
            email_address=str(email_address).lower(),
            display_name=payload.get("displayName"),
        )

    def validate_connection(
        self, credential: Mapping[str, Any]
    ) -> ConnectionValidationResult:
        identity = self.get_identity(credential["access_token"])
        return ConnectionValidationResult(
            is_valid=True,
            email_address=identity.email_address,
            provider_account_id=identity.provider_account_id,
            scopes=MICROSOFT_DEFAULT_SCOPES,
        )

    def send_message(
        self,
        credential: Mapping[str, Any],
        envelope: OutboundMessageEnvelope,
    ) -> ProviderSendResult:
        access_token = credential["access_token"]

        # Header-injection guard, applied as defense-in-depth even though
        # this transport is a JSON body, not raw MIME headers -- kept
        # consistent with Gmail/SMTP so the same guarantee holds everywhere.
        validate_header_value("To", envelope.to_address)
        validate_header_value("From", envelope.from_address)
        validate_header_value("Sender Name", envelope.from_name)
        validate_header_value("Subject", envelope.subject)

        message: dict[str, Any] = {
            "subject": envelope.subject,
            "body": {
                "contentType": "HTML",
                "content": envelope.body_html or envelope.body_text or "",
            },
            "toRecipients": [{"emailAddress": {"address": envelope.to_address}}],
            "from": {
                "emailAddress": {
                    "address": envelope.from_address,
                    "name": envelope.from_name or envelope.from_address,
                }
            },
        }
        body = {"message": message, "saveToSentItems": "true"}

        client = self._get_client()
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        }

        try:
            resp = client.post(GRAPH_SEND_URL, headers=headers, json=body)
        except httpx.TimeoutException:
            # Ambiguous: the request may have been accepted remotely
            # before the connection dropped.
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

        if resp.status_code == 202:
            # Graph's sendMail returns 202 Accepted with an EMPTY body: this
            # means "accepted for processing", not "delivered", and no
            # message ID is available synchronously. Per the provider
            # architecture's reconciliation rules, the absence of a
            # provider_message_id must never be treated as a rejection.
            return ProviderSendResult(
                status="ACCEPTED",
                provider_message_id=None,
                provider_thread_id=None,
                accepted_at=datetime.now(UTC),
                raw_response=None,
            )

        error_payload = resp.json() if resp.content else {}
        classified = self.classify_error(resp.status_code, error_payload)

        retry_after_seconds: float | None = None
        raw_retry_after = resp.headers.get("Retry-After")
        if raw_retry_after:
            try:
                retry_after_seconds = float(raw_retry_after)
            except ValueError:
                retry_after_seconds = None

        return ProviderSendResult(
            status="DEFINITIVELY_REJECTED",
            error_category=classified.category,
            error_code=classified.provider_code or str(resp.status_code),
            raw_response=error_payload,
            retry_after_seconds=retry_after_seconds,
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
        else:
            error_code = str(status_code or "")
            error_message = str(body)

        lowered_code = error_code.lower()
        lowered_message = error_message.lower()

        if status_code == 401 or "invalidauthenticationtoken" in lowered_code:
            return ClassifiedProviderError(
                category=ErrorCategory.AUTH_FAILURE,
                is_retryable=False,
                requires_reconnect=True,
                safe_message=(
                    "Microsoft authorization has expired or been revoked. "
                    "Reconnection required."
                ),
                provider_code=error_code or "invalid_token",
            )

        if status_code == 403 or "erroraccessdenied" in lowered_code:
            return ClassifiedProviderError(
                category=ErrorCategory.INSUFFICIENT_SCOPE,
                is_retryable=False,
                requires_reconnect=True,
                safe_message=(
                    "Microsoft did not grant the permissions this connection "
                    "needs. Please reconnect and approve all requested access."
                ),
                provider_code=error_code or "insufficient_scope",
            )

        if (
            status_code == 429
            or "throttl" in lowered_code
            or "throttl" in lowered_message
        ):
            return ClassifiedProviderError(
                category=ErrorCategory.RATE_LIMIT,
                is_retryable=True,
                requires_reconnect=False,
                safe_message=(
                    "Microsoft Graph rate limit exceeded. Please wait before retrying."
                ),
                provider_code="rate_limit_exceeded",
            )

        if status_code == 400 and (
            "recipient" in lowered_message or "invalidrecipients" in lowered_code
        ):
            return ClassifiedProviderError(
                category=ErrorCategory.PERMANENT_RECIPIENT_FAILURE,
                is_retryable=False,
                requires_reconnect=False,
                safe_message=(
                    "The recipient email address is invalid or was rejected "
                    "by Microsoft Graph."
                ),
                provider_code="invalid_recipient",
            )

        if status_code and status_code >= 500:
            return ClassifiedProviderError(
                category=ErrorCategory.TEMPORARY_PROVIDER_ERROR,
                is_retryable=True,
                requires_reconnect=False,
                safe_message=(
                    "Microsoft Graph is temporarily unavailable. "
                    "Please try again later."
                ),
                provider_code=f"http_{status_code}",
            )

        return ClassifiedProviderError(
            category=ErrorCategory.POLICY_REJECTION,
            is_retryable=False,
            requires_reconnect=False,
            safe_message=(
                "Microsoft Graph rejected the request due to a policy restriction."
            ),
            provider_code=error_code or "unknown",
        )

    def revoke_token(self, token: str) -> bool:
        # Microsoft's v2.0 endpoint exposes no refresh-token revocation
        # API analogous to Google's /revoke -- there is nothing to call.
        # TOKEN_REVOCATION is deliberately excluded from `capabilities`;
        # raising here (rather than silently returning False) keeps that
        # fact explicit. The existing disconnect flow already wraps this
        # call in a broad `except Exception: pass` (remote revocation
        # failure must never block a local disconnect), so this is safe.
        raise UnsupportedCapabilityError(
            "MICROSOFT", ProviderCapability.TOKEN_REVOCATION
        )

    def lookup_message(
        self,
        credential: Mapping[str, Any],
        rfc_message_id: str,
    ) -> ProviderSendResult | None:
        """Query Microsoft Graph API to check if an email with the given
        rfc_message_id exists in the user's mailbox."""
        access_token = credential.get("access_token")
        if not access_token or not rfc_message_id:
            return None

        client = self._get_client()
        headers = {"Authorization": f"Bearer {access_token}"}
        # Graph supports filtering messages by internetMessageId
        clean_id = rfc_message_id.strip("<>")
        query = f"internetMessageId eq '<{clean_id}>'"
        try:
            resp = client.get(
                "https://graph.microsoft.com/v1.0/me/messages",
                headers=headers,
                params={"$filter": query, "$top": 1, "$select": "id,conversationId"},
            )
            if resp.status_code != 200:
                return None
            data = resp.json()
            messages = data.get("value", [])
            if not messages:
                return None
            found_msg = messages[0]
            return ProviderSendResult(
                status="ACCEPTED",
                provider_message_id=found_msg.get("id"),
                provider_thread_id=found_msg.get("conversationId"),
                accepted_at=datetime.now(UTC),
                raw_response=found_msg,
            )
        except Exception:
            return None

    def _validate_graph_url(self, url: str) -> None:
        """Validate opaque continuation/delta URL against SSRF."""
        parsed = urlparse(url)
        if parsed.scheme != "https":
            raise AppError("ssrf_rejected", f"Invalid URL scheme: {parsed.scheme}", status_code=400)
        if parsed.netloc.lower() != "graph.microsoft.com":
            raise AppError("ssrf_rejected", f"Untrusted host in continuation URL: {parsed.netloc}", status_code=400)
        if not parsed.path.startswith("/v1.0/"):
            raise AppError("ssrf_rejected", f"Unexpected path in continuation URL: {parsed.path}", status_code=400)

    def sync_inbound_messages(
        self,
        credential: Mapping[str, Any],
        cursor: str | None = None,
        page_size: int = 50,
    ) -> SyncPageResult:
        access_token = credential.get("access_token")
        if not access_token:
            raise AppError("auth_failure", "Missing access token for Microsoft sync", status_code=401)

        client = self._get_client()
        headers = {
            "Authorization": f"Bearer {access_token}",
            "Prefer": f"odata.maxpagesize={min(max(page_size, 1), 100)}",
        }

        # Determine URL
        if cursor:
            self._validate_graph_url(cursor)
            target_url = cursor
            params = None
        else:
            target_url = "https://graph.microsoft.com/v1.0/me/mailFolders/Inbox/messages/delta"
            params = {
                "$select": (
                    "id,conversationId,internetMessageId,subject,from,"
                    "toRecipients,ccRecipients,bccRecipients,receivedDateTime,"
                    "sentDateTime,body,hasAttachments,internetMessageHeaders"
                )
            }

        try:
            resp = client.get(target_url, headers=headers, params=params)
        except Exception as exc:
            classified = self.classify_error(exc)
            if classified.category == ErrorCategory.RATE_LIMIT:
                return SyncPageResult(messages=[], has_more=True, retry_after_seconds=30.0)
            raise

        if resp.status_code == 410:
            return SyncPageResult(messages=[], resync_required=True)
        elif resp.status_code in (401, 403):
            classified = self.classify_error(resp.status_code)
            raise AppError(
                classified.category.value.lower(),
                classified.safe_message,
                status_code=resp.status_code,
            )
        elif resp.status_code == 429:
            retry_after = float(resp.headers.get("Retry-After", 30))
            return SyncPageResult(messages=[], has_more=True, retry_after_seconds=retry_after)
        elif resp.status_code != 200:
            try:
                err_data = resp.json().get("error", {})
                if err_data.get("code") in ("resyncRequired", "ResyncRequired"):
                    return SyncPageResult(messages=[], resync_required=True)
            except Exception:
                pass
            classified = self.classify_error(resp.status_code)
            raise AppError(
                classified.category.value.lower(),
                classified.safe_message,
                status_code=resp.status_code,
            )

        data = resp.json()
        raw_messages = data.get("value", [])
        next_link = data.get("@odata.nextLink")
        delta_link = data.get("@odata.deltaLink")

        parsed_messages: list[ProviderInboundMessage] = []
        for msg in raw_messages:
            if "@removed" in msg:
                continue
            parsed = self._parse_graph_message(msg)
            if parsed:
                parsed_messages.append(parsed)

        next_cursor = next_link or delta_link
        has_more = bool(next_link)
        synced_checkpoint = delta_link if delta_link else None

        return SyncPageResult(
            messages=parsed_messages,
            next_cursor=next_cursor,
            has_more=has_more,
            resync_required=False,
            synced_checkpoint=synced_checkpoint,
        )

    def _parse_graph_message(self, msg: dict[str, Any]) -> ProviderInboundMessage | None:
        mid = msg.get("id")
        if not mid:
            return None

        thread_id = msg.get("conversationId")
        rfc_id = msg.get("internetMessageId")
        subject = msg.get("subject") or ""

        from_dict = msg.get("from", {}).get("emailAddress", {})
        from_addr = from_dict.get("address", "")
        from_name = from_dict.get("name")

        to_addrs = [
            r.get("emailAddress", {}).get("address", "")
            for r in msg.get("toRecipients", [])
            if r.get("emailAddress", {}).get("address")
        ]
        cc_addrs = [
            r.get("emailAddress", {}).get("address", "")
            for r in msg.get("ccRecipients", [])
            if r.get("emailAddress", {}).get("address")
        ]
        bcc_addrs = [
            r.get("emailAddress", {}).get("address", "")
            for r in msg.get("bccRecipients", [])
            if r.get("emailAddress", {}).get("address")
        ]

        received_at = datetime.now(UTC)
        received_str = msg.get("receivedDateTime")
        if received_str:
            try:
                if received_str.endswith("Z"):
                    received_str = received_str[:-1] + "+00:00"
                received_at = datetime.fromisoformat(received_str)
            except Exception:
                pass

        body_obj = msg.get("body", {})
        content_type = (body_obj.get("contentType") or "").lower()
        content_val = body_obj.get("content")
        body_text = None
        body_html = None
        if "html" in content_type:
            body_html = content_val
        else:
            body_text = content_val

        headers: dict[str, str] = {}
        for h in msg.get("internetMessageHeaders", []):
            name = (h.get("name") or "").strip()
            val = (h.get("value") or "").strip()
            if name:
                headers[name.lower()] = val
                headers[name] = val

        in_reply_to = headers.get("in-reply-to")
        references_str = headers.get("references", "")
        references = references_str.split() if references_str else []

        if not in_reply_to or not references:
            for prop in msg.get("singleValueExtendedProperties", []):
                prop_id = prop.get("id", "")
                if not in_reply_to and ("0x1042" in prop_id or "InReplyTo" in prop_id):
                    in_reply_to = prop.get("value")
                if not references and ("0x1039" in prop_id or "References" in prop_id):
                    ref_val = prop.get("value", "")
                    if ref_val:
                        references = ref_val.split()

        auto_sub = headers.get("auto-submitted", "").lower()
        precedence = headers.get("precedence", "").lower()
        x_autoreply = headers.get("x-autoreply", "").lower()
        is_automated = bool(
            (auto_sub and auto_sub != "no")
            or precedence in ("bulk", "junk", "auto_reply")
            or x_autoreply in ("yes", "true")
        )

        return ProviderInboundMessage(
            provider_message_id=str(mid),
            provider_thread_id=str(thread_id) if thread_id else None,
            rfc_message_id=str(rfc_id) if rfc_id else None,
            in_reply_to=in_reply_to,
            references=references,
            from_address=from_addr,
            from_name=from_name or None,
            to_addresses=to_addrs,
            cc_addresses=cc_addrs,
            bcc_addresses=bcc_addrs,
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            received_at=received_at,
            headers=headers,
            is_automated=is_automated,
        )
