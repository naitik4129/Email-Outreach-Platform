from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import PlatformOperatorPrincipal, get_platform_operator
from app.core.auth import AuthenticatedPrincipal
from app.core.errors import AppError
from app.modules.admin.service import AdminService


@pytest.fixture
def admin_db():
    engine = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )

    last_timestamp = [datetime.now(UTC)]

    def strictly_increasing_timestamp() -> str:
        # datetime.now() can repeat (Windows' clock ticks in ~15ms steps), and the
        # service orders audit events by recorded_at, so equal values would make
        # the order of two back-to-back events arbitrary.
        now = datetime.now(UTC)
        if now <= last_timestamp[0]:
            now = last_timestamp[0] + timedelta(microseconds=1)
        last_timestamp[0] = now
        return now.isoformat()

    @event.listens_for(engine, "connect")
    def register_functions(dbapi_con, con_record):
        dbapi_con.create_function(
            "statement_timestamp", 0, strictly_increasing_timestamp
        )

    session_factory = sessionmaker(bind=engine)
    session = session_factory()

    session.execute(
        text(
            """
        CREATE TABLE workspaces (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'ACTIVE',
            sending_restriction_reason TEXT,
            sending_restriction_version INTEGER NOT NULL DEFAULT 1,
            pending_safety_count INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP NOT NULL DEFAULT (datetime('now')),
            updated_at TIMESTAMP NOT NULL DEFAULT (datetime('now'))
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
        CREATE TABLE mailboxes (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'CONNECTED'
        );
        """
        )
    )

    session.execute(
        text(
            """
        CREATE TABLE platform_audit_events (
            id TEXT PRIMARY KEY,
            actor_kind TEXT NOT NULL,
            actor_id TEXT,
            action TEXT NOT NULL,
            target_type TEXT NOT NULL,
            target_id TEXT NOT NULL,
            before_state TEXT,
            after_state TEXT,
            reason TEXT,
            recorded_at TIMESTAMP NOT NULL DEFAULT (datetime('now'))
        );
        """
        )
    )

    session.commit()
    yield session
    session.close()


def test_admin_list_workspaces_with_metrics(admin_db):
    service = AdminService(admin_db)
    ws1 = uuid.uuid4()
    ws2 = uuid.uuid4()

    admin_db.execute(
        text(
            """
            INSERT INTO workspaces (id, name, status, pending_safety_count)
            VALUES (:id1, 'Alpha Corp', 'ACTIVE', 0),
                   (:id2, 'Beta Inc', 'RESTRICTED', 5);
            """
        ),
        {"id1": str(ws1), "id2": str(ws2)},
    )
    admin_db.execute(
        text(
            """
            INSERT INTO workspace_memberships (
                id, workspace_id, user_id, role_code, status
            )
            VALUES (:m1, :ws1, :u1, 'OWNER', 'ACTIVE'),
                   (:m2, :ws1, :u2, 'MEMBER', 'ACTIVE');
            """
        ),
        {
            "m1": str(uuid.uuid4()),
            "m2": str(uuid.uuid4()),
            "ws1": str(ws1),
            "u1": str(uuid.uuid4()),
            "u2": str(uuid.uuid4()),
        },
    )
    admin_db.execute(
        text(
            """
            INSERT INTO mailboxes (id, workspace_id, status)
            VALUES (:mb1, :ws1, 'CONNECTED');
            """
        ),
        {
            "ws1": str(ws1),
            "mb1": str(uuid.uuid4()),
        },
    )
    admin_db.commit()

    workspaces = service.list_workspaces()
    assert len(workspaces) == 2

    alpha = next(w for w in workspaces if w.id == ws1)
    assert alpha.name == "Alpha Corp"
    assert alpha.status == "ACTIVE"
    assert alpha.member_count == 2
    assert alpha.mailbox_count == 1

    beta = next(w for w in workspaces if w.id == ws2)
    assert beta.name == "Beta Inc"
    assert beta.status == "RESTRICTED"
    assert beta.pending_safety_count == 5


def test_admin_restrict_and_restore_workspace(admin_db):
    service = AdminService(admin_db)
    ws_id = uuid.uuid4()
    op_id = uuid.uuid4()

    admin_db.execute(
        text(
            "INSERT INTO workspaces (id, name, status) "
            "VALUES (:id, 'Target WS', 'ACTIVE')"
        ),
        {"id": str(ws_id)},
    )
    admin_db.commit()

    # 1. Restrict workspace
    res = service.restrict_workspace(
        workspace_id=ws_id,
        reason="Exceeded complaint rate threshold (0.2%)",
        operator_id=op_id,
    )
    assert res is True

    row = (
        admin_db.execute(
            text(
                """
                SELECT status, sending_restriction_reason, sending_restriction_version
                FROM workspaces WHERE id = :id
                """
            ),
            {"id": str(ws_id)},
        )
        .mappings()
        .first()
    )
    assert row["status"] == "RESTRICTED"
    assert (
        row["sending_restriction_reason"]
        == "Exceeded complaint rate threshold (0.2%)"
    )
    assert row["sending_restriction_version"] == 2

    # Verify platform audit event was written
    logs = service.list_platform_audit_logs(limit=10)
    assert len(logs) == 1
    assert logs[0].action == "workspace.restrict"
    assert logs[0].target_id == ws_id
    assert logs[0].actor_kind == "OPERATOR"
    before_state = (
        json.loads(logs[0].before_state)
        if isinstance(logs[0].before_state, str)
        else logs[0].before_state
    )
    assert before_state == {"status": "ACTIVE"}

    # 2. Restore workspace
    res2 = service.restore_workspace(workspace_id=ws_id, operator_id=op_id)
    assert res2 is True

    row2 = (
        admin_db.execute(
            text(
                """
                SELECT status, sending_restriction_reason, sending_restriction_version
                FROM workspaces WHERE id = :id
                """
            ),
            {"id": str(ws_id)},
        )
        .mappings()
        .first()
    )
    assert row2["status"] == "ACTIVE"
    assert row2["sending_restriction_reason"] is None
    assert row2["sending_restriction_version"] == 3

    logs2 = service.list_platform_audit_logs(limit=10)
    assert len(logs2) == 2
    assert logs2[0].action == "workspace.restore"


def test_restrict_nonexistent_workspace_raises_404(admin_db):
    service = AdminService(admin_db)
    with pytest.raises(AppError) as exc_info:
        service.restrict_workspace(
            workspace_id=uuid.uuid4(),
            reason="testing",
        )
    assert exc_info.value.status_code == 404


def test_get_platform_operator_authorization(monkeypatch):
    monkeypatch.setenv("PLATFORM_OPERATOR_EMAILS", "admin@platform.com")
    monkeypatch.setenv("PLATFORM_OPERATOR_KEY", "super-secret-key-999")

    # 1. Success with correct email and key
    op = get_platform_operator(
        operator_key="super-secret-key-999",
        principal=AuthenticatedPrincipal(
            user_id=uuid.uuid4(), email="admin@platform.com"
        ),
    )
    assert isinstance(op, PlatformOperatorPrincipal)
    assert op.email == "admin@platform.com"

    # 2. Success with correct email and no key
    op2 = get_platform_operator(
        operator_key=None,
        principal=AuthenticatedPrincipal(
            user_id=uuid.uuid4(), email="admin@platform.com"
        ),
    )
    assert isinstance(op2, PlatformOperatorPrincipal)

    # 3. Success with correct key even if email is non-operator
    op3 = get_platform_operator(
        operator_key="super-secret-key-999",
        principal=AuthenticatedPrincipal(
            user_id=uuid.uuid4(), email="regular.user@example.com"
        ),
    )
    assert isinstance(op3, PlatformOperatorPrincipal)

    # 4. Wrong key and non-operator email -> 403 AppError
    with pytest.raises(AppError) as exc_info:
        get_platform_operator(
            operator_key="wrong-key",
            principal=AuthenticatedPrincipal(
                user_id=uuid.uuid4(), email="regular.user@example.com"
            ),
        )
    assert exc_info.value.status_code == 403
