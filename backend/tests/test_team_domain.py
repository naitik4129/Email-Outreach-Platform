from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.errors import AppError
from app.modules.team.schemas import (
    InvitationAcceptIn,
    InvitationCreateIn,
    MemberRemoveIn,
    MemberRoleUpdateIn,
    TransferOwnershipIn,
)
from app.modules.team.service import TeamService, hash_token


@pytest.fixture
def team_db():
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

    session.execute(text("ATTACH DATABASE ':memory:' AS auth;"))
    session.execute(
        text(
            """
        CREATE TABLE auth.users (
            id TEXT PRIMARY KEY,
            email TEXT NOT NULL
        );
        """
        )
    )

    session.execute(
        text(
            """
        CREATE TABLE workspaces (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'ACTIVE',
            owner_policy_version INTEGER NOT NULL DEFAULT 1,
            sending_restriction_reason TEXT,
            sending_restriction_version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
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
            status TEXT NOT NULL DEFAULT 'ACTIVE',
            revoked_at TIMESTAMP,
            version INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE (workspace_id, user_id)
        );
        """
        )
    )

    session.execute(
        text(
            """
        CREATE TABLE workspace_invitations (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            inviter_id TEXT NOT NULL,
            invited_email TEXT NOT NULL,
            role_code TEXT NOT NULL,
            token_digest TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'PENDING',
            expires_at TIMESTAMP NOT NULL,
            accepted_by_user_id TEXT,
            accepted_membership_id TEXT,
            accepted_at TIMESTAMP,
            revoked_by_user_id TEXT,
            revoked_at TIMESTAMP,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
        )
    )

    session.execute(
        text(
            """
        CREATE TABLE audit_events (
            id TEXT PRIMARY KEY,
            workspace_id TEXT,
            actor_kind TEXT NOT NULL,
            actor_id TEXT,
            action TEXT NOT NULL,
            target_type TEXT NOT NULL,
            target_id TEXT NOT NULL,
            before_state TEXT,
            after_state TEXT,
            reason TEXT,
            recorded_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
        )
    )

    session.commit()
    yield session
    session.close()


def add_workspace(db, ws_id, name="Acme Corp"):
    db.execute(
        text("INSERT INTO workspaces (id, name) VALUES (:id, :name)"),
        {"id": str(ws_id), "name": name},
    )


def add_user(db, user_id, email):
    db.execute(
        text("INSERT INTO auth.users (id, email) VALUES (:id, :email)"),
        {"id": str(user_id), "email": email.strip().lower()},
    )


def add_membership(
    db, ws_id, user_id, role, status="ACTIVE", version=1, mem_id=None
):
    m_id = mem_id or uuid.uuid4()
    db.execute(
        text(
            """
            INSERT INTO workspace_memberships
                (id, workspace_id, user_id, role_code, status, version)
            VALUES
                (:id, :ws_id, :user_id, :role, :status, :version)
            """
        ),
        {
            "id": str(m_id),
            "ws_id": str(ws_id),
            "user_id": str(user_id),
            "role": role,
            "status": status,
            "version": version,
        },
    )
    return m_id


def test_token_hash_matches_sha256():
    token = "test-secret-token-12345"
    expected = hashlib.sha256(token.encode("utf-8")).hexdigest()
    assert hash_token(token) == expected


def test_create_invitation_requires_owner(team_db):
    service = TeamService(team_db)
    ws_id = uuid.uuid4()
    admin_id = uuid.uuid4()

    add_workspace(team_db, ws_id)
    add_membership(team_db, ws_id, admin_id, "ADMIN")
    team_db.commit()

    with pytest.raises(AppError) as exc_info:
        service.create_invitation(
            workspace_id=ws_id,
            inviter_id=admin_id,
            payload=InvitationCreateIn(
                email="teammate@example.com", role_code="MEMBER"
            ),
            base_url="https://app.example.com",
        )
    assert exc_info.value.status_code == 403


def test_create_invitation_forbids_owner_role(team_db):
    with pytest.raises(ValueError, match="OWNER"):
        InvitationCreateIn(email="co-owner@example.com", role_code="OWNER")


def test_create_invitation_success(team_db):
    service = TeamService(team_db)
    ws_id = uuid.uuid4()
    owner_id = uuid.uuid4()

    add_workspace(team_db, ws_id)
    add_membership(team_db, ws_id, owner_id, "OWNER")
    team_db.commit()

    out = service.create_invitation(
        workspace_id=ws_id,
        inviter_id=owner_id,
        payload=InvitationCreateIn(
            email="Alice@Example.com ", role_code="MANAGER"
        ),
        base_url="https://app.example.com",
    )

    assert out.invited_email == "alice@example.com"
    assert out.role_code == "MANAGER"
    assert out.invite_url is not None
    assert "token=" in out.invite_url
    raw_token = out.invite_url.split("token=")[1]

    row = (
        team_db.execute(
            text(
                "SELECT token_digest FROM workspace_invitations WHERE id = :id"
            ),
            {"id": str(out.id)},
        )
        .mappings()
        .first()
    )
    assert row["token_digest"] == hash_token(raw_token)
    assert row["token_digest"] != raw_token


def test_verify_and_accept_invitation_success(team_db):
    service = TeamService(team_db)
    ws_id = uuid.uuid4()
    owner_id = uuid.uuid4()
    new_user_id = uuid.uuid4()
    email = "bob@example.com"

    add_workspace(team_db, ws_id)
    add_membership(team_db, ws_id, owner_id, "OWNER")
    team_db.commit()

    inv = service.create_invitation(
        workspace_id=ws_id,
        inviter_id=owner_id,
        payload=InvitationCreateIn(email=email, role_code="MEMBER"),
        base_url="https://app.example.com",
    )
    raw_token = inv.invite_url.split("token=")[1]

    # 1. Verify endpoint
    v_out = service.verify_invitation_token(raw_token)
    assert v_out.id is not None
    assert v_out.workspace_name == "Acme Corp"
    assert v_out.role_code == "MEMBER"
    assert v_out.invited_email == email

    # 2. Email mismatch is rejected
    with pytest.raises(AppError) as exc_info:
        service.accept_invitation(
            actor_id=new_user_id,
            actor_email="charlie@example.com",
            payload=InvitationAcceptIn(token=raw_token),
        )
    assert exc_info.value.status_code == 403

    # 3. Successful accept
    acc_out = service.accept_invitation(
        actor_id=new_user_id,
        actor_email=email,
        payload=InvitationAcceptIn(token=raw_token),
    )
    assert acc_out.workspace_id == ws_id
    assert acc_out.role_code == "MEMBER"

    # Verify membership created
    mem = (
        team_db.execute(
            text(
                """
                SELECT role_code, status FROM workspace_memberships
                WHERE workspace_id = :ws_id AND user_id = :user_id
                """
            ),
            {"ws_id": str(ws_id), "user_id": str(new_user_id)},
        )
        .mappings()
        .first()
    )
    assert mem["role_code"] == "MEMBER"
    assert mem["status"] == "ACTIVE"


def test_revoke_invitation(team_db):
    service = TeamService(team_db)
    ws_id = uuid.uuid4()
    owner_id = uuid.uuid4()

    add_workspace(team_db, ws_id)
    add_membership(team_db, ws_id, owner_id, "OWNER")
    team_db.commit()

    inv = service.create_invitation(
        workspace_id=ws_id,
        inviter_id=owner_id,
        payload=InvitationCreateIn(email="revoke@example.com", role_code="VIEWER"),
        base_url="https://app.example.com",
    )

    service.revoke_invitation(
        workspace_id=ws_id,
        actor_id=owner_id,
        invitation_id=inv.id,
    )

    row = (
        team_db.execute(
            text("SELECT status FROM workspace_invitations WHERE id = :id"),
            {"id": str(inv.id)},
        )
        .mappings()
        .first()
    )
    assert row["status"] == "REVOKED"


def test_update_member_role_and_guards(team_db):
    service = TeamService(team_db)
    ws_id = uuid.uuid4()
    owner_id = uuid.uuid4()
    member_user_id = uuid.uuid4()

    add_workspace(team_db, ws_id)
    owner_mem_id = add_membership(team_db, ws_id, owner_id, "OWNER")
    member_id = add_membership(team_db, ws_id, member_user_id, "MEMBER")
    team_db.commit()

    # 1. Cannot update own role
    with pytest.raises(AppError) as exc_info:
        service.update_member_role(
            workspace_id=ws_id,
            actor_id=owner_id,
            membership_id=owner_mem_id,
            payload=MemberRoleUpdateIn(role_code="ADMIN", expected_version=1),
        )
    assert exc_info.value.status_code == 403

    # 2. Cannot promote member to OWNER via update_member_role (must use transfer)
    with pytest.raises(ValueError, match="OWNER"):
        MemberRoleUpdateIn(role_code="OWNER", expected_version=1)

    # 3. Successful role update to ADMIN
    res = service.update_member_role(
        workspace_id=ws_id,
        actor_id=owner_id,
        membership_id=member_id,
        payload=MemberRoleUpdateIn(role_code="ADMIN", expected_version=1),
    )
    assert res["role_code"] == "ADMIN"
    assert res["version"] == 2


def test_remove_member_and_guards(team_db):
    service = TeamService(team_db)
    ws_id = uuid.uuid4()
    owner_id = uuid.uuid4()
    member_user_id = uuid.uuid4()

    add_workspace(team_db, ws_id)
    owner_mem_id = add_membership(team_db, ws_id, owner_id, "OWNER")
    member_id = add_membership(team_db, ws_id, member_user_id, "MEMBER")
    team_db.commit()

    # 1. Cannot remove self
    with pytest.raises(AppError) as exc_info:
        service.remove_member(
            workspace_id=ws_id,
            actor_id=owner_id,
            membership_id=owner_mem_id,
            payload=MemberRemoveIn(expected_version=1),
        )
    assert exc_info.value.status_code == 403

    # 2. Successfully remove member
    assert (
        service.remove_member(
            workspace_id=ws_id,
            actor_id=owner_id,
            membership_id=member_id,
            payload=MemberRemoveIn(expected_version=1),
        )
        is True
    )

    row = (
        team_db.execute(
            text("SELECT status FROM workspace_memberships WHERE id = :id"),
            {"id": str(member_id)},
        )
        .mappings()
        .first()
    )
    assert row["status"] == "REVOKED"


def test_transfer_ownership_atomic(team_db):
    service = TeamService(team_db)
    ws_id = uuid.uuid4()
    owner_id = uuid.uuid4()
    member_user_id = uuid.uuid4()

    add_workspace(team_db, ws_id)
    owner_mem_id = add_membership(team_db, ws_id, owner_id, "OWNER")
    member_id = add_membership(team_db, ws_id, member_user_id, "ADMIN")
    team_db.commit()

    # 1. Non-owner cannot transfer
    with pytest.raises(AppError) as exc_info:
        service.transfer_ownership(
            workspace_id=ws_id,
            actor_id=member_user_id,
            payload=TransferOwnershipIn(
                target_membership_id=owner_mem_id,
                expected_owner_version=1,
                expected_target_version=1,
            ),
        )
    assert exc_info.value.status_code == 403

    # 2. Successful atomic transfer
    out = service.transfer_ownership(
        workspace_id=ws_id,
        actor_id=owner_id,
        payload=TransferOwnershipIn(
            target_membership_id=member_id,
            expected_owner_version=1,
            expected_target_version=1,
        ),
    )
    assert out.previous_owner_id == owner_mem_id
    assert out.new_owner_id == member_id

    # Verify former owner is now ADMIN
    old_owner = (
        team_db.execute(
            text(
                "SELECT role_code, version FROM workspace_memberships WHERE id = :id"
            ),
            {"id": str(owner_mem_id)},
        )
        .mappings()
        .first()
    )
    assert old_owner["role_code"] == "ADMIN"
    assert old_owner["version"] == 2

    # Verify target is now OWNER
    new_owner = (
        team_db.execute(
            text(
                "SELECT role_code, version FROM workspace_memberships WHERE id = :id"
            ),
            {"id": str(member_id)},
        )
        .mappings()
        .first()
    )
    assert new_owner["role_code"] == "OWNER"
    assert new_owner["version"] == 2
