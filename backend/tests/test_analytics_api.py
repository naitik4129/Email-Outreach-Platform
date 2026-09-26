from __future__ import annotations

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
def api_setup():
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
        CREATE TABLE public.workspaces (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL
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
            original_address TEXT NOT NULL,
            provider TEXT NOT NULL,
            connection_state TEXT NOT NULL DEFAULT 'CONNECTED',
            health_state TEXT NOT NULL DEFAULT 'HEALTHY',
            policy_state TEXT NOT NULL DEFAULT 'ENABLED'
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
            status TEXT NOT NULL DEFAULT 'RUNNING'
        );
        """
        )
    )
    session.execute(
        text(
            """
        CREATE TABLE public.campaign_sequences (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            campaign_id TEXT NOT NULL
        );
        """
        )
    )
    session.execute(
        text(
            """
        CREATE TABLE public.sequence_steps (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            sequence_id TEXT NOT NULL,
            position INTEGER NOT NULL,
            kind TEXT NOT NULL,
            email_subject TEXT
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
            campaign_id TEXT NOT NULL,
            address_id TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'ACTIVE'
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
            campaign_id TEXT,
            enrollment_id TEXT,
            sequence_id TEXT,
            step_id TEXT,
            mailbox_id TEXT NOT NULL,
            address_id TEXT NOT NULL,
            status TEXT NOT NULL,
            accepted_at TIMESTAMP,
            updated_at TIMESTAMP
        );
        """
        )
    )
    session.execute(
        text(
            """
        CREATE TABLE public.message_attempts (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            message_id TEXT NOT NULL,
            mailbox_id TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            evidence_state TEXT NOT NULL,
            error_category TEXT,
            started_at TIMESTAMP
        );
        """
        )
    )
    session.execute(
        text(
            """
        CREATE TABLE public.recipient_outcomes (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            enrollment_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            source_key TEXT NOT NULL,
            occurred_at TIMESTAMP NOT NULL,
            inbound_message_id TEXT
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
            status TEXT NOT NULL,
            matched_at TIMESTAMP
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
            classification TEXT
        );
        """
        )
    )
    session.execute(
        text(
            """
        CREATE TABLE public.message_events (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            message_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            bounce_type TEXT,
            bounce_code TEXT,
            source TEXT NOT NULL DEFAULT 'TEST',
            detail TEXT,
            first_occurred_at TIMESTAMP NOT NULL,
            last_occurred_at TIMESTAMP NOT NULL,
            occurrence_count INTEGER NOT NULL DEFAULT 1,
            UNIQUE (workspace_id, message_id, kind)
        );
        """
        )
    )
    session.execute(
        text(
            """
        CREATE TABLE public.safety_holds (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            target_kind TEXT NOT NULL,
            target_mailbox_id TEXT,
            status TEXT NOT NULL DEFAULT 'ACTIVE',
            reason TEXT NOT NULL
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

    def override_get_workspace_context(workspace_id: uuid.UUID = ws_id):
        # Enforce that if request is for another workspace_id, we raise or mirror deps
        return WorkspaceContext(
            workspace_id=workspace_id, user_id=user_id, role_code="MANAGER"
        )

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_workspace_context] = override_get_workspace_context

    client = TestClient(app)

    yield session, client, ws_id

    app.dependency_overrides.clear()
    session.close()


def test_api_workspace_overview_empty(api_setup) -> None:
    session, client, ws_id = api_setup
    resp = client.get(f"/api/v1/workspaces/{ws_id}/analytics/overview")
    assert resp.status_code == 200
    data = resp.json()
    assert data["workspace_id"] == str(ws_id)
    assert data["emails_sent"] == 0
    assert data["replies"] == 0
    assert data["bounces"] == 0
    assert data["open_tracking_supported"] is False
    assert len(data["trend"]) == 30  # Default 30 days zero-filled


def test_api_campaign_analytics(api_setup) -> None:
    session, client, ws_id = api_setup
    camp_id = uuid.uuid4()
    mb_id = uuid.uuid4()
    addr_id = uuid.uuid4()
    enr_id = uuid.uuid4()
    now = datetime.now(UTC)

    session.execute(
        text("INSERT INTO public.workspaces (id, name) VALUES (:id, 'Test')"),
        {"id": str(ws_id)},
    )
    session.execute(
        text(
            "INSERT INTO public.campaigns (id, workspace_id, name, status) VALUES (:id, :ws, 'Spring Launch', 'RUNNING')"
        ),
        {"id": str(camp_id), "ws": str(ws_id)},
    )
    session.execute(
        text(
            "INSERT INTO public.mailboxes (id, workspace_id, original_address, provider) VALUES (:id, :ws, 'mb@test.com', 'GMAIL')"
        ),
        {"id": str(mb_id), "ws": str(ws_id)},
    )
    session.execute(
        text(
            "INSERT INTO public.campaign_enrollments (id, workspace_id, campaign_id, address_id) VALUES (:e, :ws, :cid, :a)"
        ),
        {
            "e": str(enr_id),
            "ws": str(ws_id),
            "cid": str(camp_id),
            "a": str(addr_id),
        },
    )
    session.execute(
        text(
            "INSERT INTO public.messages (id, workspace_id, campaign_id, enrollment_id, mailbox_id, address_id, status, accepted_at) VALUES (:m, :ws, :cid, :e, :mb, :a, 'SENT', :now)"
        ),
        {
            "m": str(uuid.uuid4()),
            "ws": str(ws_id),
            "cid": str(camp_id),
            "e": str(enr_id),
            "mb": str(mb_id),
            "a": str(addr_id),
            "now": now,
        },
    )
    session.commit()

    resp = client.get(f"/api/v1/workspaces/{ws_id}/analytics/campaigns/{camp_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["campaign_id"] == str(camp_id)
    assert data["campaign_name"] == "Spring Launch"
    assert data["sent"] == 1
    assert data["enrolled"] == 1
    assert data["bounced"] == 0
    assert data["replied"] == 0


def test_api_campaign_sequence_analytics(api_setup) -> None:
    session, client, ws_id = api_setup
    camp_id = uuid.uuid4()
    seq_id = uuid.uuid4()
    step_id = uuid.uuid4()

    session.execute(
        text("INSERT INTO public.workspaces (id, name) VALUES (:id, 'Test')"),
        {"id": str(ws_id)},
    )
    session.execute(
        text(
            "INSERT INTO public.campaigns (id, workspace_id, name) VALUES (:id, :ws, 'Seq Camp')"
        ),
        {"id": str(camp_id), "ws": str(ws_id)},
    )
    session.execute(
        text(
            "INSERT INTO public.campaign_sequences (id, workspace_id, campaign_id) VALUES (:id, :ws, :cid)"
        ),
        {"id": str(seq_id), "ws": str(ws_id), "cid": str(camp_id)},
    )
    session.execute(
        text(
            "INSERT INTO public.sequence_steps (id, workspace_id, sequence_id, position, kind, email_subject) VALUES (:id, :ws, :seq, 1, 'EMAIL', 'Hi there')"
        ),
        {"id": str(step_id), "ws": str(ws_id), "seq": str(seq_id)},
    )
    session.commit()

    resp = client.get(
        f"/api/v1/workspaces/{ws_id}/analytics/campaigns/{camp_id}/sequence"
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["campaign_id"] == str(camp_id)
    assert len(data["steps"]) == 1
    assert data["steps"][0]["step_id"] == str(step_id)
    assert data["steps"][0]["position"] == 1
    assert data["steps"][0]["subject"] == "Hi there"


def test_api_deliverability_overview(api_setup) -> None:
    session, client, ws_id = api_setup
    mb_id = uuid.uuid4()

    session.execute(
        text("INSERT INTO public.workspaces (id, name) VALUES (:id, 'Test')"),
        {"id": str(ws_id)},
    )
    session.execute(
        text(
            """
            INSERT INTO public.mailboxes (id, workspace_id, original_address, provider, connection_state, health_state)
            VALUES (:id, :ws, 'sales@outreach.com', 'MICROSOFT', 'CONNECTED', 'HEALTHY')
            """
        ),
        {"id": str(mb_id), "ws": str(ws_id)},
    )
    session.commit()

    resp = client.get(f"/api/v1/workspaces/{ws_id}/analytics/deliverability")
    assert resp.status_code == 200
    data = resp.json()
    assert data["workspace_id"] == str(ws_id)
    assert data["overall_health"] == "HEALTHY"
    assert len(data["mailboxes"]) == 1
    assert data["mailboxes"][0]["email_address"] == "sales@outreach.com"


def test_api_cross_tenant_isolation_idor_returns_404(api_setup) -> None:
    """Workspace A requesting Workspace B's campaign must return 404 (IDOR test)."""
    session, client, ws_a = api_setup
    ws_b = uuid.uuid4()
    camp_b = uuid.uuid4()

    session.execute(
        text("INSERT INTO public.workspaces (id, name) VALUES (:id, 'WS B')"),
        {"id": str(ws_b)},
    )
    # Campaign B belongs to Workspace B
    session.execute(
        text(
            "INSERT INTO public.campaigns (id, workspace_id, name) VALUES (:id, :ws, 'Camp B')"
        ),
        {"id": str(camp_b), "ws": str(ws_b)},
    )
    session.commit()

    # Workspace A requests Campaign B
    resp = client.get(f"/api/v1/workspaces/{ws_a}/analytics/campaigns/{camp_b}")
    assert resp.status_code == 404


def test_api_invalid_date_range_returns_422(api_setup) -> None:
    session, client, ws_id = api_setup
    # start_date > end_date
    resp = client.get(
        f"/api/v1/workspaces/{ws_id}/analytics/overview?start_date=2026-05-10&end_date=2026-05-01"
    )
    assert resp.status_code == 422
