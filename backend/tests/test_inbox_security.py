from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import WorkspaceContext, get_db, get_workspace_context
from app.main import create_app
from app.modules.templates.sanitizer import sanitize_html_preview


@pytest.fixture
def security_test_setup():
    engine = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    session_factory = sessionmaker(bind=engine)
    session = session_factory()
    session.execute(text("ATTACH DATABASE ':memory:' AS public;"))

    session.execute(
        text(
            """
        CREATE TABLE public.sequence_steps (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            position INTEGER NOT NULL
        );
        """
        )
    )
    session.execute(
        text(
            """
        CREATE TABLE public.campaign_enrollments (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            lead_id TEXT
        );
        """
        )
    )
    session.execute(
        text(
            """
        CREATE TABLE public.conversations (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            mailbox_id TEXT NOT NULL,
            provider_thread_id TEXT,
            local_anchor_id TEXT,
            campaign_summary_id TEXT,
            created_at TIMESTAMP,
            latest_activity_at TIMESTAMP,
            read_at TIMESTAMP,
            archived_at TIMESTAMP,
            version INTEGER NOT NULL DEFAULT 1,
            updated_at TIMESTAMP
        );
        """
        )
    )

    session.execute(
        text(
            """
        CREATE TABLE public.mailboxes (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            connection_state TEXT NOT NULL DEFAULT 'CONNECTED',
            health_state TEXT NOT NULL DEFAULT 'HEALTHY',
            original_address TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            updated_at TIMESTAMP
        );
        """
        )
    )

    session.execute(
        text(
            """
        CREATE TABLE public.mailbox_sync_states (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            mailbox_id TEXT NOT NULL,
            connection_generation INTEGER NOT NULL DEFAULT 1,
            sync_scope TEXT NOT NULL DEFAULT 'INBOX',
            cursor_data TEXT,
            status TEXT NOT NULL DEFAULT 'HEALTHY',
            failure_count INTEGER NOT NULL DEFAULT 0,
            last_complete_at TIMESTAMP,
            version INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMP,
            updated_at TIMESTAMP,
            UNIQUE (workspace_id, mailbox_id, sync_scope)
        );
        """
        )
    )

    session.execute(
        text(
            """
        CREATE TABLE public.campaigns (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'RUNNING',
            created_at TIMESTAMP,
            updated_at TIMESTAMP
        );
        """
        )
    )

    session.execute(
        text(
            """
        CREATE TABLE public.inbound_messages (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            mailbox_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL,
            connection_generation INTEGER NOT NULL DEFAULT 1,
            provider_message_id TEXT NOT NULL,
            rfc_message_id TEXT,
            in_reply_to TEXT,
            references_header TEXT,
            participants TEXT NOT NULL DEFAULT '{}',
            subject TEXT,
            content_text TEXT,
            received_at TIMESTAMP NOT NULL,
            observed_at TIMESTAMP NOT NULL,
            direction TEXT NOT NULL DEFAULT 'INBOUND',
            classification TEXT,
            association_status TEXT NOT NULL DEFAULT 'UNRESOLVED',
            version INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMP,
            updated_at TIMESTAMP
        );
        """
        )
    )

    session.execute(
        text(
            """
        CREATE TABLE public.messages (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            mailbox_id TEXT NOT NULL,
            campaign_id TEXT,
            enrollment_id TEXT,
            sequence_id TEXT,
            step_id TEXT,
            conversation_id TEXT,
            rfc_message_id TEXT,
            content_subject TEXT,
            content_body_html TEXT,
            frozen_destination TEXT,
            frozen_sender_address TEXT,
            frozen_sender_name TEXT,
            status TEXT NOT NULL DEFAULT 'SENT',
            accepted_at TIMESTAMP,
            created_at TIMESTAMP,
            updated_at TIMESTAMP
        );
        """
        )
    )

    session.execute(
        text(
            """
        CREATE TABLE public.inbound_outreach_links (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            mailbox_id TEXT NOT NULL,
            inbound_message_id TEXT NOT NULL,
            outbound_message_id TEXT NOT NULL,
            campaign_id TEXT,
            enrollment_id TEXT,
            evidence_type TEXT NOT NULL,
            confidence TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'CONFIRMED',
            matched_at TIMESTAMP,
            version INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMP,
            updated_at TIMESTAMP
        );
        """
        )
    )

    session.commit()

    ws_a = uuid.uuid4()
    ws_b = uuid.uuid4()
    user_id = uuid.uuid4()

    # Seed conversation in Workspace B
    mb_b = uuid.uuid4()
    session.execute(
        text(
            """
            INSERT INTO public.mailboxes (id, workspace_id, provider, connection_state, health_state, original_address)
            VALUES (:id, :ws, 'GMAIL', 'CONNECTED', 'HEALTHY', 'tenant_b@corp.com')
            """
        ),
        {"id": str(mb_b), "ws": str(ws_b)},
    )
    conv_b = uuid.uuid4()
    now = datetime.now(UTC)
    session.execute(
        text(
            """
            INSERT INTO public.conversations (id, workspace_id, mailbox_id, latest_activity_at)
            VALUES (:id, :ws, :mbid, :now)
            """
        ),
        {"id": str(conv_b), "ws": str(ws_b), "mbid": str(mb_b), "now": now},
    )
    session.execute(
        text(
            """
            INSERT INTO public.inbound_messages (id, workspace_id, mailbox_id, conversation_id, provider_message_id, subject, content_text, participants, received_at, observed_at)
            VALUES (:id, :ws, :mbid, :cid, 'b-msg-1', 'Secret Tenant B Data', 'Confidential reply', :parts, :now, :now)
            """
        ),
        {
            "id": str(uuid.uuid4()),
            "ws": str(ws_b),
            "mbid": str(mb_b),
            "cid": str(conv_b),
            "parts": json.dumps({"from": {"email": "secret@tenantb.com"}}),
            "now": now,
        },
    )
    session.commit()

    # Setup client simulating user in Workspace A
    app = create_app()

    current_role = {"code": "MEMBER"}

    def override_get_db():
        yield session

    def override_get_workspace_context():
        return WorkspaceContext(workspace_id=ws_a, user_id=user_id, role_code=current_role["code"])

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_workspace_context] = override_get_workspace_context

    client = TestClient(app)

    yield session, client, ws_a, ws_b, conv_b, current_role

    app.dependency_overrides.clear()
    session.close()


def test_cross_tenant_conversation_isolation(security_test_setup) -> None:
    session, client, ws_a, ws_b, conv_b, _ = security_test_setup

    # User in Workspace A requests conversation belonging to Workspace B
    resp = client.get(f"/api/v1/workspaces/{ws_a}/inbox/conversations/{conv_b}")
    # Must return 404 to avoid leaking existence of Workspace B resource
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"
    assert resp.json()["error"]["message"] == "Conversation not found"


def test_cross_tenant_actions_blocked(security_test_setup) -> None:
    session, client, ws_a, ws_b, conv_b, _ = security_test_setup

    # Attempt to mark read on Workspace B conversation
    resp_read = client.patch(f"/api/v1/workspaces/{ws_a}/inbox/conversations/{conv_b}/read")
    assert resp_read.status_code == 404

    # Attempt to mark unread on Workspace B conversation
    resp_unread = client.patch(f"/api/v1/workspaces/{ws_a}/inbox/conversations/{conv_b}/unread")
    assert resp_unread.status_code == 404


def test_cross_tenant_data_never_in_list(security_test_setup) -> None:
    session, client, ws_a, ws_b, conv_b, _ = security_test_setup

    # List conversations for Workspace A
    resp = client.get(f"/api/v1/workspaces/{ws_a}/inbox/conversations")
    assert resp.status_code == 200
    items = resp.json()["items"]
    # Absolutely zero items from Workspace B
    assert len(items) == 0

    # Search for tenant B secret content in Workspace A
    search_resp = client.get(f"/api/v1/workspaces/{ws_a}/inbox/conversations?q=secret")
    assert search_resp.status_code == 200
    assert len(search_resp.json()["items"]) == 0


def test_archive_rbac_enforcement(security_test_setup) -> None:
    session, client, ws_a, ws_b, _, current_role = security_test_setup

    # Seed a conversation in Workspace A
    mb_a = uuid.uuid4()
    session.execute(
        text(
            """
            INSERT INTO public.mailboxes (id, workspace_id, provider, connection_state, health_state, original_address)
            VALUES (:id, :ws, 'GMAIL', 'CONNECTED', 'HEALTHY', 'tenant_a@corp.com')
            """
        ),
        {"id": str(mb_a), "ws": str(ws_a)},
    )
    conv_a = uuid.uuid4()
    session.execute(
        text(
            """
            INSERT INTO public.conversations (id, workspace_id, mailbox_id, latest_activity_at)
            VALUES (:id, :ws, :mbid, :now)
            """
        ),
        {"id": str(conv_a), "ws": str(ws_a), "mbid": str(mb_a), "now": datetime.now(UTC)},
    )
    session.commit()

    # 1. MEMBER role cannot archive
    current_role["code"] = "MEMBER"
    resp_member = client.patch(f"/api/v1/workspaces/{ws_a}/inbox/conversations/{conv_a}/archive")
    assert resp_member.status_code == 403

    # 2. VIEWER role cannot archive
    current_role["code"] = "VIEWER"
    resp_viewer = client.patch(f"/api/v1/workspaces/{ws_a}/inbox/conversations/{conv_a}/archive")
    assert resp_viewer.status_code == 403

    # 3. MANAGER role can archive
    current_role["code"] = "MANAGER"
    resp_manager = client.patch(f"/api/v1/workspaces/{ws_a}/inbox/conversations/{conv_a}/archive")
    assert resp_manager.status_code == 200
    assert resp_manager.json()["archived_at"] is not None

    # 4. ADMIN role can unarchive
    current_role["code"] = "ADMIN"
    resp_admin = client.patch(f"/api/v1/workspaces/{ws_a}/inbox/conversations/{conv_a}/unarchive")
    assert resp_admin.status_code == 200
    assert resp_admin.json()["archived_at"] is None


def test_html_sanitizer_xss_vectors() -> None:
    # Test malicious script tags
    dirty_script = "Hello <script>alert('xss')</script> world"
    assert "<script>" not in sanitize_html_preview(dirty_script)

    # Test event handlers
    dirty_event = "<p onclick=\"alert('hack')\">Click me</p>"
    clean_event = sanitize_html_preview(dirty_event)
    assert "onclick" not in clean_event
    assert "Click me" in clean_event

    # Test dangerous URI schemes
    dirty_uri = "<a href=\"javascript:alert('pwn')\">Safe Link</a>"
    clean_uri = sanitize_html_preview(dirty_uri)
    assert "javascript:" not in clean_uri

    # Test iframe injection
    dirty_iframe = "<iframe src=\"https://evil.com/phish\"></iframe>"
    assert "<iframe" not in sanitize_html_preview(dirty_iframe)
