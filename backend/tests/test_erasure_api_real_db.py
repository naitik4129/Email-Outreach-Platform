"""The erasure HTTP routes against a REAL PostgreSQL (pgserver), file storage faked.

Covers what the SQL tests cannot: the server-side typed confirmation, the role gate,
translation of database refusals into client-safe errors, that nothing is removed on
a refused request, and that stored files are removed only after the database change
and only when nothing else still references them. No file is really deleted.
"""

# ruff: noqa: E402, E501
from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest

pytest.importorskip("pgserver")
psycopg = pytest.importorskip("psycopg")

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from app.api.deps import WorkspaceContext, get_db, get_workspace_context
from app.db.context import set_transaction_context
from app.main import app
from app.modules.imports.storage import StorageUnavailableError
from tests.support.real_pg import throwaway_database
from tests.support.real_seed import World, _insert, seed_world
from tests.test_hard_delete_real_db import disconnect, settled_world

pytestmark = pytest.mark.filterwarnings("ignore")


class FakeStorage:
    deleted: list[str] = []
    fail = False

    def __init__(self, *_a, **_k) -> None:
        pass

    def delete_object(self, key: str) -> None:
        if FakeStorage.fail:
            raise StorageUnavailableError("down")
        FakeStorage.deleted.append(key)


@pytest.fixture(scope="module")
def uri():
    with throwaway_database() as value:
        yield value


@pytest.fixture()
def su(uri):
    conn = psycopg.connect(uri, autocommit=True)
    yield conn
    conn.close()


@pytest.fixture()
def client(uri, monkeypatch):
    engine = create_engine("postgresql+psycopg://" + uri.split("://", 1)[1], future=True)
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False, future=True)
    holder: dict[str, tuple[World, str]] = {}

    def as_user() -> tuple[World, str, uuid.UUID]:
        w, role = holder["as"]
        user = {"OWNER": w.owner, "MANAGER": w.manager, "MEMBER": w.member}[role]
        return w, role, user

    def override_get_db():
        w, _role, user = as_user()
        session = factory()
        session.execute(text("SET LOCAL ROLE app_api"))
        set_transaction_context(session, user_id=user, workspace_id=w.ws)
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def override_ctx():
        w, role, user = as_user()
        return WorkspaceContext(workspace_id=w.ws, user_id=user, role_code=role)

    FakeStorage.deleted, FakeStorage.fail = [], False
    monkeypatch.setattr("app.modules.erasure.service.SupabaseStorageClient", FakeStorage)
    monkeypatch.setattr("app.modules.erasure.service.default_storage_client", FakeStorage)
    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_workspace_context] = override_ctx
    test_client = TestClient(app)
    test_client.holder = holder  # type: ignore[attr-defined]
    yield test_client
    app.dependency_overrides.clear()
    engine.dispose()


def post(client, w: World, path: str, confirm: str):
    return client.post(f"/api/v1/workspaces/{w.ws}{path}", json={"confirm": confirm})


def scalar(su, sql: str, *params):
    return su.execute(sql, list(params)).fetchone()[0]


def add_attachment(su, w: World, key: str, *, campaign=None, step=None, sequence=None) -> None:
    su.execute(
        """INSERT INTO public.campaign_step_attachments
           (workspace_id, campaign_id, sequence_id, step_id, disposition, content_id, storage_key,
            filename, content_type, size_bytes, sha256)
           VALUES (%s,%s,%s,%s,'ATTACHMENT',%s,%s,'f.pdf','application/pdf',10,%s)""",
        [w.ws, campaign or w.campaign_id, sequence or w.sequence_id, step or w.step1,
         uuid.uuid4().hex[:12], key, uuid.uuid4().hex * 2],
    )


# ---------------------------------------------------------------------------


def test_wrong_confirmation_is_refused_and_removes_nothing(client, su) -> None:
    w = seed_world(su, running=False, enroll_first=False, members=0)
    client.holder["as"] = (w, "OWNER")
    res = post(client, w, f"/campaigns/{w.campaign_id}/purge", "not the name")
    assert res.status_code == 422 and res.json()["error"]["code"] == "confirmation_mismatch"
    assert scalar(su, "SELECT count(*) FROM public.campaigns WHERE id=%s", w.campaign_id) == 1


def test_purge_draft_with_the_right_name_removes_it(client, su) -> None:
    w = seed_world(su, running=False, enroll_first=False, members=1)
    client.holder["as"] = (w, "OWNER")
    res = post(client, w, f"/campaigns/{w.campaign_id}/purge", "  Outbound  ")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["operation"] == "campaign.delete" and body["files_pending_cleanup"] == 0
    assert scalar(su, "SELECT count(*) FROM public.campaigns WHERE id=%s", w.campaign_id) == 0
    assert scalar(su, "SELECT count(*) FROM public.leads WHERE workspace_id=%s", w.ws) == 1


def test_only_admin_or_owner_may_purge(client, su) -> None:
    w = seed_world(su, running=False, enroll_first=False, members=0)
    for role in ("MANAGER", "MEMBER"):
        client.holder["as"] = (w, role)
        res = post(client, w, f"/campaigns/{w.campaign_id}/purge", "Outbound")
        assert res.status_code == 403
    assert scalar(su, "SELECT count(*) FROM public.campaigns WHERE id=%s", w.campaign_id) == 1


def test_refusal_from_the_database_is_translated(client, su) -> None:
    w = seed_world(su, running=True)
    client.holder["as"] = (w, "OWNER")
    res = post(client, w, f"/campaigns/{w.campaign_id}/purge", "Outbound")
    assert res.status_code == 409
    err = res.json()["error"]
    assert err["code"] == "state_conflict" and "archive" in err["message"].lower()
    assert "erasure:" not in res.text and "public." not in res.text  # nothing internal leaks


def test_another_tenants_id_is_404(client, su) -> None:
    mine = seed_world(su, running=False, enroll_first=False, members=0)
    other = seed_world(su, running=False, enroll_first=False, members=0)
    client.holder["as"] = (other, "OWNER")
    res = post(client, other, f"/campaigns/{mine.campaign_id}/purge", "Outbound")
    assert res.status_code == 404
    assert scalar(su, "SELECT count(*) FROM public.campaigns WHERE id=%s", mine.campaign_id) == 1


def test_attachment_files_are_removed_only_when_unshared(client, su) -> None:
    w = seed_world(su, running=False, enroll_first=False, members=0)
    other_campaign = seed_world(su, running=False, enroll_first=False, members=0)
    add_attachment(su, w, "ws/only-here.pdf")
    add_attachment(su, w, "ws/shared.pdf")
    # a duplicated campaign in the same workspace still uses shared.pdf
    dup = uuid.uuid4()
    su.execute("SET session_replication_role = replica")
    _insert(su, "campaigns", id=dup, workspace_id=w.ws, name="Copy", creator_id=w.manager)
    seq = uuid.uuid4()
    _insert(su, "campaign_sequences", id=seq, workspace_id=w.ws, campaign_id=dup, revision=1, status="DRAFT")
    step = uuid.uuid4()
    _insert(su, "sequence_steps", id=step, workspace_id=w.ws, sequence_id=seq, campaign_id=dup, position=1,
            kind="EMAIL", email_subject="s", email_body_html="<p>b</p>")
    su.execute("SET session_replication_role = origin")
    add_attachment(su, w, "ws/shared.pdf", campaign=dup, step=step, sequence=seq)

    client.holder["as"] = (w, "OWNER")
    res = post(client, w, f"/campaigns/{w.campaign_id}/purge", "Outbound")
    assert res.status_code == 200, res.text
    assert FakeStorage.deleted == ["ws/only-here.pdf"]
    assert other_campaign is not None


def test_a_storage_failure_never_undoes_the_committed_purge(client, su) -> None:
    w = seed_world(su, running=False, enroll_first=False, members=0)
    add_attachment(su, w, "ws/a.pdf")
    FakeStorage.fail = True
    client.holder["as"] = (w, "OWNER")
    res = post(client, w, f"/campaigns/{w.campaign_id}/purge", "Outbound")
    assert res.status_code == 200
    assert res.json()["files_pending_cleanup"] == 1
    assert scalar(su, "SELECT count(*) FROM public.campaigns WHERE id=%s", w.campaign_id) == 0


def test_erase_lead_requires_the_email_and_returns_no_personal_data(client, su) -> None:
    w = seed_world(su, running=False, enroll_first=False, members=2)
    lead = w.lead_ids[0]
    client.holder["as"] = (w, "OWNER")
    assert post(client, w, f"/leads/{lead}/erase", "someone@else.test").status_code == 422
    assert scalar(su, "SELECT first_name FROM public.leads WHERE id=%s", lead) == "Lead0"

    res = post(client, w, f"/leads/{lead}/erase", "lead0@target0.test")
    assert res.status_code == 200, res.text
    assert "lead0@target0.test" not in res.text and "Lead0" not in res.text
    assert scalar(su, "SELECT first_name IS NULL FROM public.leads WHERE id=%s", lead)
    assert scalar(su, "SELECT first_name FROM public.leads WHERE id=%s", w.lead_ids[1]) == "Lead1"


def test_purge_import_removes_the_files_it_named(client, su) -> None:
    w = seed_world(su, running=False, enroll_first=False, members=0)
    job = uuid.uuid4()
    su.execute("SET session_replication_role = replica")
    _insert(su, "import_jobs", id=job, workspace_id=w.ws, initiator_id=w.owner,
            storage_object_key=f"imports/{job}.csv", storage_object_version="1",
            storage_object_digest="f" * 64, import_kind="LEADS", status="COMPLETED", total_rows=0)
    su.execute("SET session_replication_role = origin")
    client.holder["as"] = (w, "OWNER")
    assert post(client, w, f"/imports/{job}/purge", "nope").status_code == 422
    res = post(client, w, f"/imports/{job}/purge", "DELETE")
    assert res.status_code == 200, res.text
    assert FakeStorage.deleted == [f"imports/{job}.csv"]


def test_purge_template_list_and_mailbox_routes(client, su) -> None:
    w = seed_world(su, running=False, enroll_first=False, members=0)
    template, version, lst, mailbox = (uuid.uuid4() for _ in range(4))
    su.execute("SET session_replication_role = replica")
    _insert(su, "templates", id=template, workspace_id=w.ws, name="Intro", current_version_id=version,
            archived_at=datetime.now(UTC))
    _insert(su, "template_versions", id=version, workspace_id=w.ws, template_id=template, revision=1,
            subject="S", body_html="<p>B</p>", content_digest="e" * 64)
    _insert(su, "lead_lists", id=lst, workspace_id=w.ws, name="Cold", archived_at=datetime.now(UTC))
    _insert(su, "mailboxes", id=mailbox, workspace_id=w.ws, provider="SMTP", original_address="spare@acme.test",
            provider_account_id="spare@acme.test", connection_state="DISCONNECTED")
    su.execute("SET session_replication_role = origin")
    client.holder["as"] = (w, "OWNER")
    assert post(client, w, f"/templates/{template}/purge", "Intro").status_code == 200
    assert post(client, w, f"/lead-lists/{lst}/purge", "Cold").status_code == 200
    assert post(client, w, f"/mailboxes/{mailbox}/purge", "spare@acme.test").status_code == 200
    for table, row in (("templates", template), ("lead_lists", lst), ("mailboxes", mailbox)):
        assert scalar(su, f"SELECT count(*) FROM public.{table} WHERE id=%s", row) == 0


def bulk(client, w: World, path: str, ids, confirm: str):
    return client.post(
        f"/api/v1/workspaces/{w.ws}{path}", json={"ids": [str(i) for i in ids], "confirm": confirm}
    )


def _lists(su, w: World, *, archived: int, active: int) -> tuple[list[uuid.UUID], list[uuid.UUID]]:
    gone, live = [], []
    su.execute("SET session_replication_role = replica")
    for i in range(archived + active):
        row = uuid.uuid4()
        is_archived = i < archived
        _insert(su, "lead_lists", id=row, workspace_id=w.ws, name=f"L{i}",
                archived_at=datetime.now(UTC) if is_archived else None)
        (gone if is_archived else live).append(row)
    su.execute("SET session_replication_role = origin")
    return gone, live


def test_bulk_purge_lists_removes_archived_ones_and_reports_the_rest(client, su) -> None:
    w = seed_world(su, running=False, enroll_first=False, members=0)
    other = seed_world(su, running=False, enroll_first=False, members=0)
    (foreign,), _ = _lists(su, other, archived=1, active=0)
    gone, live = _lists(su, w, archived=2, active=1)
    client.holder["as"] = (w, "OWNER")

    refused = bulk(client, w, "/lead-lists/bulk-purge", gone, "delete")
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "confirmation_mismatch"
    assert scalar(su, "SELECT count(*) FROM public.lead_lists WHERE workspace_id=%s", w.ws) == 3

    res = bulk(client, w, "/lead-lists/bulk-purge", [*gone, *live, foreign], "DELETE")
    assert res.status_code == 200, res.text
    body = res.json()
    assert (body["succeeded"], body["failed"]) == (2, 2)
    by_id = {r["id"]: r for r in body["results"]}
    assert by_id[str(live[0])]["code"] == "state_conflict"
    assert by_id[str(foreign)]["code"] == "not_found"
    assert scalar(su, "SELECT count(*) FROM public.lead_lists WHERE id = ANY(%s)", gone) == 0
    assert scalar(su, "SELECT count(*) FROM public.lead_lists WHERE id = ANY(%s)", [*live, foreign]) == 2


def test_bulk_erase_leads_only_touches_archived_leads(client, su) -> None:
    w = seed_world(su, running=False, enroll_first=False, members=3)
    archived, keep = w.lead_ids[:2], w.lead_ids[2]
    su.execute("UPDATE public.leads SET archived_at = now(), status = 'ARCHIVED' WHERE id = ANY(%s)", [archived])
    client.holder["as"] = (w, "OWNER")

    res = bulk(client, w, "/leads/bulk-erase", [*archived, keep], "DELETE")
    assert res.status_code == 200, res.text
    body = res.json()
    assert (body["succeeded"], body["failed"]) == (2, 1)
    assert {r["id"]: r.get("code") for r in body["results"]}[str(keep)] == "state_conflict"
    assert "lead0@target0.test" not in res.text
    assert scalar(su, "SELECT count(*) FROM public.leads WHERE id = ANY(%s) AND first_name IS NULL", archived) == 2
    assert scalar(su, "SELECT first_name FROM public.leads WHERE id=%s", keep) == "Lead2"


def test_bulk_delete_is_admin_only_and_limited_to_one_hundred(client, su) -> None:
    w = seed_world(su, running=False, enroll_first=False, members=0)
    gone, _ = _lists(su, w, archived=1, active=0)
    client.holder["as"] = (w, "MEMBER")
    assert bulk(client, w, "/lead-lists/bulk-purge", gone, "DELETE").status_code == 403
    assert bulk(client, w, "/leads/bulk-erase", gone, "DELETE").status_code == 403
    client.holder["as"] = (w, "OWNER")
    too_many = [uuid.uuid4() for _ in range(101)]
    assert bulk(client, w, "/lead-lists/bulk-purge", too_many, "DELETE").status_code == 422
    assert bulk(client, w, "/lead-lists/bulk-purge", [gone[0], gone[0]], "DELETE").status_code == 422
    assert scalar(su, "SELECT count(*) FROM public.lead_lists WHERE id=%s", gone[0]) == 1


def test_delete_an_archived_campaign_that_sent_and_its_mailbox(client, su) -> None:
    w = settled_world(su, members=1)
    client.holder["as"] = (w, "OWNER")
    res = post(client, w, f"/campaigns/{w.campaign_id}/purge", "Outbound")
    assert res.status_code == 200, res.text
    assert res.json()["operation"] == "campaign.delete"
    assert scalar(su, "SELECT count(*) FROM public.messages WHERE workspace_id=%s", w.ws) == 0
    assert scalar(su, "SELECT count(*) FROM public.leads WHERE workspace_id=%s", w.ws) == 1

    # the mailbox is still connected: refused with a client-safe message
    res = post(client, w, f"/mailboxes/{w.mailbox_id}/purge", "sender@acme.test")
    assert res.status_code == 409 and res.json()["error"]["code"] == "state_conflict"
    disconnect(su, w)
    res = post(client, w, f"/mailboxes/{w.mailbox_id}/purge", "sender@acme.test")
    assert res.status_code == 200, res.text
    assert res.json()["operation"] == "mailbox.delete"
    assert scalar(su, "SELECT count(*) FROM public.mailboxes WHERE id=%s", w.mailbox_id) == 0


def test_a_recent_send_is_refused_with_a_client_safe_error(client, su) -> None:
    from tests.test_erasure_real_db import archive, sent_world

    w = sent_world(su, members=1)
    archive(su, w)
    client.holder["as"] = (w, "OWNER")
    res = post(client, w, f"/campaigns/{w.campaign_id}/purge", "Outbound")
    assert res.status_code == 409
    err = res.json()["error"]
    assert err["code"] == "recent_sends" and "25 hours" in err["message"]
    assert "erasure:" not in res.text
    assert scalar(su, "SELECT count(*) FROM public.campaigns WHERE id=%s", w.campaign_id) == 1
