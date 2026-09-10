from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def _bootstrap(client: TestClient, user, name: str) -> dict:
    resp = client.post(
        "/api/v1/workspaces",
        json={"name": name},
        headers={**user.auth_header, "Idempotency-Key": uuid.uuid4().hex},
    )
    assert resp.status_code == 201
    return resp.json()


def _set_context(
    session: Session, *, user_id: str | None, workspace_id: str | None
) -> None:
    if user_id is not None:
        session.execute(
            text("select set_config('app.user_id', :v, true)"), {"v": user_id}
        )
    if workspace_id is not None:
        session.execute(
            text("select set_config('app.workspace_id', :v, true)"),
            {"v": workspace_id},
        )


def test_missing_context_returns_no_rows(raw_db: Session) -> None:
    # No app.user_id/app.workspace_id set at all on this fresh transaction.
    count = raw_db.execute(text("select count(*) from public.workspaces")).scalar()
    assert count == 0
    member_count = raw_db.execute(
        text("select count(*) from public.workspace_memberships")
    ).scalar()
    assert member_count == 0


def test_a_context_cannot_read_b_workspace(
    api_client: TestClient, make_test_user, raw_db: Session
) -> None:
    user_a = make_test_user()
    user_b = make_test_user()
    workspace_a = _bootstrap(api_client, user_a, "RLS Workspace A")
    workspace_b = _bootstrap(api_client, user_b, "RLS Workspace B")

    _set_context(raw_db, user_id=user_a.user_id, workspace_id=workspace_a["id"])
    visible_a = raw_db.execute(
        text("select id from public.workspaces")
    ).scalars().all()
    assert visible_a == [uuid_of(workspace_a["id"])]

    _set_context(raw_db, user_id=user_a.user_id, workspace_id=workspace_b["id"])
    visible_b_via_a = raw_db.execute(
        text("select id from public.workspaces")
    ).scalars().all()
    assert visible_b_via_a == []


def uuid_of(value: str):
    import uuid as _uuid

    return _uuid.UUID(value)


def test_a_context_cannot_read_b_membership(
    api_client: TestClient, make_test_user, raw_db: Session
) -> None:
    user_a = make_test_user()
    user_b = make_test_user()
    workspace_a = _bootstrap(api_client, user_a, "RLS Membership A")
    workspace_b = _bootstrap(api_client, user_b, "RLS Membership B")

    _set_context(raw_db, user_id=user_a.user_id, workspace_id=workspace_b["id"])
    rows = raw_db.execute(
        text("select user_id from public.workspace_memberships")
    ).scalars().all()
    assert rows == []

    _set_context(raw_db, user_id=user_a.user_id, workspace_id=workspace_a["id"])
    rows_a = raw_db.execute(
        text("select user_id from public.workspace_memberships")
    ).scalars().all()
    assert rows_a == [uuid_of(user_a.user_id)]


def test_a_cannot_update_b_workspace_through_rls(
    api_client: TestClient, make_test_user, raw_db: Session
) -> None:
    user_a = make_test_user()
    user_b = make_test_user()
    _bootstrap(api_client, user_a, "RLS Update A")
    workspace_b = _bootstrap(api_client, user_b, "RLS Update B")

    # A's own context, but targeting B's workspace id directly by value --
    # RLS must scope by app.workspace_id, not by whatever id is in the WHERE
    # clause, so this must affect zero rows.
    _set_context(raw_db, user_id=user_a.user_id, workspace_id=workspace_b["id"])
    result = raw_db.execute(
        text(
            "UPDATE public.workspaces SET name = 'Hijacked' WHERE id = :id"
        ),
        {"id": workspace_b["id"]},
    )
    assert result.rowcount == 0


def test_no_direct_insert_into_workspace_memberships(
    api_client: TestClient, make_test_user, raw_db: Session
) -> None:
    """app_api must have zero direct INSERT privilege on workspace_memberships
    outside of app_bootstrap_workspace(); this is the core invariant the
    whole Phase 1 authorization model depends on."""
    user_a = make_test_user()
    workspace_a = _bootstrap(api_client, user_a, "RLS No Direct Insert")
    _set_context(raw_db, user_id=user_a.user_id, workspace_id=workspace_a["id"])

    with pytest.raises(Exception, match="permission denied"):
        raw_db.execute(
            text(
                "INSERT INTO public.workspace_memberships "
                "(workspace_id, user_id, role_code) "
                "VALUES (:workspace_id, :user_id, 'OWNER')"
            ),
            {"workspace_id": workspace_a["id"], "user_id": user_a.user_id},
        )
    # The failed statement leaves the transaction aborted; roll back so the
    # raw_db fixture's own teardown commit doesn't also fail.
    raw_db.rollback()
