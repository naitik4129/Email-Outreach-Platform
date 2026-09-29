"""Migration 0034 -- app_delete_workspace -- against a REAL PostgreSQL.

Same throwaway-pgserver approach as test_erasure_real_db.py: the actual SQL runs
as the real `app_api` role with RLS, grants and guard triggers fully enforced.
Nothing touches the production-linked Supabase project.

What is proven here: only an OWNER (not ADMIN/MANAGER/MEMBER) may call it, that
another tenant's workspace can't be named, that a wrong typed confirmation is
rejected and the workspace survives, that a workspace with an activated/FROZEN
campaign, sent messages and an active suppression is still fully deletable (the
whole point of migration 0034's guard-trigger bypasses), that suppressions and
recipient_addresses are genuinely deleted too (the explicit 0033 deviation),
that a second call after a successful delete fails safely rather than crashing
or double-deleting, and that another tenant's data survives untouched.
"""

# ruff: noqa: E402, E501
from __future__ import annotations

import uuid
from typing import Any

import pytest

pytest.importorskip("pgserver")
psycopg = pytest.importorskip("psycopg")

from tests.support.real_pg import throwaway_database
from tests.support.real_seed import World, _insert, seed_world
from tests.test_erasure_real_db import call, code, count, expect, sent_world

pytestmark = pytest.mark.filterwarnings("ignore")

# World has no `name` field; seed_world() always inserts this literal.
WORKSPACE_NAME = "Acme Workspace"


@pytest.fixture(scope="module")
def uri():
    with throwaway_database() as value:
        yield value


@pytest.fixture()
def su(uri):
    conn = psycopg.connect(uri, autocommit=True)
    yield conn
    conn.close()


def delete_ws(uri, w: World, confirm: str, *, user=None, workspace=None) -> Any:
    """app_delete_workspace's own signature is (p_workspace, p_confirmation_name);
    the generic `call()` helper's second positional slot fits either target id or
    a text argument, so this reuses it directly."""
    return call(uri, "app_delete_workspace", w, confirm, user=user, workspace=workspace)


def freeze_sequence(su, sequence_id) -> None:
    """A real activation flow freezes the sequence; this seed doesn't, so force
    it the same way raw_insert forces other post-activation state."""
    su.execute("SET session_replication_role = replica")
    su.execute(
        "UPDATE public.campaign_sequences SET status='FROZEN', frozen_at=now() WHERE id=%s",
        [sequence_id],
    )
    su.execute("SET session_replication_role = origin")


def add_admin(su, w: World) -> uuid.UUID:
    admin = uuid.uuid4()
    su.execute("SET session_replication_role = replica")
    su.execute("INSERT INTO auth.users (id) VALUES (%s) ON CONFLICT DO NOTHING", [admin])
    _insert(su, "profiles", id=admin)
    _insert(su, "workspace_memberships", workspace_id=w.ws, user_id=admin, role_code="ADMIN")
    su.execute("SET session_replication_role = origin")
    return admin


# ---------------------------------------------------------------------------
# authorization and tenancy
# ---------------------------------------------------------------------------


class TestAuthorization:
    def test_only_owner_may_call_not_admin_or_below(self, uri, su) -> None:
        """Stricter than 0033's commands: those allow ADMIN (workspace.manage),
        this requires ownership.manage (OWNER only)."""
        w = seed_world(su, running=False, enroll_first=False, members=0)
        admin = add_admin(su, w)
        for who in (w.member, w.manager, admin):
            expect(uri, "app_delete_workspace", w, WORKSPACE_NAME, "erasure:forbidden", user=who)
        assert count(su, "workspaces", "id=%s", w.ws) == 1

    def test_owner_may_call(self, uri, su) -> None:
        w = seed_world(su, running=False, enroll_first=False, members=0)
        delete_ws(uri, w, WORKSPACE_NAME)
        assert count(su, "workspaces", "id=%s", w.ws) == 0
        assert count(su, "workspace_memberships", "workspace_id=%s", w.ws) == 0

    def test_another_tenants_workspace_cannot_be_named(self, uri, su) -> None:
        mine = seed_world(su, running=False, enroll_first=False, members=0)
        other = seed_world(su, running=False, enroll_first=False, members=0)
        # other's owner names mine's workspace id -- forbidden, not not_found:
        # the actor has no membership in `mine` at all.
        expect(uri, "app_delete_workspace", other, WORKSPACE_NAME, "erasure:forbidden",
               user=other.owner, workspace=mine.ws)
        assert count(su, "workspaces", "id=%s", mine.ws) == 1

    def test_no_user_no_access(self, uri, su) -> None:
        w = seed_world(su, running=False, enroll_first=False, members=0)
        conn = psycopg.connect(uri)
        try:
            conn.execute("SET LOCAL ROLE app_api")
            conn.execute("SELECT set_config('app.workspace_id', %s, true)", [str(w.ws)])
            with pytest.raises(psycopg.Error) as info:
                conn.execute("SELECT public.app_delete_workspace(%s, %s)", [w.ws, WORKSPACE_NAME])
            assert code(info.value) == "erasure:forbidden"
        finally:
            conn.close()

    def test_workers_cannot_execute(self, uri) -> None:
        for role in ("app_worker_send", "app_worker_general", "anon", "authenticated", "service_role"):
            with psycopg.connect(uri) as conn:
                assert not conn.execute(
                    "SELECT has_function_privilege(%s, %s, 'EXECUTE')",
                    [role, "public.app_delete_workspace(uuid,text)"],
                ).fetchone()[0], role


# ---------------------------------------------------------------------------
# confirmation
# ---------------------------------------------------------------------------


class TestConfirmation:
    def test_wrong_confirmation_is_rejected_and_workspace_survives(self, uri, su) -> None:
        w = seed_world(su, running=False, enroll_first=False, members=0)
        expect(uri, "app_delete_workspace", w, "not the workspace name", "erasure:invalid")
        assert count(su, "workspaces", "id=%s", w.ws) == 1

    def test_double_submit_after_success_fails_safely(self, uri, su) -> None:
        """The second call's actor has no membership left (it was deleted with
        everything else), so this resolves to forbidden, not not_found -- the
        authorization check runs before the row lookup, and here the target
        *is* the actor's own membership/workspace, so the two are entangled.
        The important property is that it fails cleanly, not that it crashes
        or silently no-ops a second deletion."""
        w = seed_world(su, running=False, enroll_first=False, members=0)
        delete_ws(uri, w, WORKSPACE_NAME)
        expect(uri, "app_delete_workspace", w, WORKSPACE_NAME, "erasure:forbidden")


# ---------------------------------------------------------------------------
# the actual delete: everything a workspace owns, including an activated,
# sent, FROZEN-sequence campaign -- exactly the case 0034's guard bypasses
# (Findings A/B/C/D) exist for.
# ---------------------------------------------------------------------------


class TestDeletesEverything:
    def test_a_fully_activated_workspace_with_an_active_suppression_is_deleted_whole(
        self, uri, su
    ) -> None:
        w = sent_world(su, members=2)
        freeze_sequence(su, w.sequence_id)

        # An address with an ACTIVE suppression normally survives erasure
        # (0033). Workspace delete is the explicit, documented exception.
        suppressed_address = w.address_ids[0]
        _insert(
            su, "suppressions", workspace_id=w.ws, address_id=suppressed_address,
            reason="UNSUBSCRIBE", release_audit_id=None,
        )
        assert count(su, "suppressions", "workspace_id=%s", w.ws) == 1

        delete_ws(uri, w, WORKSPACE_NAME)

        for table in (
            "workspaces", "workspace_memberships", "campaigns", "campaign_sequences",
            "sequence_steps", "campaign_audiences", "campaign_audience_members",
            "leads", "recipient_addresses", "suppressions", "mailboxes",
            "messages", "message_attempts", "message_events", "message_generations",
            "inbound_messages", "inbound_outreach_links", "personalization_previews",
            "campaign_settings_versions", "audit_events",
        ):
            col = "id" if table == "workspaces" else "workspace_id"
            assert count(su, table, f"{col}=%s", w.ws) == 0, table

    def test_a_never_activated_workspace_is_deleted_too(self, uri, su) -> None:
        w = seed_world(su, running=False, enroll_first=False, members=3)
        delete_ws(uri, w, WORKSPACE_NAME)
        assert count(su, "workspaces", "id=%s", w.ws) == 0


# ---------------------------------------------------------------------------
# cross-tenant isolation
# ---------------------------------------------------------------------------


class TestCrossTenantIsolation:
    def test_deleting_one_workspace_leaves_another_untouched(self, uri, su) -> None:
        mine = sent_world(su, members=2)
        other = sent_world(su, members=2)

        delete_ws(uri, mine, WORKSPACE_NAME)

        assert count(su, "workspaces", "id=%s", mine.ws) == 0
        assert count(su, "workspaces", "id=%s", other.ws) == 1
        assert count(su, "campaigns", "workspace_id=%s", other.ws) == 1
        assert count(su, "leads", "workspace_id=%s", other.ws) == 2
        assert count(su, "workspace_memberships", "workspace_id=%s", other.ws) == 3
