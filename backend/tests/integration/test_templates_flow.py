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


def _create_lead(
    client: TestClient,
    user,
    workspace_id: str,
    email: str,
    *,
    first_name: str = "Grace",
    last_name: str = "Hopper",
    company: str = "Navy Tech",
    title: str = "Admiral",
) -> dict:
    resp = client.post(
        f"/api/v1/workspaces/{workspace_id}/leads",
        json={
            "email": email,
            "first_name": first_name,
            "last_name": last_name,
            "company": company,
            "title": title,
            "custom_fields": {"priority": "Tier 1"},
        },
        headers=user.auth_header,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _create_template(
    client: TestClient,
    user,
    workspace_id: str,
    name: str = "Intro Cold Outreach",
    subject: str = "Quick question for {{first_name}} at {{company}}",
    body_html: str = "<p>Hi {{first_name}}, loved your work at {{company}}.</p>",
) -> dict:
    resp = client.post(
        f"/api/v1/workspaces/{workspace_id}/templates",
        json={
            "name": name,
            "subject": subject,
            "body_html": body_html,
            "mode": "STANDARD",
        },
        headers=user.auth_header,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _seed_membership(
    db_admin: Session, workspace_id: str, user_id: str, role_code: str
) -> None:
    db_admin.execute(
        text(
            "INSERT INTO workspace_memberships (workspace_id, user_id, role_code) "
            "VALUES (:workspace_id, :user_id, :role_code)"
        ),
        {
            "workspace_id": workspace_id,
            "user_id": user_id,
            "role_code": role_code,
        },
    )


def _set_context(
    session: Session, *, user_id: str | None, workspace_id: str | None
) -> None:
    if user_id is not None:
        session.execute(
            text("select set_config('app.user_id', :v, true)"),
            {"v": user_id},
        )
    if workspace_id is not None:
        session.execute(
            text("select set_config('app.workspace_id', :v, true)"),
            {"v": workspace_id},
        )


def test_template_crud_lifecycle_and_versioning(
    api_client: TestClient, make_test_user
) -> None:
    user = make_test_user()
    ws = _bootstrap(api_client, user, "Templates WS 1")
    ws_id = ws["id"]

    # 1. Create template
    created = _create_template(
        api_client,
        user,
        ws_id,
        name="Welcome Series Step 1",
        subject="Hello {{first_name|there}} from {{company}}",
        body_html="<p>Hi {{first_name}}, welcome to {{company}}!</p>",
    )
    assert created["name"] == "Welcome Series Step 1"
    assert created["subject"] == "Hello {{first_name|there}} from {{company}}"
    assert created["current_revision"] == 1
    assert created["mode"] == "STANDARD"
    assert created["archived_at"] is None
    assert created["version"] >= 2  # created then pointer touched
    tmpl_id = created["id"]

    # 2. Get template
    fetched = api_client.get(
        f"/api/v1/workspaces/{ws_id}/templates/{tmpl_id}",
        headers=user.auth_header,
    ).json()
    assert fetched["id"] == tmpl_id
    assert fetched["name"] == created["name"]
    assert fetched["content_digest"]

    # 3. List templates
    listing = api_client.get(
        f"/api/v1/workspaces/{ws_id}/templates",
        headers=user.auth_header,
    ).json()
    assert len(listing["items"]) == 1
    assert listing["items"][0]["id"] == tmpl_id

    # 4. Search templates
    search_match = api_client.get(
        f"/api/v1/workspaces/{ws_id}/templates?q=Welcome",
        headers=user.auth_header,
    ).json()
    assert len(search_match["items"]) == 1

    search_miss = api_client.get(
        f"/api/v1/workspaces/{ws_id}/templates?q=NonexistentQuery",
        headers=user.auth_header,
    ).json()
    assert len(search_miss["items"]) == 0

    # 5. Update content -> creates revision 2
    updated = api_client.patch(
        f"/api/v1/workspaces/{ws_id}/templates/{tmpl_id}",
        json={
            "expected_version": created["version"],
            "subject": "Updated: {{first_name}} at {{company}}",
            "body_html": "<p>Updated content for {{first_name}}.</p>",
        },
        headers=user.auth_header,
    )
    assert updated.status_code == 200, updated.text
    upd_data = updated.json()
    assert upd_data["current_revision"] == 2
    assert upd_data["version"] > created["version"]

    # 6. Check version history
    versions = api_client.get(
        f"/api/v1/workspaces/{ws_id}/templates/{tmpl_id}/versions",
        headers=user.auth_header,
    ).json()
    assert len(versions) == 2
    assert versions[0]["revision"] == 2
    assert versions[1]["revision"] == 1


def test_optimistic_concurrency_conflict(
    api_client: TestClient, make_test_user
) -> None:
    user = make_test_user()
    ws = _bootstrap(api_client, user, "Templates Concurrency WS")
    ws_id = ws["id"]

    tmpl = _create_template(api_client, user, ws_id, name="Concurrent Edit Template")
    tmpl_id = tmpl["id"]
    version_at_load = tmpl["version"]

    # Client A updates
    res_a = api_client.patch(
        f"/api/v1/workspaces/{ws_id}/templates/{tmpl_id}",
        json={
            "expected_version": version_at_load,
            "name": "Updated by Client A",
        },
        headers=user.auth_header,
    )
    assert res_a.status_code == 200

    # Client B attempts update with stale expected_version
    res_b = api_client.patch(
        f"/api/v1/workspaces/{ws_id}/templates/{tmpl_id}",
        json={
            "expected_version": version_at_load,
            "name": "Overwritten by Client B",
        },
        headers=user.auth_header,
    )
    assert res_b.status_code == 409
    err_data = res_b.json()
    err_msg = err_data.get("error", {}).get("message", "") or err_data.get("message", "")
    assert "modified by another user" in err_msg


def test_duplicate_template(api_client: TestClient, make_test_user) -> None:
    user = make_test_user()
    ws = _bootstrap(api_client, user, "Templates Duplicate WS")
    ws_id = ws["id"]

    orig = _create_template(api_client, user, ws_id, name="Original Template")
    dup = api_client.post(
        f"/api/v1/workspaces/{ws_id}/templates/{orig['id']}/duplicate",
        json={},
        headers=user.auth_header,
    )
    assert dup.status_code == 201
    dup_data = dup.json()
    assert dup_data["id"] != orig["id"]
    assert dup_data["name"] == "Original Template (Copy)"
    assert dup_data["current_revision"] == 1
    assert dup_data["subject"] == orig["subject"]


def test_archive_template(api_client: TestClient, make_test_user) -> None:
    user = make_test_user()
    ws = _bootstrap(api_client, user, "Templates Archive WS")
    ws_id = ws["id"]

    tmpl = _create_template(api_client, user, ws_id, name="To Be Archived")
    tmpl_id = tmpl["id"]

    # Archive
    arch = api_client.post(
        f"/api/v1/workspaces/{ws_id}/templates/{tmpl_id}/archive",
        json={"expected_version": tmpl["version"]},
        headers=user.auth_header,
    )
    assert arch.status_code == 200
    assert arch.json()["archived_at"] is not None

    # Default listing (ACTIVE) excludes it
    active_list = api_client.get(
        f"/api/v1/workspaces/{ws_id}/templates?status=ACTIVE",
        headers=user.auth_header,
    ).json()
    assert not any(t["id"] == tmpl_id for t in active_list["items"])

    # ARCHIVED listing includes it
    arch_list = api_client.get(
        f"/api/v1/workspaces/{ws_id}/templates?status=ARCHIVED",
        headers=user.auth_header,
    ).json()
    assert any(t["id"] == tmpl_id for t in arch_list["items"])

    # Attempting to edit archived template raises 409
    edit_arch = api_client.patch(
        f"/api/v1/workspaces/{ws_id}/templates/{tmpl_id}",
        json={"expected_version": arch.json()["version"], "name": "New Name"},
        headers=user.auth_header,
    )
    assert edit_arch.status_code == 409


def test_preview_template(api_client: TestClient, make_test_user) -> None:
    user = make_test_user()
    ws = _bootstrap(api_client, user, "Templates Preview WS")
    ws_id = ws["id"]

    # Preview with sample data
    prev1 = api_client.post(
        f"/api/v1/workspaces/{ws_id}/templates/preview",
        json={
            "subject": "Quick chat {{first_name}}",
            "body_html": "<p>Hi {{first_name}}, how is {{company}}?</p>",
            "sample_data": {"first_name": "Samantha", "company": "Solaris"},
        },
        headers=user.auth_header,
    )
    assert prev1.status_code == 200
    p1 = prev1.json()
    assert p1["subject"] == "Quick chat Samantha"
    assert "<p>Hi Samantha, how is Solaris?</p>" in p1["body_html"]

    # Preview with actual workspace lead
    lead = _create_lead(
        api_client,
        user,
        ws_id,
        "lead.preview@example.com",
        first_name="Linus",
        company="Linux Foundation",
    )
    prev2 = api_client.post(
        f"/api/v1/workspaces/{ws_id}/templates/preview",
        json={
            "subject": "Note for {{first_name}}",
            "body_html": "<p>Greetings {{first_name}} at {{company}}.</p>",
            "lead_id": lead["id"],
        },
        headers=user.auth_header,
    )
    assert prev2.status_code == 200
    p2 = prev2.json()
    assert p2["subject"] == "Note for Linus"
    assert "Greetings Linus at Linux Foundation." in p2["body_html"]


def test_cross_workspace_template_isolation(
    api_client: TestClient, make_test_user
) -> None:
    user_a = make_test_user()
    user_b = make_test_user()
    ws_a = _bootstrap(api_client, user_a, "Workspace Isolation A")
    ws_b = _bootstrap(api_client, user_b, "Workspace Isolation B")

    tmpl_a = _create_template(api_client, user_a, ws_a["id"], name="Template in A")
    tmpl_b = _create_template(api_client, user_b, ws_b["id"], name="Template in B")

    lead_b = _create_lead(
        api_client, user_b, ws_b["id"], "lead_b@tenant.example.com"
    )

    # User A cannot get Template B
    assert (
        api_client.get(
            f"/api/v1/workspaces/{ws_a['id']}/templates/{tmpl_b['id']}",
            headers=user_a.auth_header,
        ).status_code
        == 404
    )

    # User A cannot update Template B
    assert (
        api_client.patch(
            f"/api/v1/workspaces/{ws_a['id']}/templates/{tmpl_b['id']}",
            json={"expected_version": 1, "name": "Hijacked"},
            headers=user_a.auth_header,
        ).status_code
        == 404
    )

    # User A cannot duplicate Template B
    assert (
        api_client.post(
            f"/api/v1/workspaces/{ws_a['id']}/templates/{tmpl_b['id']}/duplicate",
            json={},
            headers=user_a.auth_header,
        ).status_code
        == 404
    )

    # User A cannot archive Template B
    assert (
        api_client.post(
            f"/api/v1/workspaces/{ws_a['id']}/templates/{tmpl_b['id']}/archive",
            json={"expected_version": 1},
            headers=user_a.auth_header,
        ).status_code
        == 404
    )

    # User A in Workspace A preview cannot use Workspace B lead
    prev_foreign = api_client.post(
        f"/api/v1/workspaces/{ws_a['id']}/templates/preview",
        json={
            "subject": "Test",
            "body_html": "<p>Test</p>",
            "lead_id": lead_b["id"],
        },
        headers=user_a.auth_header,
    )
    assert prev_foreign.status_code == 404

    # Workspace A listing only shows Template A
    list_a = api_client.get(
        f"/api/v1/workspaces/{ws_a['id']}/templates",
        headers=user_a.auth_header,
    ).json()
    assert {t["id"] for t in list_a["items"]} == {tmpl_a["id"]}


@pytest.mark.parametrize(
    ("role_code", "can_manage"),
    [("VIEWER", False), ("MEMBER", True), ("MANAGER", True), ("ADMIN", True)],
)
def test_templates_rbac_matrix(
    api_client: TestClient,
    make_test_user,
    db_admin: Session,
    role_code: str,
    can_manage: bool,
) -> None:
    owner = make_test_user()
    actor = make_test_user()
    ws = _bootstrap(api_client, owner, f"Templates RBAC {role_code}")
    ws_id = ws["id"]

    api_client.get("/api/v1/me", headers=actor.auth_header)
    _seed_membership(db_admin, ws_id, actor.user_id, role_code)
    db_admin.commit()

    tmpl = _create_template(api_client, owner, ws_id, name="RBAC Template")

    # Read/Preview check (all roles have product.read)
    get_res = api_client.get(
        f"/api/v1/workspaces/{ws_id}/templates/{tmpl['id']}",
        headers=actor.auth_header,
    )
    assert get_res.status_code == 200

    prev_res = api_client.post(
        f"/api/v1/workspaces/{ws_id}/templates/preview",
        json={"subject": "Hi", "body_html": "<p>Hi</p>"},
        headers=actor.auth_header,
    )
    assert prev_res.status_code == 200

    # Create mutation check
    create_res = api_client.post(
        f"/api/v1/workspaces/{ws_id}/templates",
        json={"name": "Role Test", "subject": "Hi", "body_html": "<p>Hi</p>"},
        headers=actor.auth_header,
    )
    expected_status = 201 if can_manage else 403
    assert create_res.status_code == expected_status

    # Archive mutation check
    arch_res = api_client.post(
        f"/api/v1/workspaces/{ws_id}/templates/{tmpl['id']}/archive",
        json={"expected_version": tmpl["version"]},
        headers=actor.auth_header,
    )
    expected_arch = 200 if can_manage else 403
    assert arch_res.status_code == expected_arch


def test_rls_direct_for_templates(
    api_client: TestClient,
    make_test_user,
    raw_db: Session,
) -> None:
    user_a = make_test_user()
    user_b = make_test_user()
    ws_a = _bootstrap(api_client, user_a, "RLS Templates A")
    ws_b = _bootstrap(api_client, user_b, "RLS Templates B")

    tmpl_a = _create_template(api_client, user_a, ws_a["id"], "Template A RLS")
    tmpl_b = _create_template(api_client, user_b, ws_b["id"], "Template B RLS")

    # When connected as app_api under Workspace A context:
    _set_context(raw_db, user_id=user_a.user_id, workspace_id=ws_a["id"])
    visible = (
        raw_db.execute(text("SELECT id FROM public.templates")).scalars().all()
    )
    assert visible == [uuid.UUID(tmpl_a["id"])]

    # Cannot update Workspace B template directly via SQL
    res = raw_db.execute(
        text(
            "UPDATE public.templates SET name = 'Hijacked' "
            "WHERE id = :foreign_id"
        ),
        {"foreign_id": tmpl_b["id"]},
    )
    assert res.rowcount == 0
