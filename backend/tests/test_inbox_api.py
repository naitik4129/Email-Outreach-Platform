from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import WorkspaceContext, get_db, get_workspace_context
from app.main import create_app


@pytest.fixture
def test_setup():
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

    ws_id = uuid.uuid4()
    user_id = uuid.uuid4()

    app = create_app()

    def override_get_db():
        yield session

    def override_get_workspace_context():
        return WorkspaceContext(workspace_id=ws_id, user_id=user_id, role_code="MANAGER")

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_workspace_context] = override_get_workspace_context

    client = TestClient(app)

    yield session, client, ws_id

    app.dependency_overrides.clear()
    session.close()


def test_api_list_conversations_empty(test_setup) -> None:
    session, client, ws_id = test_setup
    resp = client.get(f"/api/v1/workspaces/{ws_id}/inbox/conversations")
    assert resp.status_code == 200
    data = resp.json()
    assert data["items"] == []
    assert data["has_more"] is False
    assert data["unread_count"] == 0


def test_api_list_conversations_with_data(test_setup) -> None:
    session, client, ws_id = test_setup
    mb_id = uuid.uuid4()
    session.execute(
        text(
            """
            INSERT INTO public.mailboxes (id, workspace_id, provider, connection_state, health_state, original_address)
            VALUES (:id, :ws, 'GMAIL', 'CONNECTED', 'HEALTHY', 'team@outreach.com')
            """
        ),
        {"id": str(mb_id), "ws": str(ws_id)},
    )

    cid = uuid.uuid4()
    now = datetime.now(UTC)
    session.execute(
        text(
            """
            INSERT INTO public.conversations (id, workspace_id, mailbox_id, latest_activity_at, read_at)
            VALUES (:id, :ws, :mbid, :now, NULL)
            """
        ),
        {"id": str(cid), "ws": str(ws_id), "mbid": str(mb_id), "now": now},
    )
    session.execute(
        text(
            """
            INSERT INTO public.inbound_messages (id, workspace_id, mailbox_id, conversation_id, provider_message_id, subject, content_text, participants, received_at, observed_at)
            VALUES (:id, :ws, :mbid, :cid, 'msg-1', 'Demo Inquiry', 'Interested in product demo', :parts, :now, :now)
            """
        ),
        {
            "id": str(uuid.uuid4()),
            "ws": str(ws_id),
            "mbid": str(mb_id),
            "cid": str(cid),
            "parts": json.dumps({"from": {"email": "prospect@example.com", "name": "Prospect"}}),
            "now": now,
        },
    )
    session.commit()

    resp = client.get(f"/api/v1/workspaces/{ws_id}/inbox/conversations")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["items"]) == 1
    assert data["unread_count"] == 1

    item = data["items"][0]
    assert item["id"] == str(cid)
    assert item["subject"] == "Demo Inquiry"
    assert item["participant_email"] == "prospect@example.com"
    assert item["is_read"] is False
    assert item["mailbox_address"] == "team@outreach.com"


def test_api_conversation_detail(test_setup) -> None:
    session, client, ws_id = test_setup
    mb_id = uuid.uuid4()
    session.execute(
        text(
            """
            INSERT INTO public.mailboxes (id, workspace_id, provider, connection_state, health_state, original_address)
            VALUES (:id, :ws, 'GMAIL', 'CONNECTED', 'HEALTHY', 'team@outreach.com')
            """
        ),
        {"id": str(mb_id), "ws": str(ws_id)},
    )
    cid = uuid.uuid4()
    now = datetime.now(UTC)
    session.execute(
        text(
            """
            INSERT INTO public.conversations (id, workspace_id, mailbox_id, latest_activity_at)
            VALUES (:id, :ws, :mbid, :now)
            """
        ),
        {"id": str(cid), "ws": str(ws_id), "mbid": str(mb_id), "now": now},
    )
    session.commit()

    # Detail call
    resp = client.get(f"/api/v1/workspaces/{ws_id}/inbox/conversations/{cid}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == str(cid)
    assert data["workspace_id"] == str(ws_id)
    assert data["mailbox_address"] == "team@outreach.com"


def test_api_mark_read_and_unread(test_setup) -> None:
    session, client, ws_id = test_setup
    mb_id = uuid.uuid4()
    session.execute(
        text(
            """
            INSERT INTO public.mailboxes (id, workspace_id, provider, connection_state, health_state, original_address)
            VALUES (:id, :ws, 'GMAIL', 'CONNECTED', 'HEALTHY', 'team@outreach.com')
            """
        ),
        {"id": str(mb_id), "ws": str(ws_id)},
    )
    cid = uuid.uuid4()
    now = datetime.now(UTC)
    session.execute(
        text(
            """
            INSERT INTO public.conversations (id, workspace_id, mailbox_id, latest_activity_at, read_at)
            VALUES (:id, :ws, :mbid, :now, NULL)
            """
        ),
        {"id": str(cid), "ws": str(ws_id), "mbid": str(mb_id), "now": now},
    )
    session.commit()

    # 1. Mark read
    patch_resp = client.patch(f"/api/v1/workspaces/{ws_id}/inbox/conversations/{cid}/read")
    assert patch_resp.status_code == 200
    assert patch_resp.json()["is_read"] is True

    # 2. Mark unread
    patch_unread = client.patch(f"/api/v1/workspaces/{ws_id}/inbox/conversations/{cid}/unread")
    assert patch_unread.status_code == 200
    assert patch_unread.json()["is_read"] is False


def test_api_archive_and_unarchive(test_setup) -> None:
    session, client, ws_id = test_setup
    mb_id = uuid.uuid4()
    session.execute(
        text(
            """
            INSERT INTO public.mailboxes (id, workspace_id, provider, connection_state, health_state, original_address)
            VALUES (:id, :ws, 'GMAIL', 'CONNECTED', 'HEALTHY', 'team@outreach.com')
            """
        ),
        {"id": str(mb_id), "ws": str(ws_id)},
    )
    cid = uuid.uuid4()
    now = datetime.now(UTC)
    session.execute(
        text(
            """
            INSERT INTO public.conversations (id, workspace_id, mailbox_id, latest_activity_at, archived_at)
            VALUES (:id, :ws, :mbid, :now, NULL)
            """
        ),
        {"id": str(cid), "ws": str(ws_id), "mbid": str(mb_id), "now": now},
    )
    session.commit()

    # 1. Archive
    arch_resp = client.patch(f"/api/v1/workspaces/{ws_id}/inbox/conversations/{cid}/archive")
    assert arch_resp.status_code == 200
    assert arch_resp.json()["archived_at"] is not None

    # 2. Unarchive
    unarch_resp = client.patch(f"/api/v1/workspaces/{ws_id}/inbox/conversations/{cid}/unarchive")
    assert unarch_resp.status_code == 200
    assert unarch_resp.json()["archived_at"] is None


def test_api_sync_status(test_setup) -> None:
    session, client, ws_id = test_setup
    mb_id = uuid.uuid4()
    now = datetime.now(UTC)
    session.execute(
        text(
            """
            INSERT INTO public.mailboxes (id, workspace_id, provider, connection_state, health_state, original_address)
            VALUES (:id, :ws, 'MICROSOFT', 'CONNECTED', 'HEALTHY', 'outreach@corp.com')
            """
        ),
        {"id": str(mb_id), "ws": str(ws_id)},
    )
    session.execute(
        text(
            """
            INSERT INTO public.mailbox_sync_states (id, workspace_id, mailbox_id, status, last_complete_at)
            VALUES (:id, :ws, :mbid, 'HEALTHY', :now)
            """
        ),
        {"id": str(uuid.uuid4()), "ws": str(ws_id), "mbid": str(mb_id), "now": now},
    )
    session.commit()

    resp = client.get(f"/api/v1/workspaces/{ws_id}/inbox/sync-status")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["mailboxes"]) == 1
    assert data["mailboxes"][0]["email_address"] == "outreach@corp.com"
    assert data["mailboxes"][0]["provider"] == "MICROSOFT"
