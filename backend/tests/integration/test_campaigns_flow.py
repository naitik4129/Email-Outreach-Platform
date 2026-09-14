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
    assert resp.status_code == 201, resp.text
    return resp.json()


def _create_lead(client: TestClient, user, workspace_id: str, email: str) -> dict:
    resp = client.post(
        f"/api/v1/workspaces/{workspace_id}/leads",
        json={"email": email, "first_name": "Ada", "last_name": "Lovelace"},
        headers=user.auth_header,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _create_mailbox(db_admin: Session, workspace_id: str, address: str) -> str:
    """Seed a healthy mailbox row directly -- campaign tests only need an
    existing, assignable mailbox, not to exercise the OAuth/SMTP connect
    flow itself (covered by tests/integration/test_mailboxes_flow.py).

    mailboxes.connected_generation carries a composite FK into
    mailbox_connections(workspace_id, mailbox_id, generation), so a mailbox
    can never be inserted CONNECTED in one statement -- mirrors the real
    insert_mailbox() -> insert_mailbox_connection() ->
    update_mailbox_connection_state() sequence.
    """
    mailbox_id = str(uuid.uuid4())
    db_admin.execute(
        text(
            """
            INSERT INTO mailboxes
                (id, workspace_id, provider, provider_account_id, original_address,
                 connection_state, health_state, policy_state)
            VALUES
                (:id, :workspace_id, 'SMTP', :account_id, :address,
                 'CONNECTING', 'UNKNOWN', 'ENABLED')
            """
        ),
        {
            "id": mailbox_id,
            "workspace_id": workspace_id,
            "account_id": address,
            "address": address,
        },
    )
    db_admin.execute(
        text(
            """
            INSERT INTO mailbox_connections
                (id, workspace_id, mailbox_id, generation, auth_mechanism,
                 credential_ciphertext, encryption_key_id, nonce)
            VALUES
                (:id, :workspace_id, :mailbox_id, 1, 'SMTP_PASSWORD',
                 :ciphertext, 'test-fixture-key', :nonce)
            """
        ),
        {
            "id": str(uuid.uuid4()),
            "workspace_id": workspace_id,
            "mailbox_id": mailbox_id,
            # Not a real credential -- this fixture never authenticates
            # against a provider, it only needs to satisfy
            # mailbox_connections_envelope_check's non-null/length bounds.
            "ciphertext": b"test-fixture-ciphertext",
            "nonce": b"test-fixture-nonce12",
        },
    )
    db_admin.execute(
        text(
            """
            UPDATE mailboxes
            SET connection_state = 'CONNECTED', health_state = 'HEALTHY',
                connected_generation = 1
            WHERE id = :id AND workspace_id = :workspace_id
            """
        ),
        {"id": mailbox_id, "workspace_id": workspace_id},
    )
    db_admin.commit()
    return mailbox_id


def _create_campaign(client: TestClient, user, workspace_id: str, name: str) -> dict:
    resp = client.post(
        f"/api/v1/workspaces/{workspace_id}/campaigns",
        json={"name": name},
        headers={**user.auth_header, "Idempotency-Key": uuid.uuid4().hex},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _add_email_step(
    client: TestClient,
    user,
    workspace_id: str,
    campaign_id: str,
    position: int,
    subject: str = "Hello {{first_name}}",
) -> dict:
    resp = client.post(
        f"/api/v1/workspaces/{workspace_id}/campaigns/{campaign_id}/sequence/steps",
        json={
            "kind": "EMAIL",
            "position": position,
            "email_subject": subject,
            "email_body_html": "<p>Hi {{first_name}}</p>",
        },
        headers=user.auth_header,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _add_wait_step(
    client: TestClient, user, workspace_id: str, campaign_id: str, position: int
) -> dict:
    resp = client.post(
        f"/api/v1/workspaces/{workspace_id}/campaigns/{campaign_id}/sequence/steps",
        json={"kind": "WAIT", "position": position, "wait_duration_minutes": 1440},
        headers=user.auth_header,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _configure_settings(
    client: TestClient, user, workspace_id: str, campaign_id: str
) -> dict:
    resp = client.post(
        f"/api/v1/workspaces/{workspace_id}/campaigns/{campaign_id}/settings",
        json={
            "timezone": "America/New_York",
            "weekdays": [1, 2, 3, 4, 5],
            "window_start_local": "09:00:00",
            "window_end_local": "17:00:00",
            "daily_limit": 50,
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
        {"workspace_id": workspace_id, "user_id": user_id, "role_code": role_code},
    )


def _set_context(
    session: Session, *, user_id: str | None, workspace_id: str | None
) -> None:
    if user_id is not None:
        session.execute(
            text("select set_config('app.user_id', :v, true)"), {"v": user_id}
        )
    if workspace_id is not None:
        session.execute(
            text("select set_config('app.workspace_id', :v, true)"), {"v": workspace_id}
        )


# ---------------------------------------------------------------------------
# Campaign CRUD lifecycle
# ---------------------------------------------------------------------------


def test_campaign_crud_lifecycle(api_client: TestClient, make_test_user) -> None:
    user = make_test_user()
    ws = _bootstrap(api_client, user, "Campaigns CRUD WS")
    ws_id = ws["id"]

    created = _create_campaign(api_client, user, ws_id, "Q1 Outbound")
    assert created["status"] == "DRAFT"
    assert created["draft_sequence_id"] is None
    assert created["draft_audience_id"] is None
    campaign_id = created["id"]

    fetched = api_client.get(
        f"/api/v1/workspaces/{ws_id}/campaigns/{campaign_id}", headers=user.auth_header
    ).json()
    assert fetched["id"] == campaign_id

    listing = api_client.get(
        f"/api/v1/workspaces/{ws_id}/campaigns", headers=user.auth_header
    ).json()
    assert any(c["id"] == campaign_id for c in listing["items"])

    updated = api_client.patch(
        f"/api/v1/workspaces/{ws_id}/campaigns/{campaign_id}",
        json={"expected_version": created["version"], "name": "Q1 Outbound Renamed"},
        headers=user.auth_header,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["name"] == "Q1 Outbound Renamed"

    archived = api_client.post(
        f"/api/v1/workspaces/{ws_id}/campaigns/{campaign_id}/archive",
        json={"expected_version": updated.json()["version"]},
        headers=user.auth_header,
    )
    assert archived.status_code == 200, archived.text
    assert archived.json()["status"] == "ARCHIVED"

    # A DRAFT-only edit is rejected once archived.
    blocked = api_client.patch(
        f"/api/v1/workspaces/{ws_id}/campaigns/{campaign_id}",
        json={"expected_version": archived.json()["version"], "name": "Nope"},
        headers=user.auth_header,
    )
    assert blocked.status_code == 409


def test_campaign_blank_name_rejected(api_client: TestClient, make_test_user) -> None:
    user = make_test_user()
    ws = _bootstrap(api_client, user, "Campaigns Blank Name WS")
    resp = api_client.post(
        f"/api/v1/workspaces/{ws['id']}/campaigns",
        json={"name": "   "},
        headers={**user.auth_header, "Idempotency-Key": uuid.uuid4().hex},
    )
    assert resp.status_code == 422


def test_campaign_oversized_name_rejected(
    api_client: TestClient, make_test_user
) -> None:
    user = make_test_user()
    ws = _bootstrap(api_client, user, "Campaigns Oversized Name WS")
    resp = api_client.post(
        f"/api/v1/workspaces/{ws['id']}/campaigns",
        json={"name": "x" * 500},
        headers={**user.auth_header, "Idempotency-Key": uuid.uuid4().hex},
    )
    assert resp.status_code == 422


def test_campaign_optimistic_concurrency_conflict(
    api_client: TestClient, make_test_user
) -> None:
    user = make_test_user()
    ws = _bootstrap(api_client, user, "Campaigns Concurrency WS")
    campaign = _create_campaign(api_client, user, ws["id"], "Concurrent Edit")

    res_a = api_client.patch(
        f"/api/v1/workspaces/{ws['id']}/campaigns/{campaign['id']}",
        json={"expected_version": campaign["version"], "name": "Edited by A"},
        headers=user.auth_header,
    )
    assert res_a.status_code == 200

    res_b = api_client.patch(
        f"/api/v1/workspaces/{ws['id']}/campaigns/{campaign['id']}",
        json={"expected_version": campaign["version"], "name": "Edited by B"},
        headers=user.auth_header,
    )
    assert res_b.status_code == 409


def test_campaign_foreign_id_returns_404(
    api_client: TestClient, make_test_user
) -> None:
    user = make_test_user()
    ws = _bootstrap(api_client, user, "Campaigns Foreign ID WS")
    resp = api_client.get(
        f"/api/v1/workspaces/{ws['id']}/campaigns/{uuid.uuid4()}",
        headers=user.auth_header,
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Sequence
# ---------------------------------------------------------------------------


def test_sequence_step_crud_and_reorder(api_client: TestClient, make_test_user) -> None:
    user = make_test_user()
    ws = _bootstrap(api_client, user, "Sequence WS")
    campaign = _create_campaign(api_client, user, ws["id"], "Sequence Campaign")
    ws_id, cid = ws["id"], campaign["id"]

    email1 = _add_email_step(api_client, user, ws_id, cid, position=1, subject="Step 1")
    _add_wait_step(api_client, user, ws_id, cid, position=2)
    _add_email_step(api_client, user, ws_id, cid, position=3, subject="Step 2")

    sequence = api_client.get(
        f"/api/v1/workspaces/{ws_id}/campaigns/{cid}/sequence", headers=user.auth_header
    ).json()
    assert [s["kind"] for s in sequence["steps"]] == ["EMAIL", "WAIT", "EMAIL"]
    assert [s["position"] for s in sequence["steps"]] == [1, 2, 3]

    # Insert a WAIT step in the middle (position 2) -- existing steps shift.
    _add_wait_step(api_client, user, ws_id, cid, position=2)
    sequence = api_client.get(
        f"/api/v1/workspaces/{ws_id}/campaigns/{cid}/sequence", headers=user.auth_header
    ).json()
    assert [s["kind"] for s in sequence["steps"]] == ["EMAIL", "WAIT", "WAIT", "EMAIL"]
    assert [s["position"] for s in sequence["steps"]] == [1, 2, 3, 4]

    # Update EMAIL step content.
    updated = api_client.patch(
        f"/api/v1/workspaces/{ws_id}/campaigns/{cid}/sequence/steps/{email1['id']}",
        json={
            "expected_version": email1["version"],
            "email_subject": "Updated subject",
        },
        headers=user.auth_header,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["email_subject"] == "Updated subject"

    # Delete the second WAIT step, positions renumber to stay contiguous.
    steps_now = api_client.get(
        f"/api/v1/workspaces/{ws_id}/campaigns/{cid}/sequence", headers=user.auth_header
    ).json()["steps"]
    second_wait_id = steps_now[2]["id"]
    delete_resp = api_client.delete(
        f"/api/v1/workspaces/{ws_id}/campaigns/{cid}/sequence/steps/{second_wait_id}",
        headers=user.auth_header,
    )
    assert delete_resp.status_code == 204

    sequence = api_client.get(
        f"/api/v1/workspaces/{ws_id}/campaigns/{cid}/sequence", headers=user.auth_header
    ).json()
    assert [s["kind"] for s in sequence["steps"]] == ["EMAIL", "WAIT", "EMAIL"]
    assert [s["position"] for s in sequence["steps"]] == [1, 2, 3]

    # Explicit reorder: swap the two EMAIL steps to the ends is already true;
    # exercise the reorder endpoint by reversing the whole sequence.
    ids_in_order = [s["id"] for s in sequence["steps"]]
    reordered = api_client.post(
        f"/api/v1/workspaces/{ws_id}/campaigns/{cid}/sequence/steps/reorder",
        json={
            "steps": [
                {"step_id": ids_in_order[0], "position": 3},
                {"step_id": ids_in_order[1], "position": 2},
                {"step_id": ids_in_order[2], "position": 1},
            ]
        },
        headers=user.auth_header,
    )
    assert reordered.status_code == 200, reordered.text
    assert [s["id"] for s in reordered.json()["steps"]] == list(reversed(ids_in_order))


def test_sequence_wait_step_rejects_non_positive_duration(
    api_client: TestClient, make_test_user
) -> None:
    user = make_test_user()
    ws = _bootstrap(api_client, user, "Sequence Invalid Wait WS")
    campaign = _create_campaign(api_client, user, ws["id"], "Invalid Wait Campaign")
    resp = api_client.post(
        f"/api/v1/workspaces/{ws['id']}/campaigns/{campaign['id']}/sequence/steps",
        json={"kind": "WAIT", "position": 1, "wait_duration_minutes": 0},
        headers=user.auth_header,
    )
    assert resp.status_code == 422


def test_sequence_email_step_requires_content_or_template(
    api_client: TestClient, make_test_user
) -> None:
    user = make_test_user()
    ws = _bootstrap(api_client, user, "Sequence Missing Content WS")
    campaign = _create_campaign(api_client, user, ws["id"], "Missing Content Campaign")
    resp = api_client.post(
        f"/api/v1/workspaces/{ws['id']}/campaigns/{campaign['id']}/sequence/steps",
        json={"kind": "EMAIL", "position": 1},
        headers=user.auth_header,
    )
    assert resp.status_code == 422


def test_sequence_step_malformed_template_variable_rejected(
    api_client: TestClient, make_test_user
) -> None:
    user = make_test_user()
    ws = _bootstrap(api_client, user, "Sequence Bad Variable WS")
    campaign = _create_campaign(api_client, user, ws["id"], "Bad Variable Campaign")
    resp = api_client.post(
        f"/api/v1/workspaces/{ws['id']}/campaigns/{campaign['id']}/sequence/steps",
        json={
            "kind": "EMAIL",
            "position": 1,
            "email_subject": "Hi {{unknown_field}}",
            "email_body_html": "<p>Body</p>",
        },
        headers=user.auth_header,
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Mailbox assignment
# ---------------------------------------------------------------------------


def test_mailbox_assignment_crud_and_reorder(
    api_client: TestClient, make_test_user, db_admin: Session
) -> None:
    user = make_test_user()
    ws = _bootstrap(api_client, user, "Mailbox Assignment WS")
    campaign = _create_campaign(api_client, user, ws["id"], "Mailbox Campaign")
    ws_id, cid = ws["id"], campaign["id"]

    mbx1 = _create_mailbox(db_admin, ws_id, "sender1@example.com")
    mbx2 = _create_mailbox(db_admin, ws_id, "sender2@example.com")

    assign1 = api_client.post(
        f"/api/v1/workspaces/{ws_id}/campaigns/{cid}/mailboxes",
        json={"mailbox_id": mbx1},
        headers=user.auth_header,
    )
    assert assign1.status_code == 200, assign1.text
    assign2 = api_client.post(
        f"/api/v1/workspaces/{ws_id}/campaigns/{cid}/mailboxes",
        json={"mailbox_id": mbx2},
        headers=user.auth_header,
    )
    assert assign2.status_code == 200
    assert [m["mailbox_id"] for m in assign2.json()] == [mbx1, mbx2]

    reordered = api_client.post(
        f"/api/v1/workspaces/{ws_id}/campaigns/{cid}/mailboxes/reorder",
        json={"mailbox_ids": [mbx2, mbx1]},
        headers=user.auth_header,
    )
    assert reordered.status_code == 200, reordered.text
    assert [m["mailbox_id"] for m in reordered.json()] == [mbx2, mbx1]

    unassigned = api_client.delete(
        f"/api/v1/workspaces/{ws_id}/campaigns/{cid}/mailboxes/{mbx1}",
        headers=user.auth_header,
    )
    assert unassigned.status_code == 200
    assert [m["mailbox_id"] for m in unassigned.json()] == [mbx2]


def test_mailbox_assignment_rejects_foreign_mailbox(
    api_client: TestClient, make_test_user, db_admin: Session
) -> None:
    user_a = make_test_user()
    user_b = make_test_user()
    ws_a = _bootstrap(api_client, user_a, "Mailbox Foreign A")
    ws_b = _bootstrap(api_client, user_b, "Mailbox Foreign B")
    campaign_a = _create_campaign(api_client, user_a, ws_a["id"], "Campaign A")
    mailbox_b = _create_mailbox(db_admin, ws_b["id"], "foreign@example.com")

    resp = api_client.post(
        f"/api/v1/workspaces/{ws_a['id']}/campaigns/{campaign_a['id']}/mailboxes",
        json={"mailbox_id": mailbox_b},
        headers=user_a.auth_header,
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Settings / schedule
# ---------------------------------------------------------------------------


def test_settings_versioning_is_append_only(
    api_client: TestClient, make_test_user
) -> None:
    user = make_test_user()
    ws = _bootstrap(api_client, user, "Settings Append Only WS")
    campaign = _create_campaign(api_client, user, ws["id"], "Settings Campaign")
    ws_id, cid = ws["id"], campaign["id"]

    v1 = _configure_settings(api_client, user, ws_id, cid)
    assert v1["revision"] == 1

    v2 = api_client.post(
        f"/api/v1/workspaces/{ws_id}/campaigns/{cid}/settings",
        json={
            "timezone": "Asia/Kolkata",
            "weekdays": [1, 2, 3, 4, 5, 6],
            "window_start_local": "08:00:00",
            "window_end_local": "12:00:00",
            "daily_limit": 25,
        },
        headers=user.auth_header,
    )
    assert v2.status_code == 201
    assert v2.json()["revision"] == 2

    history = api_client.get(
        f"/api/v1/workspaces/{ws_id}/campaigns/{cid}/settings", headers=user.auth_header
    ).json()
    assert [h["revision"] for h in history] == [2, 1]

    current = api_client.get(
        f"/api/v1/workspaces/{ws_id}/campaigns/{cid}/review", headers=user.auth_header
    ).json()["settings"]
    assert current["revision"] == 2
    assert current["timezone"] == "Asia/Kolkata"


@pytest.mark.parametrize(
    ("timezone", "start", "end", "status_code"),
    [
        ("America/New_York", "09:00:00", "17:00:00", 201),
        ("Not/AZone", "09:00:00", "17:00:00", 422),
        ("America/New_York", "17:00:00", "09:00:00", 422),
    ],
)
def test_settings_timezone_and_window_validation(
    api_client: TestClient,
    make_test_user,
    timezone: str,
    start: str,
    end: str,
    status_code: int,
) -> None:
    user = make_test_user()
    ws = _bootstrap(api_client, user, "Settings Validation WS")
    campaign = _create_campaign(api_client, user, ws["id"], "Validation Campaign")
    resp = api_client.post(
        f"/api/v1/workspaces/{ws['id']}/campaigns/{campaign['id']}/settings",
        json={
            "timezone": timezone,
            "weekdays": [1, 2, 3],
            "window_start_local": start,
            "window_end_local": end,
            "daily_limit": 10,
        },
        headers=user.auth_header,
    )
    assert resp.status_code == status_code


# ---------------------------------------------------------------------------
# Preflight / Review progression
# ---------------------------------------------------------------------------


def test_preflight_progresses_from_empty_to_blocked_reasons(
    api_client: TestClient, make_test_user, db_admin: Session
) -> None:
    user = make_test_user()
    ws = _bootstrap(api_client, user, "Preflight Progression WS")
    campaign = _create_campaign(api_client, user, ws["id"], "Preflight Campaign")
    ws_id, cid = ws["id"], campaign["id"]

    empty = api_client.get(
        f"/api/v1/workspaces/{ws_id}/campaigns/{cid}/preflight",
        headers=user.auth_header,
    ).json()
    assert empty["ready"] is False
    error_codes = {e["code"] for e in empty["errors"]}
    assert "sequence_empty" in error_codes
    assert "no_mailboxes_assigned" in error_codes
    assert "audience_not_selected" in error_codes
    assert "settings_missing" in error_codes

    _add_email_step(api_client, user, ws_id, cid, position=1)
    mbx = _create_mailbox(db_admin, ws_id, "sender@example.com")
    api_client.post(
        f"/api/v1/workspaces/{ws_id}/campaigns/{cid}/mailboxes",
        json={"mailbox_id": mbx},
        headers=user.auth_header,
    )
    _configure_settings(api_client, user, ws_id, cid)

    partial = api_client.get(
        f"/api/v1/workspaces/{ws_id}/campaigns/{cid}/preflight",
        headers=user.auth_header,
    ).json()
    remaining_codes = {e["code"] for e in partial["errors"]}
    assert remaining_codes == {"audience_not_selected"}
    assert partial["ready"] is False


def test_review_aggregates_all_sections(
    api_client: TestClient, make_test_user, db_admin: Session
) -> None:
    user = make_test_user()
    ws = _bootstrap(api_client, user, "Review Aggregate WS")
    campaign = _create_campaign(api_client, user, ws["id"], "Review Campaign")
    ws_id, cid = ws["id"], campaign["id"]

    _add_email_step(api_client, user, ws_id, cid, position=1)
    mbx = _create_mailbox(db_admin, ws_id, "review-sender@example.com")
    api_client.post(
        f"/api/v1/workspaces/{ws_id}/campaigns/{cid}/mailboxes",
        json={"mailbox_id": mbx},
        headers=user.auth_header,
    )
    _configure_settings(api_client, user, ws_id, cid)

    review = api_client.get(
        f"/api/v1/workspaces/{ws_id}/campaigns/{cid}/review", headers=user.auth_header
    )
    assert review.status_code == 200, review.text
    body = review.json()
    assert body["campaign"]["id"] == cid
    assert len(body["sequence"]["steps"]) == 1
    assert len(body["mailboxes"]) == 1
    assert body["settings"]["revision"] == 1
    assert body["preflight"]["ready"] is False


# ---------------------------------------------------------------------------
# Duplicate
# ---------------------------------------------------------------------------


def test_duplicate_campaign_copies_sequence_mailboxes_settings_not_audience(
    api_client: TestClient, make_test_user, db_admin: Session
) -> None:
    user = make_test_user()
    ws = _bootstrap(api_client, user, "Duplicate WS")
    campaign = _create_campaign(api_client, user, ws["id"], "Original Campaign")
    ws_id, cid = ws["id"], campaign["id"]

    _add_email_step(
        api_client, user, ws_id, cid, position=1, subject="Original subject"
    )
    mbx = _create_mailbox(db_admin, ws_id, "dup-sender@example.com")
    api_client.post(
        f"/api/v1/workspaces/{ws_id}/campaigns/{cid}/mailboxes",
        json={"mailbox_id": mbx},
        headers=user.auth_header,
    )
    _configure_settings(api_client, user, ws_id, cid)

    dup = api_client.post(
        f"/api/v1/workspaces/{ws_id}/campaigns/{cid}/duplicate",
        json={},
        headers=user.auth_header,
    )
    assert dup.status_code == 201, dup.text
    dup_id = dup.json()["id"]
    assert dup_id != cid
    assert dup.json()["draft_audience_id"] is None

    dup_sequence = api_client.get(
        f"/api/v1/workspaces/{ws_id}/campaigns/{dup_id}/sequence",
        headers=user.auth_header,
    ).json()
    assert len(dup_sequence["steps"]) == 1
    assert dup_sequence["steps"][0]["email_subject"] == "Original subject"

    dup_mailboxes = api_client.get(
        f"/api/v1/workspaces/{ws_id}/campaigns/{dup_id}/mailboxes",
        headers=user.auth_header,
    ).json()
    assert [m["mailbox_id"] for m in dup_mailboxes] == [mbx]

    dup_settings = api_client.get(
        f"/api/v1/workspaces/{ws_id}/campaigns/{dup_id}/settings",
        headers=user.auth_header,
    ).json()
    assert len(dup_settings) == 1


# ---------------------------------------------------------------------------
# Cross-tenant isolation
# ---------------------------------------------------------------------------


def test_cross_workspace_campaign_isolation(
    api_client: TestClient, make_test_user, db_admin: Session
) -> None:
    user_a = make_test_user()
    user_b = make_test_user()
    ws_a = _bootstrap(api_client, user_a, "Isolation A")
    ws_b = _bootstrap(api_client, user_b, "Isolation B")

    campaign_a = _create_campaign(api_client, user_a, ws_a["id"], "Campaign A")
    campaign_b = _create_campaign(api_client, user_b, ws_b["id"], "Campaign B")
    lead_b = _create_lead(api_client, user_b, ws_b["id"], "leadb@tenant.example.com")
    mailbox_b = _create_mailbox(db_admin, ws_b["id"], "mailboxb@tenant.example.com")

    # User A cannot see/mutate Campaign B through Workspace A's URL prefix.
    assert (
        api_client.get(
            f"/api/v1/workspaces/{ws_a['id']}/campaigns/{campaign_b['id']}",
            headers=user_a.auth_header,
        ).status_code
        == 404
    )
    assert (
        api_client.patch(
            f"/api/v1/workspaces/{ws_a['id']}/campaigns/{campaign_b['id']}",
            json={"expected_version": 1, "name": "Hijacked"},
            headers=user_a.auth_header,
        ).status_code
        == 404
    )

    # Workspace A audience selection cannot reference Workspace B's lead.
    select_foreign_lead = api_client.post(
        f"/api/v1/workspaces/{ws_a['id']}/campaigns/{campaign_a['id']}/audience/select",
        json={"lead_ids": [lead_b["id"]], "list_ids": []},
        headers={**user_a.auth_header, "Idempotency-Key": uuid.uuid4().hex},
    )
    assert select_foreign_lead.status_code == 422

    # Workspace A cannot assign Workspace B's mailbox.
    assign_foreign_mailbox = api_client.post(
        f"/api/v1/workspaces/{ws_a['id']}/campaigns/{campaign_a['id']}/mailboxes",
        json={"mailbox_id": mailbox_b},
        headers=user_a.auth_header,
    )
    assert assign_foreign_mailbox.status_code == 422

    # Workspace A listing only shows Campaign A.
    listing_a = api_client.get(
        f"/api/v1/workspaces/{ws_a['id']}/campaigns", headers=user_a.auth_header
    ).json()
    assert {c["id"] for c in listing_a["items"]} == {campaign_a["id"]}


def test_rls_direct_for_campaigns(
    api_client: TestClient, make_test_user, raw_db: Session
) -> None:
    user_a = make_test_user()
    user_b = make_test_user()
    ws_a = _bootstrap(api_client, user_a, "RLS Campaigns A")
    ws_b = _bootstrap(api_client, user_b, "RLS Campaigns B")

    campaign_a = _create_campaign(api_client, user_a, ws_a["id"], "RLS Campaign A")
    campaign_b = _create_campaign(api_client, user_b, ws_b["id"], "RLS Campaign B")

    _set_context(raw_db, user_id=user_a.user_id, workspace_id=ws_a["id"])
    visible = raw_db.execute(text("SELECT id FROM public.campaigns")).scalars().all()
    assert visible == [uuid.UUID(campaign_a["id"])]

    res = raw_db.execute(
        text("UPDATE public.campaigns SET name = 'Hijacked' WHERE id = :foreign_id"),
        {"foreign_id": campaign_b["id"]},
    )
    assert res.rowcount == 0


# ---------------------------------------------------------------------------
# RBAC matrix
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("role_code", "can_draft"),
    [("VIEWER", False), ("MEMBER", True), ("MANAGER", True), ("ADMIN", True)],
)
def test_campaigns_rbac_matrix(
    api_client: TestClient,
    make_test_user,
    db_admin: Session,
    role_code: str,
    can_draft: bool,
) -> None:
    owner = make_test_user()
    actor = make_test_user()
    ws = _bootstrap(api_client, owner, f"Campaigns RBAC {role_code}")
    ws_id = ws["id"]

    api_client.get("/api/v1/me", headers=actor.auth_header)
    _seed_membership(db_admin, ws_id, actor.user_id, role_code)
    db_admin.commit()

    campaign = _create_campaign(api_client, owner, ws_id, "RBAC Campaign")

    # All active roles can read (product.read).
    get_res = api_client.get(
        f"/api/v1/workspaces/{ws_id}/campaigns/{campaign['id']}",
        headers=actor.auth_header,
    )
    assert get_res.status_code == 200

    create_res = api_client.post(
        f"/api/v1/workspaces/{ws_id}/campaigns",
        json={"name": "Actor Created Campaign"},
        headers={**actor.auth_header, "Idempotency-Key": uuid.uuid4().hex},
    )
    assert create_res.status_code == (201 if can_draft else 403)

    archive_res = api_client.post(
        f"/api/v1/workspaces/{ws_id}/campaigns/{campaign['id']}/archive",
        json={"expected_version": campaign["version"]},
        headers=actor.auth_header,
    )
    assert archive_res.status_code == (200 if can_draft else 403)
