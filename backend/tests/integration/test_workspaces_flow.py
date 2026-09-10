from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def _bootstrap(client: TestClient, user, name: str, idem_key: str | None = None):
    return client.post(
        "/api/v1/workspaces",
        json={"name": name},
        headers={
            **user.auth_header,
            "Idempotency-Key": idem_key or uuid.uuid4().hex,
        },
    )


def test_me_lazily_creates_profile(api_client: TestClient, make_test_user) -> None:
    user = make_test_user()
    resp = api_client.get("/api/v1/me", headers=user.auth_header)
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == user.user_id
    assert body["email"] == user.email


def test_missing_token_rejected(api_client: TestClient) -> None:
    assert api_client.get("/api/v1/me").status_code == 401


def test_garbage_token_rejected(api_client: TestClient) -> None:
    resp = api_client.get(
        "/api/v1/me", headers={"Authorization": "Bearer not-a-real-token"}
    )
    assert resp.status_code == 401


def test_workspace_bootstrap_creates_owner_membership(
    api_client: TestClient, make_test_user
) -> None:
    user = make_test_user()
    resp = _bootstrap(api_client, user, "Acme Outreach")
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "Acme Outreach"
    assert body["role_code"] == "OWNER"
    assert body["status"] == "ACTIVE"

    listing = api_client.get("/api/v1/workspaces", headers=user.auth_header)
    assert listing.status_code == 200
    workspace_ids = [w["workspace_id"] for w in listing.json()]
    assert body["id"] in workspace_ids


def test_workspace_bootstrap_is_idempotent(
    api_client: TestClient, make_test_user
) -> None:
    user = make_test_user()
    key = uuid.uuid4().hex

    first = _bootstrap(api_client, user, "Double Click Co", idem_key=key)
    second = _bootstrap(api_client, user, "Double Click Co", idem_key=key)

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["membership_id"] == second.json()["membership_id"]

    listing = api_client.get("/api/v1/workspaces", headers=user.auth_header).json()
    matching = [w for w in listing if w["workspace_id"] == first.json()["id"]]
    assert len(matching) == 1  # exactly one workspace, not two


def test_workspace_get_and_update(api_client: TestClient, make_test_user) -> None:
    user = make_test_user()
    created = _bootstrap(api_client, user, "Update Me Inc").json()
    workspace_id = created["id"]

    get_resp = api_client.get(
        f"/api/v1/workspaces/{workspace_id}", headers=user.auth_header
    )
    assert get_resp.status_code == 200
    assert get_resp.json()["version"] == 1

    update_resp = api_client.patch(
        f"/api/v1/workspaces/{workspace_id}",
        json={"name": "Renamed Inc", "expected_version": 1},
        headers=user.auth_header,
    )
    assert update_resp.status_code == 200
    assert update_resp.json()["name"] == "Renamed Inc"
    assert update_resp.json()["version"] == 2

    stale_retry = api_client.patch(
        f"/api/v1/workspaces/{workspace_id}",
        json={"name": "Should Conflict", "expected_version": 1},
        headers=user.auth_header,
    )
    assert stale_retry.status_code == 409


def test_workspace_members_lists_owner(
    api_client: TestClient, make_test_user
) -> None:
    user = make_test_user()
    created = _bootstrap(api_client, user, "Team Directory Co").json()

    members = api_client.get(
        f"/api/v1/workspaces/{created['id']}/members", headers=user.auth_header
    )
    assert members.status_code == 200
    roles = {m["user_id"]: m["role_code"] for m in members.json()}
    assert roles.get(user.user_id) == "OWNER"


def test_cross_tenant_isolation_matrix(
    api_client: TestClient, make_test_user
) -> None:
    """Required tenant isolation matrix: A reads A (allowed), A reads B
    (denied), B reads B (allowed), B reads A (denied); listings never leak
    across users."""
    user_a = make_test_user()
    user_b = make_test_user()

    workspace_a = _bootstrap(api_client, user_a, "Workspace A").json()
    workspace_b = _bootstrap(api_client, user_b, "Workspace B").json()

    a_reads_a = api_client.get(
        f"/api/v1/workspaces/{workspace_a['id']}", headers=user_a.auth_header
    )
    assert a_reads_a.status_code == 200

    a_reads_b = api_client.get(
        f"/api/v1/workspaces/{workspace_b['id']}", headers=user_a.auth_header
    )
    assert a_reads_b.status_code == 404

    b_reads_b = api_client.get(
        f"/api/v1/workspaces/{workspace_b['id']}", headers=user_b.auth_header
    )
    assert b_reads_b.status_code == 200

    b_reads_a = api_client.get(
        f"/api/v1/workspaces/{workspace_a['id']}", headers=user_b.auth_header
    )
    assert b_reads_a.status_code == 404

    a_list = {
        w["workspace_id"]
        for w in api_client.get(
            "/api/v1/workspaces", headers=user_a.auth_header
        ).json()
    }
    b_list = {
        w["workspace_id"]
        for w in api_client.get(
            "/api/v1/workspaces", headers=user_b.auth_header
        ).json()
    }
    assert workspace_b["id"] not in a_list
    assert workspace_a["id"] not in b_list
    assert workspace_a["id"] in a_list
    assert workspace_b["id"] in b_list

    # A cannot mutate B's workspace either, using a resource they know exists.
    denied_update = api_client.patch(
        f"/api/v1/workspaces/{workspace_b['id']}",
        json={"name": "Hijacked", "expected_version": 1},
        headers=user_a.auth_header,
    )
    assert denied_update.status_code == 404


def test_unauthorized_workspace_id_rejected(
    api_client: TestClient, make_test_user
) -> None:
    user = make_test_user()
    fake_workspace_id = str(uuid.uuid4())
    resp = api_client.get(
        f"/api/v1/workspaces/{fake_workspace_id}", headers=user.auth_header
    )
    assert resp.status_code == 404


def _seed_membership(
    db_admin: Session, workspace_id: str, user_id: str, role_code: str
) -> None:
    db_admin.execute(
        text(
            "INSERT INTO workspace_memberships (workspace_id, user_id, role_code) "
            "VALUES (:workspace_id, :user_id, :role_code)"
        ),
        {"workspace_id": workspace_id, "user_id": user_id, "role_code": role_code},
    )


@pytest.mark.parametrize(
    ("role_code", "expect_allowed"),
    [
        ("VIEWER", False),
        ("MEMBER", False),
        ("MANAGER", False),
        ("ADMIN", True),
    ],
)
def test_workspace_manage_rbac_matrix(
    api_client: TestClient,
    make_test_user,
    db_admin: Session,
    role_code: str,
    expect_allowed: bool,
) -> None:
    """workspace.manage is ADMIN/OWNER only per USER_ROLES.md; every other
    active role must get 403 from the same PATCH request."""
    owner = make_test_user()
    member = make_test_user()
    workspace = _bootstrap(api_client, owner, f"RBAC {role_code}").json()

    # Ensure the member's profile exists (normally created lazily on their
    # first authenticated request) before seeding a membership referencing it.
    api_client.get("/api/v1/me", headers=member.auth_header)
    _seed_membership(db_admin, workspace["id"], member.user_id, role_code)
    db_admin.commit()

    resp = api_client.patch(
        f"/api/v1/workspaces/{workspace['id']}",
        json={"name": "Attempted rename", "expected_version": 1},
        headers=member.auth_header,
    )
    if expect_allowed:
        assert resp.status_code == 200
    else:
        assert resp.status_code == 403


def test_revoked_membership_denied_on_next_request(
    api_client: TestClient, make_test_user, db_admin: Session
) -> None:
    owner = make_test_user()
    member = make_test_user()
    workspace = _bootstrap(api_client, owner, "Revocation Co").json()
    api_client.get("/api/v1/me", headers=member.auth_header)
    _seed_membership(db_admin, workspace["id"], member.user_id, "MEMBER")
    db_admin.commit()

    still_active = api_client.get(
        f"/api/v1/workspaces/{workspace['id']}", headers=member.auth_header
    )
    assert still_active.status_code == 200

    db_admin.execute(
        text(
            "UPDATE workspace_memberships SET status = 'REVOKED', "
            "revoked_at = now() "
            "WHERE workspace_id = :workspace_id AND user_id = :user_id"
        ),
        {"workspace_id": workspace["id"], "user_id": member.user_id},
    )
    db_admin.commit()

    after_revocation = api_client.get(
        f"/api/v1/workspaces/{workspace['id']}", headers=member.auth_header
    )
    assert after_revocation.status_code == 404
