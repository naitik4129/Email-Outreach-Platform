"""Archive / unarchive / bulk archive against a REAL PostgreSQL (pgserver).

Covers ADR-0015 Phase 1: campaigns can be archived from every state the state
machine allows (never RUNNING), templates / leads / lead lists can be restored,
archived items leave the everyday lists, bulk requests report per-item results,
and another tenant's ids never work. Everything runs as the real `app_api`
role, so RLS, grants and the campaign guard trigger are exercised for real.
Nothing touches the production-linked Supabase project.
"""

# ruff: noqa: E402, E501
from __future__ import annotations

import uuid

import pytest

pytest.importorskip("pgserver")
psycopg = pytest.importorskip("psycopg")

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.api.deps import WorkspaceContext, get_db, get_workspace_context
from app.core.errors import AppError
from app.db.context import set_transaction_context
from app.main import app
from app.modules.campaigns.service import CampaignService
from app.modules.leads.service import LeadService
from app.modules.templates.service import TemplateService
from app.schemas.leads import ExpectedVersionIn
from app.schemas.templates import TemplateArchiveIn, TemplateCreateIn
from tests.support.real_pg import throwaway_database
from tests.support.real_seed import World, _insert, seed_world

pytestmark = pytest.mark.filterwarnings("ignore")


@pytest.fixture(scope="module")
def uri():
    with throwaway_database() as value:
        yield value


@pytest.fixture(scope="module")
def session_factory(uri):
    engine = create_engine("postgresql+psycopg://" + uri.split("://", 1)[1], future=True)
    yield sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
    engine.dispose()


@pytest.fixture()
def su(uri):
    conn = psycopg.connect(uri, autocommit=True)
    yield conn
    conn.close()


def api(session_factory, w: World, role: str = "MANAGER") -> tuple[Session, WorkspaceContext]:
    """An app_api request for the tenant, acting as a user with `role`."""
    user = {"OWNER": w.owner, "MANAGER": w.manager, "MEMBER": w.member}[role]
    session = session_factory()
    session.execute(text("SET LOCAL ROLE app_api"))
    set_transaction_context(session, user_id=user, workspace_id=w.ws)
    return session, WorkspaceContext(workspace_id=w.ws, user_id=user, role_code=role)


def set_status(su, w: World, status: str) -> None:
    su.execute("UPDATE public.campaigns SET status=%s WHERE id=%s", [status, w.campaign_id])


def version(su, table: str, row_id) -> int:
    return su.execute(f"SELECT version FROM public.{table} WHERE id=%s", [row_id]).fetchone()[0]


# ---------------------------------------------------------------------------
# campaigns
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("state", ["PAUSED", "COMPLETED"])
def test_manager_archives_an_activated_campaign(session_factory, su, state) -> None:
    w = seed_world(su, running=True)
    set_status(su, w, state)
    session, ctx = api(session_factory, w)
    out = CampaignService(session).archive_campaign(
        ctx, w.campaign_id, version(su, "campaigns", w.campaign_id)
    )
    session.commit()
    session.close()

    assert out.status == "ARCHIVED" and out.archived_at is not None
    row = su.execute(
        "SELECT previous_status FROM public.campaigns WHERE id=%s", [w.campaign_id]
    ).fetchone()
    assert row[0] == state
    audit = su.execute(
        "SELECT before_state->>'status' FROM public.audit_events "
        "WHERE target_id=%s AND action='campaign.archive'",
        [w.campaign_id],
    ).fetchone()
    assert audit[0] == state


def test_running_campaign_cannot_be_archived(session_factory, su) -> None:
    w = seed_world(su, running=True)
    session, ctx = api(session_factory, w)
    with pytest.raises(AppError) as info:
        CampaignService(session).archive_campaign(
            ctx, w.campaign_id, version(su, "campaigns", w.campaign_id)
        )
    session.rollback()
    session.close()
    assert info.value.status_code == 409 and "Pause" in info.value.message
    assert su.execute(
        "SELECT status FROM public.campaigns WHERE id=%s", [w.campaign_id]
    ).fetchone()[0] == "RUNNING"


def test_member_cannot_archive_an_activated_campaign(session_factory, su) -> None:
    w = seed_world(su, running=True)
    set_status(su, w, "PAUSED")
    session, ctx = api(session_factory, w, "MEMBER")
    with pytest.raises(AppError) as info:
        CampaignService(session).archive_campaign(
            ctx, w.campaign_id, version(su, "campaigns", w.campaign_id)
        )
    session.rollback()
    session.close()
    assert info.value.status_code == 403


def test_member_can_still_archive_a_draft(session_factory, su) -> None:
    w = seed_world(su, running=False, enroll_first=False)
    session, ctx = api(session_factory, w, "MEMBER")
    out = CampaignService(session).archive_campaign(
        ctx, w.campaign_id, version(su, "campaigns", w.campaign_id)
    )
    session.commit()
    session.close()
    assert out.status == "ARCHIVED"


def test_archiving_twice_is_idempotent(session_factory, su) -> None:
    w = seed_world(su, running=True)
    set_status(su, w, "PAUSED")
    for _ in range(2):
        session, ctx = api(session_factory, w)
        out = CampaignService(session).archive_campaign(
            ctx, w.campaign_id, version(su, "campaigns", w.campaign_id)
        )
        session.commit()
        session.close()
        assert out.status == "ARCHIVED"


def test_stale_version_is_a_conflict(session_factory, su) -> None:
    w = seed_world(su, running=True)
    set_status(su, w, "PAUSED")
    session, ctx = api(session_factory, w)
    with pytest.raises(AppError) as info:
        CampaignService(session).archive_campaign(ctx, w.campaign_id, 999)
    session.rollback()
    session.close()
    assert info.value.status_code == 409


def test_campaign_list_hides_archived_unless_asked(session_factory, su) -> None:
    w = seed_world(su, running=True)
    set_status(su, w, "PAUSED")
    session, ctx = api(session_factory, w)
    CampaignService(session).archive_campaign(
        ctx, w.campaign_id, version(su, "campaigns", w.campaign_id)
    )
    session.commit()
    session.close()

    def listed(status):
        s, c = api(session_factory, w)
        try:
            page = CampaignService(s).list_campaigns(
                c, limit=50, cursor=None, query=None, status=status
            )
            return {str(item.id) for item in page.items}
        finally:
            s.close()

    cid = str(w.campaign_id)
    assert cid not in listed(None)
    assert cid in listed("ARCHIVED")
    assert cid in listed("ALL")


def test_another_tenant_cannot_archive_a_campaign(session_factory, su) -> None:
    mine = seed_world(su, running=True)
    set_status(su, mine, "PAUSED")
    other = seed_world(su, running=True)
    session, ctx = api(session_factory, other)
    with pytest.raises(AppError) as info:
        CampaignService(session).archive_campaign(
            ctx, mine.campaign_id, version(su, "campaigns", mine.campaign_id)
        )
    session.rollback()
    session.close()
    assert info.value.status_code == 404
    assert su.execute(
        "SELECT status FROM public.campaigns WHERE id=%s", [mine.campaign_id]
    ).fetchone()[0] == "PAUSED"


# ---------------------------------------------------------------------------
# templates, leads, lead lists: archive and restore
# ---------------------------------------------------------------------------


def test_template_archive_and_restore(session_factory, su) -> None:
    w = seed_world(su, running=False, enroll_first=False)
    session, ctx = api(session_factory, w, "MEMBER")
    service = TemplateService(session)
    created = service.create_template(
        ctx, TemplateCreateIn(name="Intro", subject="Hi", body_html="<p>Hello</p>")
    )
    session.commit()
    session.close()

    session, ctx = api(session_factory, w, "MEMBER")
    archived = TemplateService(session).archive_template(
        ctx, created.id, TemplateArchiveIn(expected_version=created.version)
    )
    session.commit()
    session.close()
    assert archived.archived_at is not None

    session, ctx = api(session_factory, w, "MEMBER")
    restored = TemplateService(session).unarchive_template(
        ctx, created.id, TemplateArchiveIn(expected_version=archived.version)
    )
    session.commit()
    session.close()
    assert restored.archived_at is None
    actions = {
        r[0]
        for r in su.execute(
            "SELECT action FROM public.audit_events WHERE target_id=%s", [created.id]
        ).fetchall()
    }
    assert {"template.archive", "template.unarchive"} <= actions


def test_lead_archive_and_restore(session_factory, su) -> None:
    w = seed_world(su, running=False, enroll_first=False, members=1)
    lead = w.lead_ids[0]
    session, ctx = api(session_factory, w, "MEMBER")
    archived = LeadService(session).archive_lead(
        ctx, lead, ExpectedVersionIn(expected_version=version(su, "leads", lead))
    )
    session.commit()
    session.close()
    assert archived.status == "ARCHIVED"

    session, ctx = api(session_factory, w, "MEMBER")
    restored = LeadService(session).unarchive_lead(
        ctx, lead, ExpectedVersionIn(expected_version=version(su, "leads", lead))
    )
    session.commit()
    session.close()
    assert restored.status == "ACTIVE" and restored.archived_at is None


def test_lead_list_archive_restore_and_filters(session_factory, su) -> None:
    w = seed_world(su, running=False, enroll_first=False, members=0)
    list_id = uuid.uuid4()
    _insert(su, "lead_lists", id=list_id, workspace_id=w.ws, name="Cold list")

    session, ctx = api(session_factory, w, "MEMBER")
    LeadService(session).archive_list(
        ctx, list_id, ExpectedVersionIn(expected_version=version(su, "lead_lists", list_id))
    )
    session.commit()
    session.close()

    def names(status):
        s, c = api(session_factory, w, "MEMBER")
        try:
            return {i.name for i in LeadService(s).list_lists(c, limit=50, cursor=None, status=status).items}
        finally:
            s.close()

    assert "Cold list" not in names("ACTIVE")
    assert "Cold list" in names("ARCHIVED")
    assert "Cold list" in names("ALL")

    session, ctx = api(session_factory, w, "MEMBER")
    restored = LeadService(session).unarchive_list(
        ctx, list_id, ExpectedVersionIn(expected_version=version(su, "lead_lists", list_id))
    )
    session.commit()
    session.close()
    assert restored.archived_at is None
    assert "Cold list" in names("ACTIVE")


# ---------------------------------------------------------------------------
# bulk endpoints (through the real routes)
# ---------------------------------------------------------------------------


@pytest.fixture()
def bulk_client(session_factory):
    holder: dict[str, tuple[World, str]] = {}

    def override_get_db():
        w, role = holder["as"]
        session, _ = api(session_factory, w, role)
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def override_ctx():
        w, role = holder["as"]
        user = {"OWNER": w.owner, "MANAGER": w.manager, "MEMBER": w.member}[role]
        return WorkspaceContext(workspace_id=w.ws, user_id=user, role_code=role)

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_workspace_context] = override_ctx
    client = TestClient(app)
    client.holder = holder  # type: ignore[attr-defined]
    yield client
    app.dependency_overrides.clear()


def test_bulk_archive_reports_each_item_and_commits_the_good_ones(bulk_client, su) -> None:
    w = seed_world(su, running=False, enroll_first=False, members=3)
    other = seed_world(su, running=False, enroll_first=False, members=1)
    good, stale, foreign = w.lead_ids[0], w.lead_ids[1], other.lead_ids[0]
    bulk_client.holder["as"] = (w, "MEMBER")

    res = bulk_client.post(
        f"/api/v1/workspaces/{w.ws}/leads/bulk-archive",
        json={
            "items": [
                {"id": str(good), "expected_version": version(su, "leads", good)},
                {"id": str(stale), "expected_version": 999},
                {"id": str(foreign), "expected_version": 1},
            ]
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    by_id = {r["id"]: r for r in body["results"]}
    assert by_id[str(good)]["ok"] is True
    assert by_id[str(stale)]["ok"] is False and "conflict" in by_id[str(stale)]["code"]
    assert by_id[str(foreign)]["ok"] is False and by_id[str(foreign)]["code"] == "not_found"
    assert (body["succeeded"], body["failed"]) == (1, 2)

    status = lambda lead: su.execute("SELECT status FROM public.leads WHERE id=%s", [lead]).fetchone()[0]  # noqa: E731
    assert status(good) == "ARCHIVED"
    assert status(stale) == "ACTIVE"
    assert status(foreign) == "ACTIVE"  # the other tenant's lead is untouched


def test_bulk_archive_campaigns_refuses_running_and_archives_paused(bulk_client, su) -> None:
    running = seed_world(su, running=True)
    bulk_client.holder["as"] = (running, "MANAGER")
    res = bulk_client.post(
        f"/api/v1/workspaces/{running.ws}/campaigns/bulk-archive",
        json={"items": [{"id": str(running.campaign_id), "expected_version": version(su, "campaigns", running.campaign_id)}]},
    )
    assert res.status_code == 200
    assert res.json()["results"][0]["code"] == "state_conflict"

    set_status(su, running, "PAUSED")
    res = bulk_client.post(
        f"/api/v1/workspaces/{running.ws}/campaigns/bulk-archive",
        json={"items": [{"id": str(running.campaign_id), "expected_version": version(su, "campaigns", running.campaign_id)}]},
    )
    assert res.json()["results"][0]["ok"] is True


def test_bulk_request_validation(bulk_client, su) -> None:
    w = seed_world(su, running=False, enroll_first=False, members=1)
    bulk_client.holder["as"] = (w, "MEMBER")
    lead = w.lead_ids[0]
    url = f"/api/v1/workspaces/{w.ws}/leads/bulk-archive"
    item = {"id": str(lead), "expected_version": 1}
    assert bulk_client.post(url, json={"items": []}).status_code == 422
    assert bulk_client.post(url, json={"items": [item, item]}).status_code == 422
    too_many = [{"id": str(uuid.uuid4()), "expected_version": 1} for _ in range(101)]
    assert bulk_client.post(url, json={"items": too_many}).status_code == 422


def test_viewer_role_cannot_bulk_archive(bulk_client, su) -> None:
    w = seed_world(su, running=False, enroll_first=False, members=1)
    bulk_client.holder["as"] = (w, "MEMBER")

    def viewer_ctx():
        return WorkspaceContext(workspace_id=w.ws, user_id=w.member, role_code="VIEWER")

    app.dependency_overrides[get_workspace_context] = viewer_ctx
    res = bulk_client.post(
        f"/api/v1/workspaces/{w.ws}/leads/bulk-archive",
        json={"items": [{"id": str(w.lead_ids[0]), "expected_version": 1}]},
    )
    assert res.status_code == 403


# ---------------------------------------------------------------------------
# is_activated / erased_at as the UI reads them
# ---------------------------------------------------------------------------


def test_campaign_reports_whether_it_was_activated_and_when_it_was_erased(session_factory, su) -> None:
    draft = seed_world(su, running=False, enroll_first=False, members=0)
    activated = seed_world(su, running=True)
    set_status(su, activated, "PAUSED")

    def read(w):
        session, ctx = api(session_factory, w)
        try:
            return CampaignService(session).get_campaign(ctx, w.campaign_id)
        finally:
            session.close()

    assert read(draft).is_activated is False and read(draft).erased_at is None
    assert read(activated).is_activated is True

    su.execute("UPDATE public.campaigns SET erased_at=now() WHERE id=%s", [activated.campaign_id])
    assert read(activated).erased_at is not None


def test_the_new_fields_tolerate_a_database_without_migration_0033(session_factory) -> None:
    """Code ships before the migration is applied: a row without erased_at must map."""
    from datetime import UTC, datetime

    session = session_factory()
    now = datetime.now(UTC)
    row = {
        "id": uuid.uuid4(), "workspace_id": uuid.uuid4(), "name": "N", "description": None,
        "creator_id": uuid.uuid4(), "status": "DRAFT", "campaign_type": "STANDARD", "start_at": None,
        "draft_sequence_id": None, "draft_audience_id": None, "current_settings_id": None,
        "planning_status": "PENDING", "archived_at": None, "error_reason": None,
        "activation_id": None, "version": 1, "created_at": now, "updated_at": now,
    }
    service = CampaignService(session)
    assert service._to_list_item(row).erased_at is None
    assert service._to_detail_out(row).is_activated is False
    session.close()
