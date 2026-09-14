from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _stub_celery_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
    """This sandbox has no reachable Redis broker (no Docker, no local Redis
    service -- REDIS_URL points at the Docker Compose-internal hostname
    `redis`, which does not resolve here). Every other line of
    AudienceService.select_audience() -- validation, cross-tenant checks,
    the campaign_audiences/audience_capture_sources/campaign_planning_jobs
    inserts, the transaction commit -- still runs as real production code
    against the live database; only the actual celery_app.send_task() network
    call is stubbed out. The capture itself is exercised immediately
    afterward via Task.apply() (see _run_capture_task), so the worker logic
    is still verified end-to-end against real data, just not the broker hop.
    """
    from app.modules.campaigns.audience_service import AudienceService

    monkeypatch.setattr(
        AudienceService, "_dispatch_capture_task", lambda self, **kw: None
    )


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


def _create_list(client: TestClient, user, workspace_id: str, name: str) -> dict:
    resp = client.post(
        f"/api/v1/workspaces/{workspace_id}/lead-lists",
        json={"name": name},
        headers=user.auth_header,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _add_to_list(
    client: TestClient, user, workspace_id: str, list_id: str, lead_id: str
) -> None:
    resp = client.post(
        f"/api/v1/workspaces/{workspace_id}/lead-lists/{list_id}/members",
        json={"lead_id": lead_id},
        headers=user.auth_header,
    )
    assert resp.status_code == 204, resp.text


def _create_campaign(client: TestClient, user, workspace_id: str, name: str) -> dict:
    resp = client.post(
        f"/api/v1/workspaces/{workspace_id}/campaigns",
        json={"name": name},
        headers={**user.auth_header, "Idempotency-Key": uuid.uuid4().hex},
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


def _select_audience(
    client: TestClient,
    user,
    workspace_id: str,
    campaign_id: str,
    *,
    lead_ids: list[str] | None = None,
    list_ids: list[str] | None = None,
    expect_status: int = 202,
) -> dict:
    resp = client.post(
        f"/api/v1/workspaces/{workspace_id}/campaigns/{campaign_id}/audience/select",
        json={"lead_ids": lead_ids or [], "list_ids": list_ids or []},
        headers={**user.auth_header, "Idempotency-Key": uuid.uuid4().hex},
    )
    assert resp.status_code == expect_status, resp.text
    return resp.json()


def _run_capture_task(workspace_id: str, campaign_id: str, audience_id: str) -> None:
    """Runs the audience-capture Celery task synchronously in-process,
    bypassing the Redis broker -- there is no live worker process in the
    test environment. Task.apply() executes exactly like a real worker
    invocation (including any self.retry() looping), just without a broker
    round trip."""
    from workers.campaigns import capture_audience_chunk

    result = capture_audience_chunk.apply(
        kwargs={
            "workspace_id": workspace_id,
            "campaign_id": campaign_id,
            "audience_id": audience_id,
        }
    )
    result.get()  # re-raises if the task recorded a failure


def _run_abandon_task(workspace_id: str, campaign_id: str, audience_id: str) -> None:
    from workers.campaigns import capture_audience_chunk

    result = capture_audience_chunk.apply(
        kwargs={
            "workspace_id": workspace_id,
            "campaign_id": campaign_id,
            "audience_id": audience_id,
            "abandon": True,
        }
    )
    result.get()


def test_audience_select_capture_ready_and_commit(
    api_client: TestClient, make_test_user
) -> None:
    user = make_test_user()
    ws = _bootstrap(api_client, user, "Audience Capture WS")
    campaign = _create_campaign(api_client, user, ws["id"], "Audience Campaign")
    ws_id, cid = ws["id"], campaign["id"]

    lead1 = _create_lead(api_client, user, ws_id, "audience1@example.com")
    lead2 = _create_lead(api_client, user, ws_id, "audience2@example.com")

    selected = _select_audience(
        api_client, user, ws_id, cid, lead_ids=[lead1["id"], lead2["id"]]
    )
    assert selected["status"] == "CAPTURING"
    audience_id = selected["id"]

    _run_capture_task(ws_id, cid, audience_id)

    status_resp = api_client.get(
        f"/api/v1/workspaces/{ws_id}/campaigns/{cid}/audience/{audience_id}",
        headers=user.auth_header,
    ).json()
    assert status_resp["status"] == "READY"
    assert status_resp["accepted_count"] == 2
    assert status_resp["excluded_count"] == 0

    committed = api_client.post(
        f"/api/v1/workspaces/{ws_id}/campaigns/{cid}/audience/{audience_id}/commit",
        headers=user.auth_header,
    )
    assert committed.status_code == 200, committed.text
    assert committed.json()["is_committed"] is True

    campaign_after = api_client.get(
        f"/api/v1/workspaces/{ws_id}/campaigns/{cid}", headers=user.auth_header
    ).json()
    assert campaign_after["draft_audience_id"] == audience_id


def test_audience_capture_via_lead_list(api_client: TestClient, make_test_user) -> None:
    user = make_test_user()
    ws = _bootstrap(api_client, user, "Audience List WS")
    campaign = _create_campaign(api_client, user, ws["id"], "List Audience Campaign")
    ws_id, cid = ws["id"], campaign["id"]

    lead1 = _create_lead(api_client, user, ws_id, "listmember1@example.com")
    lead2 = _create_lead(api_client, user, ws_id, "listmember2@example.com")
    lead_list = _create_list(api_client, user, ws_id, "Cold Leads")
    _add_to_list(api_client, user, ws_id, lead_list["id"], lead1["id"])
    _add_to_list(api_client, user, ws_id, lead_list["id"], lead2["id"])

    selected = _select_audience(
        api_client, user, ws_id, cid, list_ids=[lead_list["id"]]
    )
    _run_capture_task(ws_id, cid, selected["id"])

    status_resp = api_client.get(
        f"/api/v1/workspaces/{ws_id}/campaigns/{cid}/audience/{selected['id']}",
        headers=user.auth_header,
    ).json()
    assert status_resp["status"] == "READY"
    assert status_resp["accepted_count"] == 2


def test_audience_capture_excludes_suppressed_and_archived_leads(
    api_client: TestClient, make_test_user, db_admin: Session
) -> None:
    user = make_test_user()
    ws = _bootstrap(api_client, user, "Audience Exclusion WS")
    campaign = _create_campaign(api_client, user, ws["id"], "Exclusion Campaign")
    ws_id, cid = ws["id"], campaign["id"]

    good_lead = _create_lead(api_client, user, ws_id, "good@example.com")
    suppressed_lead = _create_lead(api_client, user, ws_id, "suppressed@example.com")
    archived_lead = _create_lead(api_client, user, ws_id, "archived@example.com")

    # POST /suppressions is a pre-existing (Phase 3) endpoint with no prior
    # integration coverage; it currently 500s with "permission denied for
    # table suppression_sources" (a genuine bug, unrelated to Phase 7 --
    # reported separately, not fixed here). Seed the suppression directly to
    # keep this test focused on audience-capture's exclusion behavior.
    db_admin.execute(
        text(
            """
            INSERT INTO suppressions
                (workspace_id, address_id, reason, status)
            VALUES
                (:workspace_id,
                 (SELECT id FROM recipient_addresses
                  WHERE workspace_id = :workspace_id
                    AND canonical_address = 'suppressed@example.com'),
                 'MANUAL', 'ACTIVE')
            """
        ),
        {"workspace_id": ws_id},
    )
    db_admin.commit()

    archive_resp = api_client.post(
        f"/api/v1/workspaces/{ws_id}/leads/{archived_lead['id']}/archive",
        json={"expected_version": archived_lead["version"]},
        headers=user.auth_header,
    )
    assert archive_resp.status_code == 200, archive_resp.text

    selected = _select_audience(
        api_client,
        user,
        ws_id,
        cid,
        lead_ids=[good_lead["id"], suppressed_lead["id"], archived_lead["id"]],
    )
    _run_capture_task(ws_id, cid, selected["id"])

    status_resp = api_client.get(
        f"/api/v1/workspaces/{ws_id}/campaigns/{cid}/audience/{selected['id']}",
        headers=user.auth_header,
    ).json()
    assert status_resp["status"] == "READY"
    assert status_resp["accepted_count"] == 1
    assert status_resp["excluded_count"] == 2

    # Suppression itself is untouched by capture (no auto-unsuppression).
    suppressions = api_client.get(
        f"/api/v1/workspaces/{ws_id}/suppressions", headers=user.auth_header
    ).json()
    assert any(s["email"] == "suppressed@example.com" for s in suppressions["items"])


def test_audience_select_requires_at_least_one_lead_or_list(
    api_client: TestClient, make_test_user
) -> None:
    user = make_test_user()
    ws = _bootstrap(api_client, user, "Audience Empty Selection WS")
    campaign = _create_campaign(api_client, user, ws["id"], "Empty Selection Campaign")
    _select_audience(api_client, user, ws["id"], campaign["id"], expect_status=422)


def test_audience_concurrent_select_conflict(
    api_client: TestClient, make_test_user
) -> None:
    user = make_test_user()
    ws = _bootstrap(api_client, user, "Audience Concurrency WS")
    campaign = _create_campaign(api_client, user, ws["id"], "Concurrency Campaign")
    ws_id, cid = ws["id"], campaign["id"]
    lead = _create_lead(api_client, user, ws_id, "concurrent@example.com")

    first = _select_audience(api_client, user, ws_id, cid, lead_ids=[lead["id"]])
    assert first["status"] == "CAPTURING"

    # A second selection while the first is still CAPTURING hits the DB's
    # campaign_audiences_active_capture_idx partial unique index.
    second = api_client.post(
        f"/api/v1/workspaces/{ws_id}/campaigns/{cid}/audience/select",
        json={"lead_ids": [lead["id"]], "list_ids": []},
        headers={**user.auth_header, "Idempotency-Key": uuid.uuid4().hex},
    )
    assert second.status_code == 409

    # Once the first capture completes, a fresh selection is allowed again.
    _run_capture_task(ws_id, cid, first["id"])
    third = _select_audience(api_client, user, ws_id, cid, lead_ids=[lead["id"]])
    assert third["status"] == "CAPTURING"
    assert third["id"] != first["id"]


def test_audience_abandon_stuck_capture(api_client: TestClient, make_test_user) -> None:
    user = make_test_user()
    ws = _bootstrap(api_client, user, "Audience Abandon WS")
    campaign = _create_campaign(api_client, user, ws["id"], "Abandon Campaign")
    ws_id, cid = ws["id"], campaign["id"]
    lead = _create_lead(api_client, user, ws_id, "abandon@example.com")

    selected = _select_audience(api_client, user, ws_id, cid, lead_ids=[lead["id"]])
    abandon_resp = api_client.post(
        f"/api/v1/workspaces/{ws_id}/campaigns/{cid}/audience/{selected['id']}/abandon",
        headers=user.auth_header,
    )
    assert abandon_resp.status_code == 204

    _run_abandon_task(ws_id, cid, selected["id"])

    status_resp = api_client.get(
        f"/api/v1/workspaces/{ws_id}/campaigns/{cid}/audience/{selected['id']}",
        headers=user.auth_header,
    ).json()
    assert status_resp["status"] == "ABANDONED"

    # A fresh selection is now allowed again.
    fresh = _select_audience(api_client, user, ws_id, cid, lead_ids=[lead["id"]])
    assert fresh["status"] == "CAPTURING"


def test_commit_rejects_non_ready_audience(
    api_client: TestClient, make_test_user
) -> None:
    user = make_test_user()
    ws = _bootstrap(api_client, user, "Audience Commit Guard WS")
    campaign = _create_campaign(api_client, user, ws["id"], "Commit Guard Campaign")
    ws_id, cid = ws["id"], campaign["id"]
    lead = _create_lead(api_client, user, ws_id, "commitguard@example.com")

    selected = _select_audience(api_client, user, ws_id, cid, lead_ids=[lead["id"]])
    # Still CAPTURING -- commit must be rejected, never silently accepted.
    commit_resp = api_client.post(
        f"/api/v1/workspaces/{ws_id}/campaigns/{cid}/audience/{selected['id']}/commit",
        headers=user.auth_header,
    )
    assert commit_resp.status_code == 422

    # A random/nonexistent audience id is rejected the same way.
    commit_missing = api_client.post(
        f"/api/v1/workspaces/{ws_id}/campaigns/{cid}/audience/{uuid.uuid4()}/commit",
        headers=user.auth_header,
    )
    assert commit_missing.status_code == 422
