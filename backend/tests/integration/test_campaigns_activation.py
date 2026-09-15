from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _stub_celery_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """No reachable Redis broker in this sandbox -- see
    test_campaigns_audience_capture.py's identical fixture for the full
    rationale. Only the network send_task() hops are stubbed; every other
    line of the real activation/planning code runs against the live
    database, and the stubbed dispatches are immediately followed by running
    the corresponding task synchronously via Task.apply()."""
    import workers.campaigns as workers_campaigns

    from app.modules.campaigns.activation_service import CampaignActivationService
    from app.modules.campaigns.audience_service import AudienceService

    monkeypatch.setattr(
        AudienceService, "_dispatch_capture_task", lambda self, **kw: None
    )
    monkeypatch.setattr(
        CampaignActivationService, "_dispatch_enroll_task", lambda self, **kw: None
    )
    monkeypatch.setattr(workers_campaigns, "_dispatch_render_task", lambda **kw: None)


# ---------------------------------------------------------------------------
# Shared helpers (duplicated from test_campaigns_flow.py /
# test_campaigns_audience_capture.py per this repo's per-file convention)
# ---------------------------------------------------------------------------


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
    position: int = 1,
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


def _configure_settings(
    client: TestClient,
    user,
    workspace_id: str,
    campaign_id: str,
    *,
    timezone: str = "America/New_York",
    weekdays: list[int] | None = None,
    window_start_local: str = "09:00:00",
    window_end_local: str = "17:00:00",
) -> dict:
    resp = client.post(
        f"/api/v1/workspaces/{workspace_id}/campaigns/{campaign_id}/settings",
        json={
            "timezone": timezone,
            "weekdays": weekdays or [1, 2, 3, 4, 5, 6, 7],
            "window_start_local": window_start_local,
            "window_end_local": window_end_local,
            "daily_limit": 50,
        },
        headers=user.auth_header,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _assign_mailbox(
    client: TestClient, user, workspace_id: str, campaign_id: str, mailbox_id: str
) -> None:
    resp = client.post(
        f"/api/v1/workspaces/{workspace_id}/campaigns/{campaign_id}/mailboxes",
        json={"mailbox_id": mailbox_id},
        headers=user.auth_header,
    )
    assert resp.status_code == 200, resp.text


def _select_audience(
    client: TestClient,
    user,
    workspace_id: str,
    campaign_id: str,
    *,
    lead_ids: list[str],
) -> dict:
    resp = client.post(
        f"/api/v1/workspaces/{workspace_id}/campaigns/{campaign_id}/audience/select",
        json={"lead_ids": lead_ids, "list_ids": []},
        headers={**user.auth_header, "Idempotency-Key": uuid.uuid4().hex},
    )
    assert resp.status_code == 202, resp.text
    return resp.json()


def _run_capture_task(workspace_id: str, campaign_id: str, audience_id: str) -> None:
    from workers.campaigns import capture_audience_chunk

    result = capture_audience_chunk.apply(
        kwargs={
            "workspace_id": workspace_id,
            "campaign_id": campaign_id,
            "audience_id": audience_id,
        }
    )
    result.get()


def _commit_audience(
    client: TestClient, user, workspace_id: str, campaign_id: str, audience_id: str
) -> dict:
    resp = client.post(
        f"/api/v1/workspaces/{workspace_id}/campaigns/{campaign_id}/audience/{audience_id}/commit",
        headers=user.auth_header,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _run_enroll_task(workspace_id: str, campaign_id: str, activation_id: str):
    from workers.campaigns import enroll_activation_chunk

    return enroll_activation_chunk.apply(
        kwargs={
            "workspace_id": workspace_id,
            "campaign_id": campaign_id,
            "activation_id": activation_id,
        }
    )


def _run_render_task(workspace_id: str, campaign_id: str, activation_id: str):
    from workers.campaigns import render_messages_chunk

    return render_messages_chunk.apply(
        kwargs={
            "workspace_id": workspace_id,
            "campaign_id": campaign_id,
            "activation_id": activation_id,
        }
    )


def _run_planning_to_completion(
    workspace_id: str, campaign_id: str, activation_id: str
) -> None:
    """Drives ENROLL then RENDER to completion. Celery's eager apply() loops
    through a task's own self.retry() calls synchronously in-process (verified
    empirically -- self.retry() does not need an external re-invocation loop
    the way capture_audience_chunk's tests might suggest; those tests simply
    never need a second chunk at their fixture sizes), so one .apply().get()
    per phase is sufficient regardless of how many internal chunks it takes."""
    _run_enroll_task(workspace_id, campaign_id, activation_id).get()
    _run_render_task(workspace_id, campaign_id, activation_id).get()


def _activate(
    client: TestClient,
    user,
    workspace_id: str,
    campaign_id: str,
    *,
    expected_version: int,
    start_at: str | None = None,
    idempotency_key: str | None = None,
    expect_status: int = 200,
) -> dict:
    body: dict = {"expected_version": expected_version}
    if start_at is not None:
        body["start_at"] = start_at
    resp = client.post(
        f"/api/v1/workspaces/{workspace_id}/campaigns/{campaign_id}/activate",
        json=body,
        headers={
            **user.auth_header,
            "Idempotency-Key": idempotency_key or uuid.uuid4().hex,
        },
    )
    assert resp.status_code == expect_status, resp.text
    return resp.json()


def _current_activation_id(db_admin: Session, ws_id: str, cid: str) -> str:
    row = (
        db_admin.execute(
            text(
                "SELECT activation_id FROM campaigns WHERE workspace_id=:ws AND id=:cid"
            ),
            {"ws": ws_id, "cid": cid},
        )
        .mappings()
        .one()
    )
    return str(row["activation_id"])


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
    db_admin.commit()


def _build_ready_campaign(
    api_client: TestClient,
    user,
    name: str,
    *,
    db_admin: Session,
    n_leads: int = 2,
    timezone: str = "America/New_York",
) -> dict:
    """A DRAFT campaign with one Email step, one connected mailbox, settings,
    and a READY, committed audience of n_leads accepted leads. Returns a dict
    with ws_id, campaign_id, mailbox_id, lead_ids, audience_id, version."""
    ws = _bootstrap(api_client, user, f"{name} WS")
    ws_id = ws["id"]
    campaign = _create_campaign(api_client, user, ws_id, name)
    cid = campaign["id"]

    _add_email_step(api_client, user, ws_id, cid)
    mailbox_id = _create_mailbox(db_admin, ws_id, f"{uuid.uuid4().hex}@example.com")
    _assign_mailbox(api_client, user, ws_id, cid, mailbox_id)
    _configure_settings(api_client, user, ws_id, cid, timezone=timezone)

    lead_ids = [
        _create_lead(api_client, user, ws_id, f"{uuid.uuid4().hex}@example.com")["id"]
        for _ in range(n_leads)
    ]
    selected = _select_audience(api_client, user, ws_id, cid, lead_ids=lead_ids)
    _run_capture_task(ws_id, cid, selected["id"])
    _commit_audience(api_client, user, ws_id, cid, selected["id"])

    campaign = api_client.get(
        f"/api/v1/workspaces/{ws_id}/campaigns/{cid}", headers=user.auth_header
    ).json()
    return {
        "ws_id": ws_id,
        "campaign_id": cid,
        "mailbox_id": mailbox_id,
        "lead_ids": lead_ids,
        "audience_id": selected["id"],
        "version": campaign["version"],
    }


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_activate_now_plans_full_campaign(
    api_client: TestClient, make_test_user, db_admin: Session
) -> None:
    user = make_test_user()
    setup = _build_ready_campaign(
        api_client, user, "Activation Happy Path", db_admin=db_admin, n_leads=3
    )
    ws_id, cid = setup["ws_id"], setup["campaign_id"]

    activated = _activate(
        api_client, user, ws_id, cid, expected_version=setup["version"]
    )
    assert activated["status"] == "RUNNING"
    assert activated["planning_status"] == "PENDING"
    assert activated["start_at"] is not None

    activation_id = _current_activation_id(db_admin, ws_id, cid)
    _run_planning_to_completion(ws_id, cid, activation_id)

    campaign_after = api_client.get(
        f"/api/v1/workspaces/{ws_id}/campaigns/{cid}", headers=user.auth_header
    ).json()
    assert campaign_after["planning_status"] == "READY"

    enrollments = (
        db_admin.execute(
            text(
                "SELECT id, state, assigned_mailbox_id FROM campaign_enrollments "
                "WHERE workspace_id = :ws AND campaign_id = :cid"
            ),
            {"ws": ws_id, "cid": cid},
        )
        .mappings()
        .all()
    )
    assert len(enrollments) == 3
    assert all(e["state"] == "ACTIVE" for e in enrollments)
    assert all(e["assigned_mailbox_id"] == setup["mailbox_id"] for e in enrollments)

    messages = (
        db_admin.execute(
            text(
                "SELECT status, content_subject, content_digest, due_at, anchor_at "
                "FROM messages WHERE workspace_id = :ws AND campaign_id = :cid"
            ),
            {"ws": ws_id, "cid": cid},
        )
        .mappings()
        .all()
    )
    assert len(messages) == 3
    assert all(m["status"] == "SCHEDULED" for m in messages)
    assert all(m["content_digest"] is not None for m in messages)
    assert all(m["due_at"] is not None and m["anchor_at"] is not None for m in messages)

    sequence = (
        db_admin.execute(
            text(
                "SELECT status, content_digest FROM campaign_sequences "
                "WHERE workspace_id = :ws AND campaign_id = :cid"
            ),
            {"ws": ws_id, "cid": cid},
        )
        .mappings()
        .one()
    )
    assert sequence["status"] == "FROZEN"
    assert sequence["content_digest"] is not None


def test_activate_future_start_schedules_campaign(
    api_client: TestClient, make_test_user, db_admin: Session
) -> None:
    user = make_test_user()
    setup = _build_ready_campaign(
        api_client, user, "Activation Future Start", db_admin=db_admin, n_leads=1
    )
    ws_id, cid = setup["ws_id"], setup["campaign_id"]

    future = "2099-01-01T12:00:00+00:00"
    activated = _activate(
        api_client, user, ws_id, cid, expected_version=setup["version"], start_at=future
    )
    assert activated["status"] == "SCHEDULED"
    assert activated["start_at"].startswith("2099-01-01")

    activation_id = _current_activation_id(db_admin, ws_id, cid)
    _run_planning_to_completion(ws_id, cid, activation_id)

    message = (
        db_admin.execute(
            text(
                "SELECT due_at, anchor_at FROM messages "
                "WHERE workspace_id=:ws AND campaign_id=:cid"
            ),
            {"ws": ws_id, "cid": cid},
        )
        .mappings()
        .one()
    )
    assert message["due_at"].year == 2099


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


def test_activate_idempotent_replay_returns_same_activation(
    api_client: TestClient, make_test_user, db_admin: Session
) -> None:
    user = make_test_user()
    setup = _build_ready_campaign(
        api_client, user, "Activation Idempotent Replay", db_admin=db_admin
    )
    ws_id, cid = setup["ws_id"], setup["campaign_id"]
    key = uuid.uuid4().hex

    first = _activate(
        api_client,
        user,
        ws_id,
        cid,
        expected_version=setup["version"],
        idempotency_key=key,
    )
    second = _activate(
        api_client,
        user,
        ws_id,
        cid,
        expected_version=setup["version"],
        idempotency_key=key,
    )

    assert first["version"] == second["version"]
    assert first["status"] == second["status"]

    job_count = db_admin.execute(
        text(
            "SELECT count(*) FROM campaign_planning_jobs "
            "WHERE workspace_id=:ws AND campaign_id=:cid"
        ),
        {"ws": ws_id, "cid": cid},
    ).scalar_one()
    assert job_count == 2  # exactly one ENROLL + one RENDER job, not duplicated


def test_activate_idempotency_key_reuse_with_different_payload_conflicts(
    api_client: TestClient, make_test_user, db_admin: Session
) -> None:
    user = make_test_user()
    setup = _build_ready_campaign(
        api_client, user, "Activation Key Conflict", db_admin=db_admin
    )
    ws_id, cid = setup["ws_id"], setup["campaign_id"]
    key = uuid.uuid4().hex

    _activate(
        api_client,
        user,
        ws_id,
        cid,
        expected_version=setup["version"],
        idempotency_key=key,
    )

    # Same key, different payload (a different expected_version) must conflict.
    resp = api_client.post(
        f"/api/v1/workspaces/{ws_id}/campaigns/{cid}/activate",
        json={"expected_version": setup["version"] + 999},
        headers={**user.auth_header, "Idempotency-Key": key},
    )
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


def test_double_activate_only_one_wins(
    api_client: TestClient, make_test_user, db_admin: Session
) -> None:
    user = make_test_user()
    setup = _build_ready_campaign(
        api_client, user, "Activation Double Click", db_admin=db_admin
    )
    ws_id, cid = setup["ws_id"], setup["campaign_id"]

    first = _activate(
        api_client,
        user,
        ws_id,
        cid,
        expected_version=setup["version"],
        idempotency_key=uuid.uuid4().hex,
    )
    assert first["status"] in ("RUNNING", "SCHEDULED")

    # A second request with a *different* idempotency key and the same
    # now-stale expected_version must not double-activate.
    second = api_client.post(
        f"/api/v1/workspaces/{ws_id}/campaigns/{cid}/activate",
        json={"expected_version": setup["version"]},
        headers={**user.auth_header, "Idempotency-Key": uuid.uuid4().hex},
    )
    assert second.status_code == 409

    activation_count = db_admin.execute(
        text(
            "SELECT count(DISTINCT activation_id) FROM campaigns "
            "WHERE workspace_id=:ws AND id=:cid AND activation_id IS NOT NULL"
        ),
        {"ws": ws_id, "cid": cid},
    ).scalar_one()
    assert activation_count == 1


# ---------------------------------------------------------------------------
# Preflight failure
# ---------------------------------------------------------------------------


def test_activate_blocked_by_preflight_failure(
    api_client: TestClient, make_test_user
) -> None:
    user = make_test_user()
    ws = _bootstrap(api_client, user, "Activation Preflight Block WS")
    ws_id = ws["id"]
    campaign = _create_campaign(api_client, user, ws_id, "No Mailbox Campaign")
    cid = campaign["id"]
    _add_email_step(api_client, user, ws_id, cid)
    _configure_settings(api_client, user, ws_id, cid)
    # No mailbox assigned, no audience selected -- preflight must fail.

    # draft_sequence_id/current_settings_id updates above each bump
    # campaigns.version via app_touch_row -- refetch the current value rather
    # than reusing the stale one from the initial create response.
    campaign = api_client.get(
        f"/api/v1/workspaces/{ws_id}/campaigns/{cid}", headers=user.auth_header
    ).json()

    resp = api_client.post(
        f"/api/v1/workspaces/{ws_id}/campaigns/{cid}/activate",
        json={"expected_version": campaign["version"]},
        headers={**user.auth_header, "Idempotency-Key": uuid.uuid4().hex},
    )
    assert resp.status_code == 422, resp.text
    body = resp.json()
    assert body["error"]["details"]["ready"] is False
    codes = {e["code"] for e in body["error"]["details"]["errors"]}
    assert "no_mailboxes_assigned" in codes

    campaign_after = api_client.get(
        f"/api/v1/workspaces/{ws_id}/campaigns/{cid}", headers=user.auth_header
    ).json()
    assert campaign_after["status"] == "DRAFT"
    assert (
        campaign_after["draft_sequence_id"] is not None
    )  # unchanged, still just draft


# ---------------------------------------------------------------------------
# RBAC
# ---------------------------------------------------------------------------


def test_member_role_cannot_activate(
    api_client: TestClient, make_test_user, db_admin: Session
) -> None:
    owner = make_test_user()
    member = make_test_user()
    setup = _build_ready_campaign(
        api_client, owner, "Activation RBAC", db_admin=db_admin
    )
    # profiles rows are created lazily on first authenticated request (see
    # get_current_user) -- workspace_memberships_user_fkey requires one to
    # already exist, so the member must make one call before being seeded.
    api_client.get("/api/v1/me", headers=member.auth_header)
    _seed_membership(db_admin, setup["ws_id"], member.user_id, "MEMBER")

    resp = api_client.post(
        f"/api/v1/workspaces/{setup['ws_id']}/campaigns/{setup['campaign_id']}/activate",
        json={"expected_version": setup["version"]},
        headers={**member.auth_header, "Idempotency-Key": uuid.uuid4().hex},
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# Tenant isolation
# ---------------------------------------------------------------------------


def test_cannot_activate_foreign_workspace_campaign(
    api_client: TestClient, make_test_user, db_admin: Session
) -> None:
    user_a = make_test_user()
    user_b = make_test_user()
    setup_b = _build_ready_campaign(
        api_client, user_b, "Activation Tenant B", db_admin=db_admin
    )
    ws_a = _bootstrap(api_client, user_a, "Activation Tenant A WS")

    resp = api_client.post(
        f"/api/v1/workspaces/{ws_a['id']}/campaigns/{setup_b['campaign_id']}/activate",
        json={"expected_version": setup_b["version"]},
        headers={**user_a.auth_header, "Idempotency-Key": uuid.uuid4().hex},
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Planner recovery / idempotency
# ---------------------------------------------------------------------------


def test_enroll_recovery_produces_no_duplicates(
    api_client: TestClient,
    make_test_user,
    db_admin: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Forces genuine multi-chunk ENROLL (BATCH_SIZE=1 over 3 leads) so a
    redelivered/duplicate task execution after real partial progress must
    still converge on exactly one enrollment/message per lead."""
    import workers.campaigns as workers_campaigns

    monkeypatch.setattr(workers_campaigns, "BATCH_SIZE", 1)

    user = make_test_user()
    setup = _build_ready_campaign(
        api_client, user, "Activation Planner Recovery", db_admin=db_admin, n_leads=3
    )
    ws_id, cid = setup["ws_id"], setup["campaign_id"]

    _activate(api_client, user, ws_id, cid, expected_version=setup["version"])
    activation_id = _current_activation_id(db_admin, ws_id, cid)

    _run_enroll_task(ws_id, cid, activation_id).get()

    job_state = (
        db_admin.execute(
            text(
                "SELECT state, processed_count FROM campaign_planning_jobs "
                "WHERE workspace_id=:ws AND campaign_id=:cid AND activation_id=:act "
                "AND phase='ENROLL'"
            ),
            {"ws": ws_id, "cid": cid, "act": activation_id},
        )
        .mappings()
        .one()
    )
    assert job_state["state"] == "READY"
    assert job_state["processed_count"] == 3

    # Redelivered duplicate execution of an already-READY job is a no-op.
    _run_enroll_task(ws_id, cid, activation_id)

    enrollments = (
        db_admin.execute(
            text(
                "SELECT lead_id FROM campaign_enrollments "
                "WHERE workspace_id=:ws AND campaign_id=:cid"
            ),
            {"ws": ws_id, "cid": cid},
        )
        .mappings()
        .all()
    )
    assert len(enrollments) == 3
    assert len({e["lead_id"] for e in enrollments}) == 3

    messages = (
        db_admin.execute(
            text("SELECT id FROM messages WHERE workspace_id=:ws AND campaign_id=:cid"),
            {"ws": ws_id, "cid": cid},
        )
        .mappings()
        .all()
    )
    assert len(messages) == 3


# ---------------------------------------------------------------------------
# Suppression race
# ---------------------------------------------------------------------------


def test_suppressed_after_capture_excluded_from_enrollment(
    api_client: TestClient, make_test_user, db_admin: Session
) -> None:
    user = make_test_user()
    ws = _bootstrap(api_client, user, "Activation Suppression WS")
    ws_id = ws["id"]
    campaign = _create_campaign(api_client, user, ws_id, "Suppression Campaign")
    cid = campaign["id"]
    _add_email_step(api_client, user, ws_id, cid)
    mailbox_id = _create_mailbox(db_admin, ws_id, f"{uuid.uuid4().hex}@example.com")
    _assign_mailbox(api_client, user, ws_id, cid, mailbox_id)
    _configure_settings(api_client, user, ws_id, cid)

    good_lead = _create_lead(api_client, user, ws_id, "suppression-good@example.com")
    suppressed_lead = _create_lead(
        api_client, user, ws_id, "suppression-bad@example.com"
    )
    selected = _select_audience(
        api_client, user, ws_id, cid, lead_ids=[good_lead["id"], suppressed_lead["id"]]
    )
    _run_capture_task(ws_id, cid, selected["id"])
    _commit_audience(api_client, user, ws_id, cid, selected["id"])

    # Suppress one address AFTER capture but BEFORE activation/enrollment --
    # capture-time eligibility_status is still ACCEPTED for both.
    db_admin.execute(
        text(
            """
            INSERT INTO suppressions (workspace_id, address_id, reason, status)
            VALUES (:ws,
                (SELECT id FROM recipient_addresses
                 WHERE workspace_id=:ws
                   AND canonical_address='suppression-bad@example.com'),
                'MANUAL', 'ACTIVE')
            """
        ),
        {"ws": ws_id},
    )
    db_admin.commit()

    campaign_current = api_client.get(
        f"/api/v1/workspaces/{ws_id}/campaigns/{cid}", headers=user.auth_header
    ).json()
    _activate(
        api_client, user, ws_id, cid, expected_version=campaign_current["version"]
    )
    activation_id = _current_activation_id(db_admin, ws_id, cid)
    _run_planning_to_completion(ws_id, cid, activation_id)

    enrollments = (
        db_admin.execute(
            text(
                "SELECT lead_id FROM campaign_enrollments "
                "WHERE workspace_id=:ws AND campaign_id=:cid"
            ),
            {"ws": ws_id, "cid": cid},
        )
        .mappings()
        .all()
    )
    assert {str(e["lead_id"]) for e in enrollments} == {good_lead["id"]}

    campaign_after = api_client.get(
        f"/api/v1/workspaces/{ws_id}/campaigns/{cid}", headers=user.auth_header
    ).json()
    assert (
        campaign_after["planning_status"] == "READY"
    )  # completes despite the exclusion


# ---------------------------------------------------------------------------
# Snapshot determinism
# ---------------------------------------------------------------------------


def test_lead_change_after_capture_does_not_affect_rendered_content(
    api_client: TestClient, make_test_user, db_admin: Session
) -> None:
    user = make_test_user()
    setup = _build_ready_campaign(
        api_client,
        user,
        "Activation Snapshot Determinism",
        db_admin=db_admin,
        n_leads=1,
    )
    ws_id, cid, lead_id = setup["ws_id"], setup["campaign_id"], setup["lead_ids"][0]

    _activate(api_client, user, ws_id, cid, expected_version=setup["version"])
    activation_id = _current_activation_id(db_admin, ws_id, cid)

    # Mutate the lead's name after activation/capture but before RENDER runs.
    db_admin.execute(
        text("UPDATE leads SET first_name = 'ChangedAfterCapture' WHERE id = :id"),
        {"id": lead_id},
    )
    db_admin.commit()

    _run_planning_to_completion(ws_id, cid, activation_id)

    message = (
        db_admin.execute(
            text(
                "SELECT content_subject FROM messages "
                "WHERE workspace_id=:ws AND campaign_id=:cid"
            ),
            {"ws": ws_id, "cid": cid},
        )
        .mappings()
        .one()
    )
    assert "ChangedAfterCapture" not in message["content_subject"]
    assert "Ada" in message["content_subject"]


# ---------------------------------------------------------------------------
# Pause / resume
# ---------------------------------------------------------------------------


def test_pause_and_resume_cycle(
    api_client: TestClient, make_test_user, db_admin: Session
) -> None:
    user = make_test_user()
    setup = _build_ready_campaign(
        api_client, user, "Activation Pause Resume", db_admin=db_admin
    )
    ws_id, cid = setup["ws_id"], setup["campaign_id"]

    activated = _activate(
        api_client, user, ws_id, cid, expected_version=setup["version"]
    )
    assert activated["status"] == "RUNNING"

    paused = api_client.post(
        f"/api/v1/workspaces/{ws_id}/campaigns/{cid}/pause",
        json={"expected_version": activated["version"]},
        headers=user.auth_header,
    )
    assert paused.status_code == 200, paused.text
    assert paused.json()["status"] == "PAUSED"

    resumed = api_client.post(
        f"/api/v1/workspaces/{ws_id}/campaigns/{cid}/resume",
        json={"expected_version": paused.json()["version"]},
        headers=user.auth_header,
    )
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["status"] == "RUNNING"


def test_pause_rejected_for_draft_campaign(
    api_client: TestClient, make_test_user, db_admin: Session
) -> None:
    user = make_test_user()
    setup = _build_ready_campaign(
        api_client, user, "Activation Pause Draft Guard", db_admin=db_admin
    )
    resp = api_client.post(
        f"/api/v1/workspaces/{setup['ws_id']}/campaigns/{setup['campaign_id']}/pause",
        json={"expected_version": setup["version"]},
        headers=user.auth_header,
    )
    assert resp.status_code == 409


# ---------------------------------------------------------------------------
# Post-activation edits rejected
# ---------------------------------------------------------------------------


def test_post_activation_edits_return_clean_conflicts(
    api_client: TestClient, make_test_user, db_admin: Session
) -> None:
    user = make_test_user()
    setup = _build_ready_campaign(
        api_client, user, "Activation Edit Guard", db_admin=db_admin
    )
    ws_id, cid = setup["ws_id"], setup["campaign_id"]
    _activate(api_client, user, ws_id, cid, expected_version=setup["version"])

    mailbox_resp = api_client.post(
        f"/api/v1/workspaces/{ws_id}/campaigns/{cid}/mailboxes",
        json={"mailbox_id": setup["mailbox_id"]},
        headers=user.auth_header,
    )
    assert mailbox_resp.status_code == 409

    settings_resp = api_client.post(
        f"/api/v1/workspaces/{ws_id}/campaigns/{cid}/settings",
        json={
            "timezone": "UTC",
            "weekdays": [1, 2, 3],
            "window_start_local": "09:00:00",
            "window_end_local": "17:00:00",
        },
        headers=user.auth_header,
    )
    assert settings_resp.status_code == 409

    step_resp = api_client.post(
        f"/api/v1/workspaces/{ws_id}/campaigns/{cid}/sequence/steps",
        json={"kind": "WAIT", "position": 2, "wait_duration_minutes": 60},
        headers=user.auth_header,
    )
    assert step_resp.status_code == 409

    planning = api_client.get(
        f"/api/v1/workspaces/{ws_id}/campaigns/{cid}/planning", headers=user.auth_header
    )
    assert planning.status_code == 200, planning.text
    assert planning.json()["planning_status"] in ("PENDING", "READY")
