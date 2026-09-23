from __future__ import annotations

import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from dotenv import dotenv_values
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import Settings, reset_settings_cache
from app.db.session import reset_engine_cache, session_scope
from app.main import create_app

_REPO_ROOT_ENV = Path(__file__).resolve().parents[3] / ".env"


def _request_with_retry(
    client: httpx.Client, method: str, url: str, **kwargs: Any
) -> httpx.Response:
    """A handful of retries on transient transport errors only (not on HTTP
    error status codes). Windows occasionally drops/rejects a fresh outbound
    connection under rapid-fire test setup/teardown; this is about network
    flakiness, not application behavior, so it is not worth failing tests
    over.
    """
    last_exc: Exception | None = None
    for attempt in range(4):
        try:
            return client.request(method, url, **kwargs)
        except httpx.TransportError as exc:
            last_exc = exc
            time.sleep(0.5 * (attempt + 1))
    assert last_exc is not None
    raise last_exc


@pytest.fixture(scope="session")
def supabase_http_client() -> Iterator[httpx.Client]:
    """One shared, connection-reusing client for every Supabase Admin API
    call in the whole test session, instead of a new connection per call."""
    with httpx.Client(timeout=30) as client:
        yield client


@pytest.fixture(autouse=True)
def clean_caches(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Shadows the parent conftest's SQLite-forcing fixture of the same name.

    Integration tests need the real DATABASE_URL/SUPABASE_* values from the
    repo-root .env, not the SQLite placeholder the unit-test suite forces.
    pytest resolves same-named fixtures from the closest conftest, so this
    definition replaces the parent's entirely for everything under
    tests/integration/.
    """
    real_values = dotenv_values(_REPO_ROOT_ENV)
    for key, value in real_values.items():
        if value is not None:
            monkeypatch.setenv(key, value)
    reset_settings_cache()
    reset_engine_cache()
    yield
    reset_settings_cache()
    reset_engine_cache()


def _require_env(name: str) -> str:
    import os

    value = os.environ.get(name, "")
    if not value:
        pytest.skip(f"{name} not set in .env; skipping integration tests")
    return value


@pytest.fixture
def supabase_url(clean_caches: None) -> str:
    return _require_env("SUPABASE_URL")


class TestUser:
    def __init__(self, user_id: str, email: str, access_token: str) -> None:
        self.user_id = user_id
        self.email = email
        self.access_token = access_token

    @property
    def auth_header(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token}"}


@pytest.fixture
def make_test_user(
    clean_caches: None, supabase_http_client: httpx.Client
) -> Iterator[Any]:
    import os

    supabase_url = os.environ.get("SUPABASE_URL", "")
    anon_key = os.environ.get("NEXT_PUBLIC_SUPABASE_ANON_KEY", "")
    service_role_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if not (supabase_url and anon_key and service_role_key):
        pytest.skip(
            "SUPABASE_URL/NEXT_PUBLIC_SUPABASE_ANON_KEY/SUPABASE_SERVICE_ROLE_KEY "
            "must be set in .env to run integration tests"
        )

    created: list[str] = []
    admin_headers = {
        "apikey": service_role_key,
        "Authorization": f"Bearer {service_role_key}",
        "Content-Type": "application/json",
    }

    def _make() -> TestUser:
        email = f"phase1-test-{uuid.uuid4().hex}@example.com"
        password = f"Aa1!{uuid.uuid4().hex}"
        resp = _request_with_retry(
            supabase_http_client,
            "POST",
            f"{supabase_url}/auth/v1/admin/users",
            headers=admin_headers,
            json={"email": email, "password": password, "email_confirm": True},
        )
        resp.raise_for_status()
        user_id = resp.json()["id"]
        created.append(user_id)

        token_resp = _request_with_retry(
            supabase_http_client,
            "POST",
            f"{supabase_url}/auth/v1/token?grant_type=password",
            headers={"apikey": anon_key, "Content-Type": "application/json"},
            json={"email": email, "password": password},
        )
        token_resp.raise_for_status()
        access_token = token_resp.json()["access_token"]
        return TestUser(user_id=user_id, email=email, access_token=access_token)

    yield _make

    settings = Settings.current()
    with session_scope(settings) as cleanup_session:
        cleanup_session.execute(text("SET session_replication_role = replica"))
        for user_id in created:
            _delete_test_user_data(cleanup_session, user_id)
        cleanup_session.execute(text("SET session_replication_role = DEFAULT"))

    for user_id in created:
        _request_with_retry(
            supabase_http_client,
            "DELETE",
            f"{supabase_url}/auth/v1/admin/users/{user_id}",
            headers=admin_headers,
        )


def _delete_test_user_data(session: Session, user_id: str) -> None:
    """Best-effort cascade so a test's auth.users row can actually be deleted
    afterward. profiles/workspaces/workspace_memberships are deliberately
    ON DELETE RESTRICT (no ordinary hard-delete path exists in this schema,
    by design), so leaving them behind would make the Admin API's user-delete
    fail with a foreign key violation and accumulate test data in the real
    project. This is test-only cleanup using the unrestricted (postgres)
    connection; the application itself never deletes these rows.
    """
    workspace_ids = session.execute(
        text(
            "SELECT DISTINCT workspace_id FROM workspace_memberships "
            "WHERE user_id = :user_id"
        ),
        {"user_id": user_id},
    ).scalars().all()

    # Deletion order below must respect FK RESTRICT dependencies (children
    # before parents) or Postgres rejects the delete outright -- this table
    # is deliberately children-first, not alphabetical/historical order:
    #   message_attempts -> messages -> controlled_send_authorizations
    #   -> suppressions -> command_receipts/recipient_addresses
    #   -> mailbox_connections/oauth_flows -> mailboxes
    # A stale ordering here previously aborted the whole per-user cleanup
    # transaction partway through (whichever table it reached first that
    # still had a live child row), silently leaving orphaned workspaces/
    # mailboxes behind in the shared Supabase test project -- including one
    # that collided with a later run via mailboxes_provider_account_global_key.
    for workspace_id in workspace_ids:
        # Phase 7 campaign-family tables reference leads/lead_lists/
        # recipient_addresses/mailboxes/template_versions deleted further
        # down this cascade -- clean up everything except `campaigns` itself
        # (and campaign_enrollments, which messages.enrollment_id can still
        # reference) here, before any of those parent rows are gone.
        # campaigns.draft_audience_id/draft_sequence_id/current_settings_id/
        # activated_* are themselves FKs back into these child tables (a
        # committed audience, a settings revision, ...) -- null them out
        # first, same as templates.current_version_id below, or the child
        # Deletion order below must respect FK RESTRICT dependencies (children
        # before parents). We delete outbox records, message attempts, messages,
        # campaign children, then campaigns itself before deleting the sequences
        # and audiences that campaigns references. This avoids updating activated_*
        # columns on campaigns which would violate app_guard_campaign_snapshot.
        session.execute(
            text("DELETE FROM outbox_deliveries WHERE workspace_id = :workspace_id"),
            {"workspace_id": workspace_id},
        )
        session.execute(
            text("DELETE FROM outbox_work WHERE workspace_id = :workspace_id"),
            {"workspace_id": workspace_id},
        )
        session.execute(
            text("DELETE FROM message_attempts WHERE workspace_id = :workspace_id"),
            {"workspace_id": workspace_id},
        )
        session.execute(
            text("DELETE FROM messages WHERE workspace_id = :workspace_id"),
            {"workspace_id": workspace_id},
        )
        session.execute(
            text(
                "DELETE FROM campaign_enrollments WHERE workspace_id = :workspace_id"
            ),
            {"workspace_id": workspace_id},
        )
        session.execute(
            text(
                "DELETE FROM campaign_planning_jobs "
                "WHERE workspace_id = :workspace_id"
            ),
            {"workspace_id": workspace_id},
        )
        session.execute(
            text(
                "DELETE FROM campaign_mailboxes WHERE workspace_id = :workspace_id"
            ),
            {"workspace_id": workspace_id},
        )
        session.execute(
            text("DELETE FROM campaigns WHERE workspace_id = :workspace_id"),
            {"workspace_id": workspace_id},
        )
        session.execute(
            text(
                "DELETE FROM campaign_audience_members "
                "WHERE workspace_id = :workspace_id"
            ),
            {"workspace_id": workspace_id},
        )
        session.execute(
            text(
                "DELETE FROM audience_capture_sources "
                "WHERE workspace_id = :workspace_id"
            ),
            {"workspace_id": workspace_id},
        )
        session.execute(
            text("DELETE FROM campaign_audiences WHERE workspace_id = :workspace_id"),
            {"workspace_id": workspace_id},
        )
        session.execute(
            text("DELETE FROM sequence_steps WHERE workspace_id = :workspace_id"),
            {"workspace_id": workspace_id},
        )
        session.execute(
            text(
                "DELETE FROM campaign_sequences WHERE workspace_id = :workspace_id"
            ),
            {"workspace_id": workspace_id},
        )
        session.execute(
            text(
                "DELETE FROM campaign_settings_versions "
                "WHERE workspace_id = :workspace_id"
            ),
            {"workspace_id": workspace_id},
        )
        session.execute(
            text(
                "DELETE FROM lead_list_memberships "
                "WHERE workspace_id = :workspace_id"
            ),
            {"workspace_id": workspace_id},
        )
        session.execute(
            text("DELETE FROM leads WHERE workspace_id = :workspace_id"),
            {"workspace_id": workspace_id},
        )
        session.execute(
            text("DELETE FROM lead_lists WHERE workspace_id = :workspace_id"),
            {"workspace_id": workspace_id},
        )
        session.execute(
            text(
                "DELETE FROM controlled_send_authorizations "
                "WHERE workspace_id = :workspace_id"
            ),
            {"workspace_id": workspace_id},
        )
        session.execute(
            text("DELETE FROM suppressions WHERE workspace_id = :workspace_id"),
            {"workspace_id": workspace_id},
        )
        session.execute(
            text("DELETE FROM command_receipts WHERE workspace_id = :workspace_id"),
            {"workspace_id": workspace_id},
        )
        session.execute(
            text("DELETE FROM recipient_addresses WHERE workspace_id = :workspace_id"),
            {"workspace_id": workspace_id},
        )
        session.execute(
            text(
                "UPDATE templates SET current_version_id = NULL "
                "WHERE workspace_id = :workspace_id"
            ),
            {"workspace_id": workspace_id},
        )
        session.execute(
            text("DELETE FROM template_versions WHERE workspace_id = :workspace_id"),
            {"workspace_id": workspace_id},
        )
        session.execute(
            text("DELETE FROM templates WHERE workspace_id = :workspace_id"),
            {"workspace_id": workspace_id},
        )
        session.execute(
            text("DELETE FROM oauth_flows WHERE workspace_id = :workspace_id"),
            {"workspace_id": workspace_id},
        )
        # mailboxes and mailbox_connections reference each other
        # (mailboxes_current_connection_fkey vs. mailbox_connections_mailbox_
        # fkey), so neither can simply be deleted first. Release mailboxes'
        # pointer into mailbox_connections before deleting either: null out
        # connected_generation (composite FK on connected_generation is not
        # enforced when it's NULL) and bump current_connection_generation
        # past it, satisfying app_guard_mailbox_generation the same way the
        # application's own disconnect flow does.
        session.execute(
            text(
                """
                UPDATE mailboxes
                SET connection_state = 'DISCONNECTED',
                    connected_generation = NULL,
                    current_connection_generation = current_connection_generation + 1
                WHERE workspace_id = :workspace_id
                """
            ),
            {"workspace_id": workspace_id},
        )
        session.execute(
            text("DELETE FROM mailbox_connections WHERE workspace_id = :workspace_id"),
            {"workspace_id": workspace_id},
        )
        session.execute(
            text("DELETE FROM mailboxes WHERE workspace_id = :workspace_id"),
            {"workspace_id": workspace_id},
        )
        session.execute(
            text("DELETE FROM audit_events WHERE workspace_id = :workspace_id"),
            {"workspace_id": workspace_id},
        )
        session.execute(
            text(
                "DELETE FROM workspace_bootstrap_receipts "
                "WHERE workspace_id = :workspace_id"
            ),
            {"workspace_id": workspace_id},
        )
        session.execute(
            text(
                "DELETE FROM workspace_memberships WHERE workspace_id = :workspace_id"
            ),
            {"workspace_id": workspace_id},
        )
        session.execute(
            text("DELETE FROM workspaces WHERE id = :workspace_id"),
            {"workspace_id": workspace_id},
        )

    session.execute(
        text("DELETE FROM workspace_bootstrap_receipts WHERE actor_id = :user_id"),
        {"user_id": user_id},
    )
    session.execute(
        text("DELETE FROM profiles WHERE id = :user_id"), {"user_id": user_id}
    )


@pytest.fixture
def api_client(clean_caches: None) -> TestClient:
    return TestClient(create_app())


@pytest.fixture
def raw_db(clean_caches: None) -> Iterator[Session]:
    """A raw session pre-switched to app_api, for RLS assertions independent
    of the FastAPI layer. Callers set app.user_id/app.workspace_id themselves.
    """
    settings = Settings.current()
    with session_scope(settings) as session:
        session.execute(text("SET LOCAL ROLE app_api"))
        yield session


@pytest.fixture
def db_admin(clean_caches: None) -> Iterator[Session]:
    """Unrestricted (postgres, BYPASSRLS) session for test setup/teardown
    only -- e.g. seeding a MANAGER/MEMBER/VIEWER membership for RBAC-matrix
    tests, since Phase 1 has no invite/role-assignment command to do this
    through the API. Never used to assert application behavior itself.
    """
    settings = Settings.current()
    with session_scope(settings) as session:
        yield session
