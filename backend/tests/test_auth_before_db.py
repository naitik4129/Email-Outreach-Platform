from __future__ import annotations

from contextlib import contextmanager
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def opened_sessions(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Records every database session a request opens (none touch a real DB)."""
    opened: list[str] = []

    @contextmanager
    def fake_session_scope(settings: Any) -> Any:
        opened.append("session")
        raise AssertionError("a database session was opened before authentication")
        yield  # pragma: no cover

    monkeypatch.setattr("app.api.deps.session_scope", fake_session_scope)
    return opened


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer not-a-jwt-at-all"},
        {"Authorization": "Bearer "},
    ],
)
def test_unauthenticated_request_never_opens_a_database_session(
    opened_sessions: list[str], headers: dict[str, str]
) -> None:
    # Authentication must finish BEFORE a pooled connection is checked out, so a
    # bad/missing token (or a slow signing-key fetch) can never hold or exhaust
    # the connection pool.
    client = TestClient(create_app(), raise_server_exceptions=False)

    response = client.get("/api/v1/workspaces", headers=headers)

    assert response.status_code == 401
    assert opened_sessions == []
