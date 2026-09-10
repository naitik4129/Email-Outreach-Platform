from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text

from app.core.config import Settings

pytestmark = pytest.mark.integration


def _bootstrap(client: TestClient, user, name: str) -> dict:
    resp = client.post(
        "/api/v1/workspaces",
        json={"name": name},
        headers={**user.auth_header, "Idempotency-Key": uuid.uuid4().hex},
    )
    assert resp.status_code == 201
    return resp.json()


def _run_scoped_query(engine, *, user_id: str | None, workspace_id: str | None):
    """One full request-shaped transaction: BEGIN -> SET LOCAL ROLE ->
    (optional) context -> query -> COMMIT, using a connection drawn from the
    given (size-1) pool -- exactly the code path get_db() exercises."""
    with engine.begin() as conn:
        conn.execute(text("SET LOCAL ROLE app_api"))
        if user_id is not None:
            conn.execute(
                text("select set_config('app.user_id', :v, true)"), {"v": user_id}
            )
        if workspace_id is not None:
            conn.execute(
                text("select set_config('app.workspace_id', :v, true)"),
                {"v": workspace_id},
            )
        seen_user = conn.execute(
            text("select public.app_current_user_id()")
        ).scalar()
        seen_workspace_ids = (
            conn.execute(text("select id from public.workspaces")).scalars().all()
        )
        return seen_user, seen_workspace_ids


def test_pooled_connection_does_not_leak_context_between_requests(
    api_client: TestClient, make_test_user
) -> None:
    """Release-blocking test (task spec section 59): with a single-connection
    pool, a request for User A/Workspace A followed by a request for User
    B/Workspace B on the SAME physical connection must not see A's context or
    rows, and a request with NO context set must see nothing at all."""
    user_a = make_test_user()
    user_b = make_test_user()
    workspace_a = _bootstrap(api_client, user_a, "Pool Leak A")
    workspace_b = _bootstrap(api_client, user_b, "Pool Leak B")

    settings = Settings.current()
    engine = create_engine(
        settings.database_url, pool_size=1, max_overflow=0, pool_pre_ping=True
    )
    try:
        # Request 1: User A / Workspace A. Connection returns to the pool
        # after this `with engine.begin()` block exits (COMMIT).
        seen_user_1, seen_workspaces_1 = _run_scoped_query(
            engine, user_id=user_a.user_id, workspace_id=workspace_a["id"]
        )
        assert str(seen_user_1) == user_a.user_id
        assert [str(w) for w in seen_workspaces_1] == [workspace_a["id"]]

        # Request 2: forced onto the SAME physical connection (pool_size=1,
        # max_overflow=0 -- there is only one connection to give out), but
        # with NO context set. Must not see A's context or Workspace A.
        seen_user_2, seen_workspaces_2 = _run_scoped_query(
            engine, user_id=None, workspace_id=None
        )
        assert seen_user_2 is None
        assert seen_workspaces_2 == []

        # Request 3: same connection again, now as User B / Workspace B.
        # Must see only B, never A.
        seen_user_3, seen_workspaces_3 = _run_scoped_query(
            engine, user_id=user_b.user_id, workspace_id=workspace_b["id"]
        )
        assert str(seen_user_3) == user_b.user_id
        assert [str(w) for w in seen_workspaces_3] == [workspace_b["id"]]

        # Reverse order: B then A, to rule out an ordering-dependent leak.
        seen_user_4, seen_workspaces_4 = _run_scoped_query(
            engine, user_id=user_b.user_id, workspace_id=workspace_b["id"]
        )
        assert str(seen_user_4) == user_b.user_id
        assert [str(w) for w in seen_workspaces_4] == [workspace_b["id"]]

        seen_user_5, seen_workspaces_5 = _run_scoped_query(
            engine, user_id=user_a.user_id, workspace_id=workspace_a["id"]
        )
        assert str(seen_user_5) == user_a.user_id
        assert [str(w) for w in seen_workspaces_5] == [workspace_a["id"]]
    finally:
        engine.dispose()
