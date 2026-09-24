from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.errors import AppError
from app.modules.usage.service import DEFAULT_WORKSPACE_LIMITS, UsageService


@pytest.fixture
def usage_db():
    engine = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    session_factory = sessionmaker(bind=engine)
    session = session_factory()

    session.execute(
        text(
            """
        CREATE TABLE workspaces (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            defaults TEXT,
            status TEXT NOT NULL DEFAULT 'ACTIVE'
        );
        """
        )
    )

    session.execute(
        text(
            """
        CREATE TABLE messages (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            status TEXT NOT NULL,
            accepted_at TIMESTAMP NOT NULL
        );
        """
        )
    )

    session.execute(
        text(
            """
        CREATE TABLE mailboxes (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            status TEXT NOT NULL
        );
        """
        )
    )

    session.execute(
        text(
            """
        CREATE TABLE leads (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            archived_at TIMESTAMP
        );
        """
        )
    )

    session.execute(
        text(
            """
        CREATE TABLE campaigns (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            status TEXT NOT NULL
        );
        """
        )
    )

    session.execute(
        text(
            """
        CREATE TABLE workspace_memberships (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            status TEXT NOT NULL
        );
        """
        )
    )

    session.commit()
    yield session
    session.close()


def test_get_workspace_usage_default_limits(usage_db):
    service = UsageService(usage_db)
    ws_id = uuid.uuid4()

    usage_db.execute(
        text(
            "INSERT INTO workspaces (id, name, defaults) "
            "VALUES (:id, 'Test WS', NULL)"
        ),
        {"id": str(ws_id)},
    )
    usage_db.commit()

    usage = service.get_workspace_usage(ws_id)
    assert usage.workspace_id == ws_id
    assert usage.plan_name == "Standard"
    for dim_name, dim in usage.dimensions.items():
        assert dim.used == 0
        assert dim.limit == DEFAULT_WORKSPACE_LIMITS[dim_name]


def test_get_workspace_usage_with_data_and_custom_limits(usage_db):
    service = UsageService(usage_db)
    ws_id = uuid.uuid4()
    now_iso = datetime.now(UTC).isoformat()

    defaults = {
        "plan_name": "Pro Tier",
        "limits": {
            "sent_messages_today": 5000,
            "active_mailboxes": 25,
            "leads": 20000,
            "active_campaigns": 50,
            "team_members": 20,
        },
    }

    usage_db.execute(
        text(
            "INSERT INTO workspaces (id, name, defaults) "
            "VALUES (:id, 'Scale Corp', :defaults)"
        ),
        {"id": str(ws_id), "defaults": json.dumps(defaults)},
    )

    # 2 sent messages today, 1 draft message, 1 old sent message
    usage_db.execute(
        text(
            "INSERT INTO messages (id, workspace_id, status, accepted_at) VALUES "
            "(:m1, :ws, 'SENT', :now), (:m2, :ws, 'SENT', :now), "
            "(:m3, :ws, 'DRAFT', :now), (:m4, :ws, 'SENT', '2020-01-01T00:00:00')"
        ),
        {
            "m1": str(uuid.uuid4()),
            "m2": str(uuid.uuid4()),
            "m3": str(uuid.uuid4()),
            "m4": str(uuid.uuid4()),
            "ws": str(ws_id),
            "now": now_iso,
        },
    )

    # 2 connected mailboxes, 1 disconnected
    usage_db.execute(
        text(
            "INSERT INTO mailboxes (id, workspace_id, status) VALUES "
            "(:mb1, :ws, 'CONNECTED'), (:mb2, :ws, 'CONNECTED'), "
            "(:mb3, :ws, 'DISCONNECTED')"
        ),
        {
            "mb1": str(uuid.uuid4()),
            "mb2": str(uuid.uuid4()),
            "mb3": str(uuid.uuid4()),
            "ws": str(ws_id),
        },
    )

    # 3 active leads, 1 archived lead
    usage_db.execute(
        text(
            "INSERT INTO leads (id, workspace_id, archived_at) VALUES "
            "(:l1, :ws, NULL), (:l2, :ws, NULL), (:l3, :ws, NULL), "
            "(:l4, :ws, :now)"
        ),
        {
            "l1": str(uuid.uuid4()),
            "l2": str(uuid.uuid4()),
            "l3": str(uuid.uuid4()),
            "l4": str(uuid.uuid4()),
            "ws": str(ws_id),
            "now": now_iso,
        },
    )

    # 1 running campaign, 1 scheduled, 1 paused
    usage_db.execute(
        text(
            "INSERT INTO campaigns (id, workspace_id, status) VALUES "
            "(:c1, :ws, 'RUNNING'), (:c2, :ws, 'SCHEDULED'), "
            "(:c3, :ws, 'PAUSED')"
        ),
        {
            "c1": str(uuid.uuid4()),
            "c2": str(uuid.uuid4()),
            "c3": str(uuid.uuid4()),
            "ws": str(ws_id),
        },
    )

    # 3 active members, 1 invited/suspended
    usage_db.execute(
        text(
            "INSERT INTO workspace_memberships (id, workspace_id, status) VALUES "
            "(:u1, :ws, 'ACTIVE'), (:u2, :ws, 'ACTIVE'), (:u3, :ws, 'ACTIVE'), "
            "(:u4, :ws, 'SUSPENDED')"
        ),
        {
            "u1": str(uuid.uuid4()),
            "u2": str(uuid.uuid4()),
            "u3": str(uuid.uuid4()),
            "u4": str(uuid.uuid4()),
            "ws": str(ws_id),
        },
    )
    usage_db.commit()

    usage = service.get_workspace_usage(ws_id)
    assert usage.plan_name == "Pro Tier"
    assert usage.dimensions["sent_messages_today"].used == 2
    assert usage.dimensions["sent_messages_today"].limit == 5000

    assert usage.dimensions["active_mailboxes"].used == 2
    assert usage.dimensions["active_mailboxes"].limit == 25

    assert usage.dimensions["leads"].used == 3
    assert usage.dimensions["leads"].limit == 20000

    assert usage.dimensions["active_campaigns"].used == 2
    assert usage.dimensions["active_campaigns"].limit == 50

    assert usage.dimensions["team_members"].used == 3
    assert usage.dimensions["team_members"].limit == 20


def test_check_entitlement_enforces_limits(usage_db):
    service = UsageService(usage_db)
    ws_id = uuid.uuid4()

    defaults = {
        "limits": {
            "team_members": 2,
        }
    }
    usage_db.execute(
        text(
            "INSERT INTO workspaces (id, name, defaults) "
            "VALUES (:id, 'Tiny WS', :defaults)"
        ),
        {"id": str(ws_id), "defaults": json.dumps(defaults)},
    )
    usage_db.execute(
        text(
            "INSERT INTO workspace_memberships (id, workspace_id, status) VALUES "
            "(:u1, :ws, 'ACTIVE'), (:u2, :ws, 'ACTIVE')"
        ),
        {"u1": str(uuid.uuid4()), "u2": str(uuid.uuid4()), "ws": str(ws_id)},
    )
    usage_db.commit()

    # Allowed check (delta 0 should pass)
    service.check_entitlement(ws_id, "team_members", delta=0)

    # Exceed check (delta 1 should fail since 2 + 1 > 2)
    with pytest.raises(AppError) as exc_info:
        service.check_entitlement(ws_id, "team_members", delta=1)
    assert exc_info.value.status_code == 403
    assert "Workspace quota exceeded for team_members" in exc_info.value.message


def test_get_usage_nonexistent_workspace_raises_404(usage_db):
    service = UsageService(usage_db)
    with pytest.raises(AppError) as exc_info:
        service.get_workspace_usage(uuid.uuid4())
    assert exc_info.value.status_code == 404
