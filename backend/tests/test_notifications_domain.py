from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.modules.notifications.schemas import NotificationPreferencesIn
from app.modules.notifications.service import NotificationService
from app.modules.notifications.transports import (
    PlatformTransactionalEmailTransport,
    TransactionalEmailMessage,
)


@pytest.fixture
def notif_db():
    engine = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def register_functions(dbapi_con, con_record):
        dbapi_con.create_function(
            "statement_timestamp", 0, lambda: datetime.now(UTC).isoformat()
        )

    session_factory = sessionmaker(bind=engine)
    session = session_factory()

    session.execute(
        text(
            """
        CREATE TABLE workspaces (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'ACTIVE'
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
            user_id TEXT NOT NULL,
            role_code TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'ACTIVE'
        );
        """
        )
    )

    session.execute(
        text(
            """
        CREATE TABLE notifications (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            recipient_user_id TEXT NOT NULL,
            recipient_membership_id TEXT NOT NULL,
            source_event_id TEXT NOT NULL,
            type TEXT NOT NULL,
            summary TEXT NOT NULL,
            resource_link TEXT,
            read_at TIMESTAMP,
            created_at TIMESTAMP NOT NULL DEFAULT (datetime('now')),
            UNIQUE (workspace_id, recipient_user_id, source_event_id, type)
        );
        """
        )
    )

    session.execute(
        text(
            """
        CREATE TABLE profiles (
            id TEXT PRIMARY KEY,
            preferences TEXT NOT NULL DEFAULT '{}',
            updated_at TIMESTAMP NOT NULL DEFAULT (datetime('now'))
        );
        """
        )
    )

    session.commit()
    yield session
    session.close()


def test_create_and_count_unread_notifications(notif_db):
    service = NotificationService(notif_db)
    ws_id = uuid.uuid4()
    user_id = uuid.uuid4()
    mem_id = uuid.uuid4()
    source_event_id = uuid.uuid4()

    notif_db.execute(
        text("INSERT INTO workspaces (id, name) VALUES (:id, 'Acme')"),
        {"id": str(ws_id)},
    )
    notif_db.execute(
        text(
            """
            INSERT INTO workspace_memberships (id, workspace_id, user_id, role_code)
            VALUES (:id, :ws_id, :u_id, 'MEMBER')
            """
        ),
        {"id": str(mem_id), "ws_id": str(ws_id), "u_id": str(user_id)},
    )
    notif_db.commit()

    assert service.get_unread_count(ws_id, user_id) == 0

    notif_id = service.create_notification(
        workspace_id=ws_id,
        recipient_user_id=user_id,
        notification_type="campaign.completed",
        source_event_id=source_event_id,
        summary="Campaign Spring Q1 completed successfully",
        resource_link="/app/campaigns/123",
    )
    assert notif_id is not None

    assert service.get_unread_count(ws_id, user_id) == 1

    listing = service.list_notifications(ws_id, user_id, unread_only=True)
    assert listing.total == 1
    assert listing.unread_count == 1
    assert len(listing.items) == 1
    assert listing.items[0].summary == "Campaign Spring Q1 completed successfully"
    assert listing.items[0].read_at is None


def test_create_notification_idempotency(notif_db):
    service = NotificationService(notif_db)
    ws_id = uuid.uuid4()
    user_id = uuid.uuid4()
    mem_id = uuid.uuid4()
    source_event_id = uuid.uuid4()

    notif_db.execute(
        text("INSERT INTO workspaces (id, name) VALUES (:id, 'Acme')"),
        {"id": str(ws_id)},
    )
    notif_db.execute(
        text(
            """
            INSERT INTO workspace_memberships (id, workspace_id, user_id, role_code)
            VALUES (:id, :ws_id, :u_id, 'MEMBER')
            """
        ),
        {"id": str(mem_id), "ws_id": str(ws_id), "u_id": str(user_id)},
    )
    notif_db.commit()

    id1 = service.create_notification(
        workspace_id=ws_id,
        recipient_user_id=user_id,
        notification_type="alert",
        source_event_id=source_event_id,
        summary="Mailbox disconnected",
    )
    id2 = service.create_notification(
        workspace_id=ws_id,
        recipient_user_id=user_id,
        notification_type="alert",
        source_event_id=source_event_id,
        summary="Mailbox disconnected",
    )
    assert id1 is not None
    assert id2 is not None

    # Count must remain 1 due to unique constraint ON CONFLICT DO NOTHING
    assert service.get_unread_count(ws_id, user_id) == 1


def test_mark_as_read_isolation_and_idempotency(notif_db):
    service = NotificationService(notif_db)
    ws_id = uuid.uuid4()
    other_ws_id = uuid.uuid4()
    user_id = uuid.uuid4()
    other_user_id = uuid.uuid4()
    mem_id = uuid.uuid4()

    notif_db.execute(
        text("INSERT INTO workspaces (id, name) VALUES (:id, 'Acme')"),
        {"id": str(ws_id)},
    )
    notif_db.execute(
        text(
            """
            INSERT INTO workspace_memberships (id, workspace_id, user_id, role_code)
            VALUES (:id, :ws_id, :u_id, 'MEMBER')
            """
        ),
        {"id": str(mem_id), "ws_id": str(ws_id), "u_id": str(user_id)},
    )
    notif_db.commit()

    notif_id = service.create_notification(
        workspace_id=ws_id,
        recipient_user_id=user_id,
        notification_type="lead.replied",
        source_event_id=uuid.uuid4(),
        summary="Interested reply received",
    )

    # 1. Other workspace cannot mark read (tenant isolation)
    assert not service.mark_as_read(other_ws_id, user_id, notif_id)

    # 2. Other user cannot mark read (user isolation)
    assert not service.mark_as_read(ws_id, other_user_id, notif_id)

    # 3. Owner of notification marks read -> True
    assert service.mark_as_read(ws_id, user_id, notif_id)
    assert service.get_unread_count(ws_id, user_id) == 0

    # 4. Marking already read notification -> False (idempotent, no duplicate update)
    assert not service.mark_as_read(ws_id, user_id, notif_id)


def test_mark_all_as_read(notif_db):
    service = NotificationService(notif_db)
    ws_id = uuid.uuid4()
    user_id = uuid.uuid4()
    mem_id = uuid.uuid4()

    notif_db.execute(
        text("INSERT INTO workspaces (id, name) VALUES (:id, 'Acme')"),
        {"id": str(ws_id)},
    )
    notif_db.execute(
        text(
            """
            INSERT INTO workspace_memberships (id, workspace_id, user_id, role_code)
            VALUES (:id, :ws_id, :u_id, 'MEMBER')
            """
        ),
        {"id": str(mem_id), "ws_id": str(ws_id), "u_id": str(user_id)},
    )
    notif_db.commit()

    for i in range(3):
        service.create_notification(
            workspace_id=ws_id,
            recipient_user_id=user_id,
            notification_type=f"item.{i}",
            source_event_id=uuid.uuid4(),
            summary=f"Notification {i}",
        )

    assert service.get_unread_count(ws_id, user_id) == 3

    marked = service.mark_all_as_read(ws_id, user_id)
    assert marked == 3
    assert service.get_unread_count(ws_id, user_id) == 0


def test_user_preferences_roundtrip(notif_db):
    service = NotificationService(notif_db)
    user_id = uuid.uuid4()

    notif_db.execute(
        text("INSERT INTO profiles (id, preferences) VALUES (:id, '{}')"),
        {"id": str(user_id)},
    )
    notif_db.commit()

    # Default preferences
    prefs = service.get_user_preferences(user_id)
    assert prefs.email_enabled is True
    assert prefs.in_app_enabled is True
    assert prefs.categories.get("replies") is True

    # Update preferences
    updated = service.update_user_preferences(
        user_id=user_id,
        payload=NotificationPreferencesIn(
            email_enabled=False,
            in_app_enabled=True,
            categories={"replies": True, "deliverability": False},
        ),
    )
    assert updated.email_enabled is False
    assert updated.categories.get("deliverability") is False

    # Read back
    fetched = service.get_user_preferences(user_id)
    assert fetched.email_enabled is False
    assert fetched.categories.get("deliverability") is False
    assert fetched.categories.get("replies") is True


def test_transactional_email_transport():
    msg = TransactionalEmailMessage(
        to_email="customer@example.com",
        subject="Important Account Notice",
        body_text="Your workspace limit is reaching threshold.",
        category="security",
    )
    # Verifies separate platform transport sends without error (ADR 0008)
    res = PlatformTransactionalEmailTransport.send(msg)
    assert res is True
