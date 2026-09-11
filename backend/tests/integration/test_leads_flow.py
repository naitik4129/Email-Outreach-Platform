from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
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
    company: str = "Acme",
    list_id: str | None = None,
) -> dict:
    resp = client.post(
        f"/api/v1/workspaces/{workspace_id}/leads",
        json={
            "email": email,
            "first_name": "Ada",
            "last_name": "Lovelace",
            "company": company,
            "title": "Founder",
            "custom_fields": {"segment": "founder"},
            "list_id": list_id,
        },
        headers=user.auth_header,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _create_list(client: TestClient, user, workspace_id: str, name: str) -> dict:
    resp = client.post(
        f"/api/v1/workspaces/{workspace_id}/lead-lists",
        json={"name": name},
        headers=user.auth_header,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


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


def test_lead_crud_search_pagination_and_archive(
    api_client: TestClient, make_test_user
) -> None:
    user = make_test_user()
    workspace = _bootstrap(api_client, user, "Leads CRUD")
    lead_list = _create_list(api_client, user, workspace["id"], "Founders")
    lead = _create_lead(
        api_client,
        user,
        workspace["id"],
        "Ada.Example+Demo@Example.com",
        list_id=lead_list["id"],
    )
    other = _create_lead(
        api_client,
        user,
        workspace["id"],
        "grace@example.com",
        company="Compiler Co",
    )

    assert lead["canonical_address"] == "ada.example+demo@example.com"

    duplicate = api_client.post(
        f"/api/v1/workspaces/{workspace['id']}/leads",
        json={"email": "ada.example+demo@example.com"},
        headers=user.auth_header,
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "duplicate_lead"

    searched = api_client.get(
        f"/api/v1/workspaces/{workspace['id']}/leads?q=compiler",
        headers=user.auth_header,
    )
    assert searched.status_code == 200
    assert [item["id"] for item in searched.json()["items"]] == [other["id"]]

    first_page = api_client.get(
        f"/api/v1/workspaces/{workspace['id']}/leads?limit=1",
        headers=user.auth_header,
    ).json()
    assert len(first_page["items"]) == 1
    assert first_page["next_cursor"]

    second_page = api_client.get(
        f"/api/v1/workspaces/{workspace['id']}/leads"
        f"?limit=1&cursor={first_page['next_cursor']}",
        headers=user.auth_header,
    ).json()
    assert len(second_page["items"]) == 1
    assert second_page["items"][0]["id"] != first_page["items"][0]["id"]

    detail = api_client.get(
        f"/api/v1/workspaces/{workspace['id']}/leads/{lead['id']}",
        headers=user.auth_header,
    )
    assert detail.status_code == 200
    assert detail.json()["lists"][0]["id"] == lead_list["id"]

    updated = api_client.patch(
        f"/api/v1/workspaces/{workspace['id']}/leads/{lead['id']}",
        json={"company": "Analytical Engines", "expected_version": lead["version"]},
        headers=user.auth_header,
    )
    assert updated.status_code == 200
    assert updated.json()["version"] == lead["version"] + 1

    stale = api_client.patch(
        f"/api/v1/workspaces/{workspace['id']}/leads/{lead['id']}",
        json={"company": "Stale", "expected_version": lead["version"]},
        headers=user.auth_header,
    )
    assert stale.status_code == 409

    archived = api_client.post(
        f"/api/v1/workspaces/{workspace['id']}/leads/{lead['id']}/archive",
        json={"expected_version": updated.json()["version"]},
        headers=user.auth_header,
    )
    assert archived.status_code == 200
    assert archived.json()["status"] == "ARCHIVED"

    active = api_client.get(
        f"/api/v1/workspaces/{workspace['id']}/leads",
        headers=user.auth_header,
    ).json()
    assert lead["id"] not in {item["id"] for item in active["items"]}

    all_leads = api_client.get(
        f"/api/v1/workspaces/{workspace['id']}/leads?status=ALL",
        headers=user.auth_header,
    ).json()
    assert lead["id"] in {item["id"] for item in all_leads["items"]}


def test_lead_list_membership_is_idempotent_and_removable(
    api_client: TestClient, make_test_user
) -> None:
    user = make_test_user()
    workspace = _bootstrap(api_client, user, "List Membership")
    lead_list = _create_list(api_client, user, workspace["id"], "Agencies")
    lead = _create_lead(api_client, user, workspace["id"], "member@example.com")

    url = f"/api/v1/workspaces/{workspace['id']}/lead-lists/{lead_list['id']}/members"
    first = api_client.post(
        url,
        json={"lead_id": lead["id"]},
        headers=user.auth_header,
    )
    second = api_client.post(
        url,
        json={"lead_id": lead["id"]},
        headers=user.auth_header,
    )
    assert first.status_code == 204
    assert second.status_code == 204

    list_detail = api_client.get(
        f"/api/v1/workspaces/{workspace['id']}/lead-lists/{lead_list['id']}",
        headers=user.auth_header,
    ).json()
    assert list_detail["member_count"] == 1

    members = api_client.get(url, headers=user.auth_header).json()
    assert [member["lead"]["id"] for member in members["items"]] == [lead["id"]]

    remove_url = f"{url}/{lead['id']}"
    assert api_client.delete(remove_url, headers=user.auth_header).status_code == 204
    assert api_client.delete(remove_url, headers=user.auth_header).status_code == 204
    assert api_client.get(url, headers=user.auth_header).json()["items"] == []


def test_lead_list_rename_archive_and_stale_version(
    api_client: TestClient, make_test_user
) -> None:
    user = make_test_user()
    workspace = _bootstrap(api_client, user, "List Edit")
    lead_list = _create_list(api_client, user, workspace["id"], "Before")

    renamed = api_client.patch(
        f"/api/v1/workspaces/{workspace['id']}/lead-lists/{lead_list['id']}",
        json={"name": "After", "expected_version": lead_list["version"]},
        headers=user.auth_header,
    )
    assert renamed.status_code == 200
    assert renamed.json()["name"] == "After"

    stale = api_client.patch(
        f"/api/v1/workspaces/{workspace['id']}/lead-lists/{lead_list['id']}",
        json={"name": "Stale", "expected_version": lead_list["version"]},
        headers=user.auth_header,
    )
    assert stale.status_code == 409

    archived = api_client.post(
        f"/api/v1/workspaces/{workspace['id']}/lead-lists/{lead_list['id']}/archive",
        json={"expected_version": renamed.json()["version"]},
        headers=user.auth_header,
    )
    assert archived.status_code == 200
    assert archived.json()["archived_at"] is not None


def test_cross_workspace_leads_lists_and_memberships_are_invisible(
    api_client: TestClient, make_test_user
) -> None:
    user_a = make_test_user()
    user_b = make_test_user()
    workspace_a = _bootstrap(api_client, user_a, "Lead Tenant A")
    workspace_b = _bootstrap(api_client, user_b, "Lead Tenant B")
    list_a = _create_list(api_client, user_a, workspace_a["id"], "A List")
    list_b = _create_list(api_client, user_b, workspace_b["id"], "B List")
    lead_a = _create_lead(api_client, user_a, workspace_a["id"], "a@example.com")
    lead_b = _create_lead(api_client, user_b, workspace_b["id"], "b@example.com")

    assert (
        api_client.get(
            f"/api/v1/workspaces/{workspace_b['id']}/leads/{lead_b['id']}",
            headers=user_a.auth_header,
        ).status_code
        == 404
    )
    assert (
        api_client.get(
            f"/api/v1/workspaces/{workspace_b['id']}/lead-lists/{list_b['id']}",
            headers=user_a.auth_header,
        ).status_code
        == 404
    )

    add_foreign_lead = api_client.post(
        f"/api/v1/workspaces/{workspace_a['id']}/lead-lists/{list_a['id']}/members",
        json={"lead_id": lead_b["id"]},
        headers=user_a.auth_header,
    )
    assert add_foreign_lead.status_code == 404

    add_to_foreign_list = api_client.post(
        f"/api/v1/workspaces/{workspace_b['id']}/lead-lists/{list_b['id']}/members",
        json={"lead_id": lead_a["id"]},
        headers=user_a.auth_header,
    )
    assert add_to_foreign_list.status_code == 404

    a_list = api_client.get(
        f"/api/v1/workspaces/{workspace_a['id']}/leads?status=ALL",
        headers=user_a.auth_header,
    ).json()
    assert {item["id"] for item in a_list["items"]} == {lead_a["id"]}


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


@pytest.mark.parametrize(
    ("role_code", "expected_status"),
    [("VIEWER", 403), ("MEMBER", 201), ("MANAGER", 201), ("ADMIN", 201)],
)
def test_contacts_manage_rbac_matrix(
    api_client: TestClient,
    make_test_user,
    db_admin: Session,
    role_code: str,
    expected_status: int,
) -> None:
    owner = make_test_user()
    actor = make_test_user()
    workspace = _bootstrap(api_client, owner, f"Contacts RBAC {role_code}")
    api_client.get("/api/v1/me", headers=actor.auth_header)
    _seed_membership(db_admin, workspace["id"], actor.user_id, role_code)
    db_admin.commit()

    resp = api_client.post(
        f"/api/v1/workspaces/{workspace['id']}/leads",
        json={"email": f"{role_code.lower()}-{uuid.uuid4().hex}@example.com"},
        headers=actor.auth_header,
    )
    assert resp.status_code == expected_status


def test_rls_and_same_workspace_fk_safety_for_leads(
    api_client: TestClient,
    make_test_user,
    raw_db: Session,
) -> None:
    user_a = make_test_user()
    user_b = make_test_user()
    workspace_a = _bootstrap(api_client, user_a, "RLS Leads A")
    workspace_b = _bootstrap(api_client, user_b, "RLS Leads B")
    list_a = _create_list(api_client, user_a, workspace_a["id"], "A RLS List")
    lead_a = _create_lead(api_client, user_a, workspace_a["id"], "rls-a@example.com")
    lead_b = _create_lead(api_client, user_b, workspace_b["id"], "rls-b@example.com")

    _set_context(raw_db, user_id=user_a.user_id, workspace_id=workspace_a["id"])
    visible = raw_db.execute(text("SELECT id FROM public.leads")).scalars().all()
    assert visible == [uuid.UUID(lead_a["id"])]

    result = raw_db.execute(
        text(
            "UPDATE public.leads SET company = 'Hijacked' "
            "WHERE id = :foreign_id"
        ),
        {"foreign_id": lead_b["id"]},
    )
    assert result.rowcount == 0

    with pytest.raises(SQLAlchemyError):
        raw_db.execute(
            text(
                "INSERT INTO public.lead_list_memberships "
                "(workspace_id, list_id, lead_id, added_by) "
                "VALUES (:workspace_id, :list_id, :lead_id, :user_id)"
            ),
            {
                "workspace_id": workspace_a["id"],
                "list_id": list_a["id"],
                "lead_id": lead_b["id"],
                "user_id": user_a.user_id,
            },
        )
    raw_db.rollback()
