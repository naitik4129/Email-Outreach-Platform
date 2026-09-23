from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session

from app.modules.mailboxes.providers.base import (
    ConnectionValidationResult,
    EmailProvider,
    OutboundMessageEnvelope,
    ProviderAccountIdentity,
    ProviderCapability,
    ProviderSendResult,
    TokenExchangeResult,
    TokenRefreshResult,
)
from app.modules.mailboxes.providers.registry import ProviderRegistry
from app.modules.mailboxes.providers.smtp import SmtpProvider
from app.modules.mailboxes.providers.ssrf import ResolvedTarget
from tests.support.fake_smtp_server import FakeSmtpServer
from tests.test_smtp_provider import _trust_server_cert

pytestmark = pytest.mark.integration


class FakeIntegrationProvider(EmailProvider):
    capabilities = frozenset(
        {
            ProviderCapability.OAUTH_FLOW,
            ProviderCapability.IDENTITY_DISCOVERY,
            ProviderCapability.CONNECTION_VALIDATION,
            ProviderCapability.SEND,
            ProviderCapability.CREDENTIAL_REFRESH,
            ProviderCapability.TOKEN_REVOCATION,
        }
    )

    def __init__(
        self,
        email: str = "integration.sender@example.com",
        initial_expires_in: int = 3600,
    ) -> None:
        self.email = email
        self.initial_expires_in = initial_expires_in
        self.sent_envelopes: list[OutboundMessageEnvelope] = []
        self.revoked_tokens: list[str] = []

    def get_authorization_url(
        self,
        state: str,
        redirect_uri: str,
        login_hint: str | None = None,
        code_challenge: str | None = None,
    ) -> str:
        return f"https://accounts.google.com/o/oauth2/v2/auth?state={state}&code_challenge={code_challenge}&redirect_uri={redirect_uri}"

    def exchange_code(
        self, code: str, redirect_uri: str, code_verifier: str
    ) -> TokenExchangeResult:
        return TokenExchangeResult(
            access_token="fake-integration-access-token",
            refresh_token="fake-integration-refresh-token",
            expires_in=self.initial_expires_in,
            token_type="Bearer",
            granted_scopes=["https://www.googleapis.com/auth/gmail.send"],
        )

    def refresh_token(self, refresh_token: str) -> TokenRefreshResult:
        return TokenRefreshResult(
            access_token="fake-refreshed-access-token",
            expires_in=3600,
            token_type="Bearer",
            refresh_token="fake-integration-refresh-token",
            granted_scopes=["https://www.googleapis.com/auth/gmail.send"],
        )

    def get_identity(self, access_token: str) -> ProviderAccountIdentity:
        return ProviderAccountIdentity(
            email_address=self.email,
            display_name="Integration Sender",
            provider_account_id=f"google-{self.email}",
        )

    def validate_connection(self, credential: dict) -> ConnectionValidationResult:
        return ConnectionValidationResult(
            is_valid=True,
            email_address=self.email,
            provider_account_id=f"google-{self.email}",
            scopes=[],
        )

    def send_message(
        self, credential: dict, envelope: OutboundMessageEnvelope
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


@pytest.fixture(autouse=True)
def fake_gmail_provider() -> Any:
    # Unique per test run: provider_account_id is globally unique across all
    # workspaces (mailboxes_provider_account_global_key), so a fixed email
    # here would collide with a leftover row from any prior run whose
    # teardown didn't complete (e.g. a failing assertion earlier in this same
    # test), causing an unrelated UniqueViolation on an unlucky rerun.
    unique_email = f"integration.sender+{uuid.uuid4().hex}@example.com"
    fake = FakeIntegrationProvider(email=unique_email)
    ProviderRegistry.register("GMAIL", fake)
    unique_ms_email = f"integration.ms.sender+{uuid.uuid4().hex}@example.com"
    ProviderRegistry.register(
        "MICROSOFT", FakeIntegrationProvider(email=unique_ms_email)
    )
    yield fake
    ProviderRegistry.reset()


def _bootstrap(client: TestClient, user: Any, name: str) -> dict:
    resp = client.post(
        "/api/v1/workspaces",
        json={"name": name},
        headers={**user.auth_header, "Idempotency-Key": uuid.uuid4().hex},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _connect_mailbox(client: TestClient, user: Any, workspace_id: str) -> dict:
    # 1. Start OAuth
    start_resp = client.post(
        f"/api/v1/workspaces/{workspace_id}/mailboxes/connect/gmail/start",
        json={"return_path": "/app/mailboxes"},
        headers=user.auth_header,
    )
    assert start_resp.status_code == 200, start_resp.text
    auth_url = start_resp.json()["authorization_url"]

    # Extract state token from auth_url query string
    parsed = urlparse(auth_url)
    qs = parse_qs(parsed.query)
    state = qs["state"][0]

    # 2. Complete OAuth
    comp_resp = client.post(
        f"/api/v1/workspaces/{workspace_id}/mailboxes/connect/gmail/complete",
        json={"code": "auth-test-code", "state": state},
        headers=user.auth_header,
    )
    assert comp_resp.status_code == 200, comp_resp.text
    return comp_resp.json()


def _connect_microsoft_mailbox(
    client: TestClient, user: Any, workspace_id: str
) -> dict:
    start_resp = client.post(
        f"/api/v1/workspaces/{workspace_id}/mailboxes/connect/microsoft/start",
        json={"return_path": "/app/mailboxes"},
        headers=user.auth_header,
    )
    assert start_resp.status_code == 200, start_resp.text
    auth_url = start_resp.json()["authorization_url"]

    parsed = urlparse(auth_url)
    qs = parse_qs(parsed.query)
    state = qs["state"][0]

    comp_resp = client.post(
        f"/api/v1/workspaces/{workspace_id}/mailboxes/connect/microsoft/complete",
        json={"code": "auth-test-code", "state": state},
        headers=user.auth_header,
    )
    assert comp_resp.status_code == 200, comp_resp.text
    return comp_resp.json()


def _connect_smtp_mailbox(
    client: TestClient, user: Any, workspace_id: str, *, email_address: str
) -> dict:
    resp = client.post(
        f"/api/v1/workspaces/{workspace_id}/mailboxes/connect/smtp",
        json={
            "host": "smtp.integration-test.example.com",
            "port": 587,
            "security_mode": "STARTTLS",
            "username": "integration-smtp-user",
            "password": "integration-smtp-password",
            "email_address": email_address,
            "sender_display_name": "Integration SMTP Sender",
        },
        headers=user.auth_header,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.fixture
def fake_smtp_server(monkeypatch: pytest.MonkeyPatch) -> Any:
    """A local, 127.0.0.1-only fake SMTP server for one test, wired into the
    registry via a per-instance resolver override.

    This is a deliberate, narrow, test-only dependency-injection seam --
    NOT a global SSRF bypass. It registers one specific SmtpProvider
    instance (constructed with a resolver that always points at this
    fixture's own loopback server) under ProviderRegistry for the duration
    of this test only; ProviderRegistry.reset() in teardown removes it.
    Every other test, and all non-test code, still goes through
    ProviderRegistry.get("SMTP") -> SmtpProvider() with the real
    SSRF-safe resolve_and_validate -- production behavior is untouched.
    """
    server = FakeSmtpServer(mode="starttls", auth_ok=True, recipient_ok=True)
    _trust_server_cert(monkeypatch, server)

    def _resolver(host: str, port: int, *, dns_timeout: float) -> ResolvedTarget:
        return ResolvedTarget(
            hostname="localhost",
            port=server.port,
            resolved_ip="127.0.0.1",
            address_family=0,
        )

    ProviderRegistry.register(
        "SMTP", SmtpProvider(resolver=_resolver, connect_timeout=5.0)
    )
    yield server
    server.stop()


def test_mailbox_connect_lifecycle_and_test_send(
    api_client: TestClient,
    make_test_user: Any,
    db_admin: Session,
    fake_gmail_provider: FakeIntegrationProvider,
) -> None:
    user = make_test_user()
    ws = _bootstrap(api_client, user, "Mailbox Test WS")
    workspace_id = ws["id"]

    # 1. Connect Mailbox
    connected = _connect_mailbox(api_client, user, workspace_id)
    mailbox_id = connected["mailbox_id"]
    assert connected["connection_state"] == "CONNECTED"
    assert connected["health_state"] == "HEALTHY"
    assert connected["email_address"] == fake_gmail_provider.email

    # 2. List Mailboxes
    list_resp = api_client.get(
        f"/api/v1/workspaces/{workspace_id}/mailboxes",
        headers=user.auth_header,
    )
    assert list_resp.status_code == 200
    items = list_resp.json()
    assert len(items) == 1
    assert items[0]["id"] == mailbox_id
    assert items[0]["email_address"] == fake_gmail_provider.email

    # 3. Get Mailbox Detail
    detail_resp = api_client.get(
        f"/api/v1/workspaces/{workspace_id}/mailboxes/{mailbox_id}",
        headers=user.auth_header,
    )
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert detail["id"] == mailbox_id
    assert detail["provider"] == "GMAIL"
    assert detail["connection_state"] == "CONNECTED"

    # 4. Update Mailbox
    update_resp = api_client.patch(
        f"/api/v1/workspaces/{workspace_id}/mailboxes/{mailbox_id}",
        json={
            "sender_display_name": "Updated Sender",
            "signature_html": "<p>Updated Signature</p>",
        },
        headers=user.auth_header,
    )
    assert update_resp.status_code == 200
    updated = update_resp.json()
    assert updated["sender_display_name"] == "Updated Sender"
    assert updated["signature_html"] == "<p>Updated Signature</p>"

    # 5. Controlled Test Send (Success)
    send_resp = api_client.post(
        f"/api/v1/workspaces/{workspace_id}/mailboxes/{mailbox_id}/test-send",
        json={"recipient_email": "test-recipient@example.com"},
        headers=user.auth_header,
    )
    assert send_resp.status_code == 200, send_resp.text
    send_res = send_resp.json()
    assert send_res["status"] == "SENT"
    assert send_res["provider_message_id"] is not None

    # 6. Controlled Test Send to Suppressed Address (Blocked)
    # Add suppression entry via raw sql
    recipient_email = "suppressed@example.com"
    addr_id = uuid.uuid4()
    db_admin.execute(
        text(
            """
            INSERT INTO recipient_addresses (
                id, workspace_id, canonical_address, original_display
            )
            VALUES (:id, :ws, :c_email, :o_email)
            ON CONFLICT (workspace_id, canonical_address, normalization_version)
            DO NOTHING
            """
        ),
        {
            "id": str(addr_id),
            "ws": workspace_id,
            "c_email": recipient_email,
            "o_email": recipient_email,
        },
    )
    actual_addr_id = db_admin.execute(
        text(
            """
            SELECT id FROM recipient_addresses
            WHERE workspace_id = :ws AND canonical_address = :c_email
            """
        ),
        {"ws": workspace_id, "c_email": recipient_email},
    ).scalar()
    db_admin.execute(
        text(
            """
            INSERT INTO suppressions (workspace_id, address_id, reason)
            VALUES (:ws, :addr, 'MANUAL')
            """
        ),
        {"ws": workspace_id, "addr": actual_addr_id},
    )
    db_admin.commit()

    supp_send_resp = api_client.post(
        f"/api/v1/workspaces/{workspace_id}/mailboxes/{mailbox_id}/test-send",
        json={"recipient_email": recipient_email},
        headers=user.auth_header,
    )
    assert supp_send_resp.status_code == 400
    assert supp_send_resp.json()["error"]["code"] == "suppressed_recipient"

    # 7. Disconnect Mailbox
    disc_resp = api_client.post(
        f"/api/v1/workspaces/{workspace_id}/mailboxes/{mailbox_id}/disconnect",
        headers=user.auth_header,
    )
    assert disc_resp.status_code == 200
    assert disc_resp.json()["connection_state"] == "DISCONNECTED"

    # Verify credentials destroyed in database
    conn_row = (
        db_admin.execute(
            text(
                """
            SELECT credential_ciphertext, nonce, encryption_key_id
            FROM mailbox_connections
            WHERE workspace_id = :ws AND mailbox_id = :mb
            ORDER BY generation DESC LIMIT 1
            """
            ),
            {"ws": workspace_id, "mb": mailbox_id},
        )
        .mappings()
        .first()
    )
    assert conn_row["credential_ciphertext"] is None

    # 8. Test Send on Disconnected Mailbox Fails
    disc_send_resp = api_client.post(
        f"/api/v1/workspaces/{workspace_id}/mailboxes/{mailbox_id}/test-send",
        json={"recipient_email": "test-recipient@example.com"},
        headers=user.auth_header,
    )
    assert disc_send_resp.status_code == 409


def test_test_send_refreshes_expiring_token_into_new_generation(
    api_client: TestClient,
    make_test_user: Any,
    db_admin: Session,
) -> None:
    """Regression test: send_controlled_test_email must refresh a
    near-expiry access token by rotating into a NEW mailbox_connections
    generation, not by updating the existing row in place. An in-place
    UPDATE of credential_ciphertext/nonce/expires_at is rejected by the
    mailbox_connections_guard_connection_history trigger (retained credential
    history is immutable) and app_connection isn't even granted UPDATE on
    expires_at/granted_scopes -- so the old code path raised a genuine
    "permission denied for table mailbox_connections" 500 on any real
    Gmail account whose token had actually expired, which FakeIntegration
    Provider's fixed expires_in=3600 never triggered in the lifecycle test.
    """
    # mailbox_connections is append-only by design (mailbox_connections_
    # guard_connection_history rejects any UPDATE that changes expires_at,
    # credentials, or several other columns on an existing row -- exactly
    # the invariant this test exists to prove the app respects). So the only
    # way to produce an already-expired connection is to have the initial
    # OAuth exchange itself return an expired token, not to back-date one
    # afterward with raw SQL.
    already_expired_provider = FakeIntegrationProvider(
        email=f"refresh.sender+{uuid.uuid4().hex}@example.com",
        initial_expires_in=-3600,
    )
    ProviderRegistry.register("GMAIL", already_expired_provider)

    user = make_test_user()
    ws = _bootstrap(api_client, user, "Token Refresh WS")
    workspace_id = ws["id"]

    connected = _connect_mailbox(api_client, user, workspace_id)
    mailbox_id = connected["mailbox_id"]

    send_resp = api_client.post(
        f"/api/v1/workspaces/{workspace_id}/mailboxes/{mailbox_id}/test-send",
        json={"recipient_email": "refresh-recipient@example.com"},
        headers=user.auth_header,
    )
    assert send_resp.status_code == 200, send_resp.text
    assert send_resp.json()["status"] == "SENT"

    mailbox_row = db_admin.execute(
        text(
            """
            SELECT current_connection_generation, connected_generation, connection_state
            FROM mailboxes WHERE workspace_id = :ws AND id = :mb
            """
        ),
        {"ws": workspace_id, "mb": mailbox_id},
    ).mappings().one()
    assert mailbox_row["current_connection_generation"] == 2
    assert mailbox_row["connected_generation"] == 2
    assert mailbox_row["connection_state"] == "CONNECTED"

    new_conn = db_admin.execute(
        text(
            """
            SELECT credential_ciphertext FROM mailbox_connections
            WHERE workspace_id = :ws AND mailbox_id = :mb AND generation = 2
            """
        ),
        {"ws": workspace_id, "mb": mailbox_id},
    ).mappings().one()
    assert new_conn["credential_ciphertext"] is not None


def test_credential_secrecy_app_api_denied_access_to_connections(
    raw_db: Session,
    make_test_user: Any,
    api_client: TestClient,
) -> None:
    user = make_test_user()
    ws = _bootstrap(api_client, user, "Secrecy WS")
    workspace_id = ws["id"]
    _connect_mailbox(api_client, user, workspace_id)

    # raw_db has role `app_api`
    # app_api must NOT have SELECT permission on public.mailbox_connections
    with pytest.raises(ProgrammingError) as exc_info:
        raw_db.execute(
            text("SELECT credential_ciphertext FROM public.mailbox_connections")
        )
    assert "permission denied" in str(exc_info.value).lower()


def test_cross_tenant_isolation(
    api_client: TestClient,
    make_test_user: Any,
) -> None:
    user_a = make_test_user()
    user_b = make_test_user()

    ws_a = _bootstrap(api_client, user_a, "Tenant A")
    ws_b = _bootstrap(api_client, user_b, "Tenant B")

    # Connect mailbox in workspace A
    conn_a = _connect_mailbox(api_client, user_a, ws_a["id"])
    mailbox_a_id = conn_a["mailbox_id"]

    # User B tries to view workspace A mailboxes
    resp_list = api_client.get(
        f"/api/v1/workspaces/{ws_a['id']}/mailboxes",
        headers=user_b.auth_header,
    )
    assert resp_list.status_code in (403, 404)

    # User B tries to view mailbox A in workspace B
    resp_get = api_client.get(
        f"/api/v1/workspaces/{ws_b['id']}/mailboxes/{mailbox_a_id}",
        headers=user_b.auth_header,
    )
    assert resp_get.status_code == 404

    # User B tries to disconnect mailbox A
    resp_disc = api_client.post(
        f"/api/v1/workspaces/{ws_b['id']}/mailboxes/{mailbox_a_id}/disconnect",
        headers=user_b.auth_header,
    )
    assert resp_disc.status_code == 404


def test_complete_gmail_oauth_rejects_workspace_mismatch(
    api_client: TestClient,
    make_test_user: Any,
) -> None:
    """A flow started under one workspace cannot be completed via another
    workspace's URL, even for a user who is a member of both. This is
    enforced by RLS (oauth_flows_connection_update requires
    workspace_id = app_current_workspace_id(), which get_workspace_context
    binds from the URL's workspace_id before the claim query runs) rather
    than by application code, so the claim itself fails as if the state
    token were unrecognized under that workspace.
    """
    user = make_test_user()
    ws_a = _bootstrap(api_client, user, "Flow Started Here")
    ws_b = _bootstrap(api_client, user, "Wrong Workspace URL")

    # Start OAuth under workspace A
    start_resp = api_client.post(
        f"/api/v1/workspaces/{ws_a['id']}/mailboxes/connect/gmail/start",
        json={"return_path": "/app/mailboxes"},
        headers=user.auth_header,
    )
    assert start_resp.status_code == 200, start_resp.text
    parsed = urlparse(start_resp.json()["authorization_url"])
    state = parse_qs(parsed.query)["state"][0]

    # Attempt to complete it under workspace B's URL
    comp_resp = api_client.post(
        f"/api/v1/workspaces/{ws_b['id']}/mailboxes/connect/gmail/complete",
        json={"code": "auth-test-code", "state": state},
        headers=user.auth_header,
    )
    assert comp_resp.status_code == 400, comp_resp.text
    assert comp_resp.json()["error"]["code"] == "invalid_oauth_state"

    # No mailbox should have been created in either workspace
    for workspace_id in (ws_a["id"], ws_b["id"]):
        list_resp = api_client.get(
            f"/api/v1/workspaces/{workspace_id}/mailboxes",
            headers=user.auth_header,
        )
        assert list_resp.status_code == 200
        assert list_resp.json() == []

    # The flow can still be completed correctly under its own workspace
    comp_resp_ok = api_client.post(
        f"/api/v1/workspaces/{ws_a['id']}/mailboxes/connect/gmail/complete",
        json={"code": "auth-test-code", "state": state},
        headers=user.auth_header,
    )
    assert comp_resp_ok.status_code == 200, comp_resp_ok.text


def test_rbac_matrix_permissions(
    api_client: TestClient,
    make_test_user: Any,
    db_admin: Session,
) -> None:
    owner = make_test_user()
    viewer = make_test_user()

    ws = _bootstrap(api_client, owner, "RBAC WS")
    workspace_id = ws["id"]

    # Lazily creates the viewer's profiles row (see app/api/deps.py's
    # get_current_user) -- workspace_memberships_user_fkey requires one to
    # already exist before the raw membership insert below.
    api_client.get("/api/v1/me", headers=viewer.auth_header)

    # Add viewer to workspace with role VIEWER
    db_admin.execute(
        text(
            """
            INSERT INTO workspace_memberships (id, workspace_id, user_id, role_code)
            VALUES (:id, :ws, :user, 'VIEWER')
            """
        ),
        {"id": str(uuid.uuid4()), "ws": workspace_id, "user": viewer.user_id},
    )
    db_admin.commit()

    # Viewer CAN list mailboxes (READ)
    list_resp = api_client.get(
        f"/api/v1/workspaces/{workspace_id}/mailboxes",
        headers=viewer.auth_header,
    )
    assert list_resp.status_code == 200

    # Viewer CANNOT start connect flow (WRITE required)
    start_resp = api_client.post(
        f"/api/v1/workspaces/{workspace_id}/mailboxes/connect/gmail/start",
        json={"return_path": "/app/mailboxes"},
        headers=viewer.auth_header,
    )
    assert start_resp.status_code == 403


# -------------------------------------------------------------------------
# Microsoft OAuth (mirrors the Gmail lifecycle test above)
# -------------------------------------------------------------------------


def test_microsoft_mailbox_connect_lifecycle_and_test_send(
    api_client: TestClient,
    make_test_user: Any,
) -> None:
    user = make_test_user()
    ws = _bootstrap(api_client, user, "Microsoft Mailbox Test WS")
    workspace_id = ws["id"]

    connected = _connect_microsoft_mailbox(api_client, user, workspace_id)
    mailbox_id = connected["mailbox_id"]
    assert connected["provider"] == "MICROSOFT"
    assert connected["connection_state"] == "CONNECTED"
    assert connected["health_state"] == "HEALTHY"

    detail_resp = api_client.get(
        f"/api/v1/workspaces/{workspace_id}/mailboxes/{mailbox_id}",
        headers=user.auth_header,
    )
    assert detail_resp.status_code == 200
    assert detail_resp.json()["provider"] == "MICROSOFT"

    send_resp = api_client.post(
        f"/api/v1/workspaces/{workspace_id}/mailboxes/{mailbox_id}/test-send",
        json={"recipient_email": "ms-test-recipient@example.com"},
        headers=user.auth_header,
    )
    assert send_resp.status_code == 200, send_resp.text
    assert send_resp.json()["status"] == "SENT"

    reconnect_resp = api_client.post(
        f"/api/v1/workspaces/{workspace_id}/mailboxes/{mailbox_id}/reconnect/microsoft",
        headers=user.auth_header,
    )
    assert reconnect_resp.status_code == 200, reconnect_resp.text
    assert "authorization_url" in reconnect_resp.json()

    disc_resp = api_client.post(
        f"/api/v1/workspaces/{workspace_id}/mailboxes/{mailbox_id}/disconnect",
        headers=user.auth_header,
    )
    assert disc_resp.status_code == 200
    assert disc_resp.json()["connection_state"] == "DISCONNECTED"


def test_microsoft_reconnect_wrong_provider_rejected(
    api_client: TestClient,
    make_test_user: Any,
) -> None:
    """A Gmail mailbox cannot be "reconnected" via the Microsoft endpoint --
    verifies the provider-precondition guard end-to-end, not just at the
    service-layer unit-test level."""
    user = make_test_user()
    ws = _bootstrap(api_client, user, "Wrong Provider Reconnect WS")
    workspace_id = ws["id"]

    connected = _connect_mailbox(api_client, user, workspace_id)
    mailbox_id = connected["mailbox_id"]

    resp = api_client.post(
        f"/api/v1/workspaces/{workspace_id}/mailboxes/{mailbox_id}/reconnect/microsoft",
        headers=user.auth_header,
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "bad_request"


# -------------------------------------------------------------------------
# Custom SMTP (local fixture server only -- never real network)
# -------------------------------------------------------------------------


def test_smtp_mailbox_connect_lifecycle_and_test_send(
    api_client: TestClient,
    make_test_user: Any,
    db_admin: Session,
    fake_smtp_server: FakeSmtpServer,
) -> None:
    user = make_test_user()
    ws = _bootstrap(api_client, user, "SMTP Mailbox Test WS")
    workspace_id = ws["id"]
    email_address = f"smtp.integration+{uuid.uuid4().hex}@example.com"

    connected = _connect_smtp_mailbox(
        api_client, user, workspace_id, email_address=email_address
    )
    mailbox_id = connected["mailbox_id"]
    assert connected["provider"] == "SMTP"
    assert connected["connection_state"] == "CONNECTED"
    assert connected["health_state"] == "HEALTHY"

    detail_resp = api_client.get(
        f"/api/v1/workspaces/{workspace_id}/mailboxes/{mailbox_id}",
        headers=user.auth_header,
    )
    assert detail_resp.status_code == 200
    detail = detail_resp.json()
    assert detail["provider"] == "SMTP"
    assert detail["smtp_config"]["host"] == "smtp.integration-test.example.com"
    assert detail["smtp_config"]["port"] == 587
    assert "password" not in detail["smtp_config"]

    send_resp = api_client.post(
        f"/api/v1/workspaces/{workspace_id}/mailboxes/{mailbox_id}/test-send",
        json={"recipient_email": "smtp-test-recipient@example.com"},
        headers=user.auth_header,
    )
    assert send_resp.status_code == 200, send_resp.text
    assert send_resp.json()["status"] == "SENT"

    # Update configuration: password omitted -> existing credential is
    # reused, only the display name changes.
    update_resp = api_client.patch(
        f"/api/v1/workspaces/{workspace_id}/mailboxes/{mailbox_id}/smtp",
        json={"sender_display_name": "Renamed SMTP Sender"},
        headers=user.auth_header,
    )
    assert update_resp.status_code == 200, update_resp.text
    assert update_resp.json()["sender_display_name"] == "Renamed SMTP Sender"

    disc_resp = api_client.post(
        f"/api/v1/workspaces/{workspace_id}/mailboxes/{mailbox_id}/disconnect",
        headers=user.auth_header,
    )
    assert disc_resp.status_code == 200
    assert disc_resp.json()["connection_state"] == "DISCONNECTED"

    conn_row = (
        db_admin.execute(
            text(
                """
                SELECT credential_ciphertext FROM mailbox_connections
                WHERE workspace_id = :ws AND mailbox_id = :mb
                ORDER BY generation DESC LIMIT 1
                """
            ),
            {"ws": workspace_id, "mb": mailbox_id},
        )
        .mappings()
        .first()
    )
    assert conn_row["credential_ciphertext"] is None


def test_smtp_connect_rejects_unsafe_host_without_creating_mailbox(
    api_client: TestClient,
    make_test_user: Any,
) -> None:
    """Uses the REAL SmtpProvider/SSRF path (no fake_smtp_server fixture
    here -- ProviderRegistry.get("SMTP") is the untouched production
    path), proving a private-network host is rejected end-to-end through
    the API and never produces a mailbox row."""
    user = make_test_user()
    ws = _bootstrap(api_client, user, "SMTP SSRF WS")
    workspace_id = ws["id"]

    resp = api_client.post(
        f"/api/v1/workspaces/{workspace_id}/mailboxes/connect/smtp",
        json={
            "host": "127.0.0.1",
            "port": 587,
            "security_mode": "STARTTLS",
            "username": "user",
            "password": "password",
            "email_address": "ssrf-attempt@example.com",
        },
        headers=user.auth_header,
    )
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["code"] == "unsafe_destination"

    list_resp = api_client.get(
        f"/api/v1/workspaces/{workspace_id}/mailboxes",
        headers=user.auth_header,
    )
    assert list_resp.status_code == 200
    assert list_resp.json() == []


def test_credential_secrecy_app_api_denied_access_to_smtp_connections(
    raw_db: Session,
    make_test_user: Any,
    api_client: TestClient,
    fake_smtp_server: FakeSmtpServer,
) -> None:
    user = make_test_user()
    ws = _bootstrap(api_client, user, "SMTP Secrecy WS")
    workspace_id = ws["id"]
    _connect_smtp_mailbox(
        api_client,
        user,
        workspace_id,
        email_address=f"smtp.secrecy+{uuid.uuid4().hex}@example.com",
    )

    with pytest.raises(ProgrammingError) as exc_info:
        raw_db.execute(
            text("SELECT credential_ciphertext FROM public.mailbox_connections")
        )
    assert "permission denied" in str(exc_info.value).lower()
