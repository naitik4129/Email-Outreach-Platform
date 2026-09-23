from __future__ import annotations

import concurrent.futures
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.modules.scheduler.service import OutboxPublisherService, SchedulerService

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Stub celery send_task so intermediate campaign planning hops run synchronously
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _stub_celery_dispatch(monkeypatch: pytest.MonkeyPatch) -> None:
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


@pytest.fixture(autouse=True)
def _cleanup_stray_outbox(db_admin: Session) -> None:
    db_admin.execute(
        text(
            """
            SET session_replication_role = replica;
            DELETE FROM outbox_deliveries;
            DELETE FROM outbox_work;
            DELETE FROM message_attempts;
            DELETE FROM messages WHERE status IN ('SCHEDULED', 'RETRY_SCHEDULED', 'QUEUED');
            SET session_replication_role = DEFAULT;
            """
        )
    )
    db_admin.commit()


# ---------------------------------------------------------------------------
# Setup Helpers
# ---------------------------------------------------------------------------


def _bootstrap(client: TestClient, user, name: str) -> dict:
    resp = client.post(
        "/api/v1/workspaces",
        json={"name": name},
        headers={**user.auth_header, "Idempotency-Key": uuid.uuid4().hex},
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


def _create_lead(client: TestClient, user, workspace_id: str, email: str) -> dict:
    resp = client.post(
        f"/api/v1/workspaces/{workspace_id}/leads",
        json={"email": email, "first_name": "Ada", "last_name": "Lovelace"},
        headers=user.auth_header,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


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


def _activate(
    client: TestClient,
    user,
    workspace_id: str,
    campaign_id: str,
    *,
    expected_version: int,
) -> dict:
    resp = client.post(
        f"/api/v1/workspaces/{workspace_id}/campaigns/{campaign_id}/activate",
        json={"expected_version": expected_version},
        headers={**user.auth_header, "Idempotency-Key": uuid.uuid4().hex},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def _current_activation_id(db_admin: Session, ws_id: str, cid: str) -> str:
    row = (
        db_admin.execute(
            text(
                "SELECT activation_id FROM campaigns WHERE workspace_id = :ws AND id = :cid"
            ),
            {"ws": ws_id, "cid": cid},
        )
        .mappings()
        .one()
    )
    return str(row["activation_id"])


def _run_planning_to_completion(
    workspace_id: str, campaign_id: str, activation_id: str
) -> None:
    from workers.campaigns import enroll_activation_chunk, render_messages_chunk

    enroll_activation_chunk.apply(
        kwargs={
            "workspace_id": workspace_id,
            "campaign_id": campaign_id,
            "activation_id": activation_id,
        }
    ).get()
    render_messages_chunk.apply(
        kwargs={
            "workspace_id": workspace_id,
            "campaign_id": campaign_id,
            "activation_id": activation_id,
        }
    ).get()


def _build_and_activate_campaign(
    api_client: TestClient,
    user,
    name: str,
    *,
    db_admin: Session,
    n_leads: int = 1,
) -> dict:
    ws = _bootstrap(api_client, user, f"{name} WS")
    ws_id = ws["id"]
    campaign = _create_campaign(api_client, user, ws_id, name)
    cid = campaign["id"]

    _add_email_step(api_client, user, ws_id, cid)
    mailbox_id = _create_mailbox(db_admin, ws_id, f"{uuid.uuid4().hex}@example.com")
    _assign_mailbox(api_client, user, ws_id, cid, mailbox_id)
    _configure_settings(api_client, user, ws_id, cid)

    lead_ids = [
        _create_lead(api_client, user, ws_id, f"{uuid.uuid4().hex}@example.com")["id"]
        for _ in range(n_leads)
    ]
    selected = _select_audience(api_client, user, ws_id, cid, lead_ids=lead_ids)
    _run_capture_task(ws_id, cid, selected["id"])
    _commit_audience(api_client, user, ws_id, cid, selected["id"])

    campaign_meta = api_client.get(
        f"/api/v1/workspaces/{ws_id}/campaigns/{cid}", headers=user.auth_header
    ).json()

    _activate(api_client, user, ws_id, cid, expected_version=campaign_meta["version"])
    activation_id = _current_activation_id(db_admin, ws_id, cid)
    _run_planning_to_completion(ws_id, cid, activation_id)

    # Fetch created messages
    messages = (
        db_admin.execute(
            text(
                "SELECT id, status, due_at FROM messages "
                "WHERE workspace_id = :ws AND campaign_id = :cid ORDER BY created_at"
            ),
            {"ws": ws_id, "cid": cid},
        )
        .mappings()
        .all()
    )

    return {
        "ws_id": ws_id,
        "campaign_id": cid,
        "mailbox_id": mailbox_id,
        "lead_ids": lead_ids,
        "message_ids": [str(m["id"]) for m in messages],
    }


# ---------------------------------------------------------------------------
# Test Cases
# ---------------------------------------------------------------------------


def test_scheduler_end_to_end_claim_and_publish(
    api_client: TestClient, make_test_user, db_admin: Session
) -> None:
    user = make_test_user()
    setup = _build_and_activate_campaign(
        api_client, user, "Scheduler E2E", db_admin=db_admin, n_leads=1
    )
    ws_id = setup["ws_id"]
    msg_id = setup["message_ids"][0]

    # Set message due_at to past so it is immediately eligible for dispatch
    now = datetime.now(UTC)
    db_admin.execute(
        text("UPDATE messages SET due_at = :due, anchor_at = :due WHERE id = :id"),
        {"due": now - timedelta(seconds=10), "id": msg_id},
    )
    db_admin.commit()

    # 1. Run scheduler discovery and claiming
    scheduler = SchedulerService(db_admin)
    disc, claimed = scheduler.discover_and_claim_due_work(batch_size=10)

    assert disc >= 1
    assert claimed >= 1

    # Verify message transitioned to QUEUED with lease metadata
    row = db_admin.execute(
        text("SELECT status, dispatch_generation, dispatch_origin, claim_expires_at FROM messages WHERE id = :id"),
        {"id": msg_id},
    ).mappings().one()

    assert row["status"] == "QUEUED"
    assert row["dispatch_generation"] == 2
    assert row["dispatch_origin"] == "SCHEDULED"
    assert row["claim_expires_at"] is not None

    # Verify outbox_work and outbox_deliveries rows created
    work_row = db_admin.execute(
        text("SELECT id, kind, schema_version, resource_id, superseded FROM outbox_work WHERE resource_id = :id"),
        {"id": msg_id},
    ).mappings().one()

    assert work_row["kind"] == "email.send"
    assert work_row["schema_version"] == 1
    assert work_row["superseded"] is False

    delivery_row = db_admin.execute(
        text("SELECT id, state, attempts, published_at FROM outbox_deliveries WHERE work_id = :wid"),
        {"wid": work_row["id"]},
    ).mappings().one()

    assert delivery_row["state"] == "PENDING"
    assert delivery_row["attempts"] == 0
    assert delivery_row["published_at"] is None

    # 2. Run outbox publisher service with mock Celery
    celery_mock = MagicMock()
    publisher = OutboxPublisherService(db_admin, celery_client=celery_mock, publisher_id="test-e2e-publisher")

    published = publisher.publish_pending_deliveries(batch_size=10)
    db_admin.commit()
    assert published >= 1

    # Verify Celery received the send task with valid SendTaskPayload
    matching_calls = [
        c for c in celery_mock.send_task.call_args_list
        if c[1].get("kwargs", {}).get("payload", {}).get("message_id") == msg_id
    ]
    assert len(matching_calls) == 1
    task_name = matching_calls[0][0][0]
    kwargs = matching_calls[0][1]["kwargs"]
    queue = matching_calls[0][1]["queue"]
    assert task_name == "email.send"
    assert queue == "email.send"
    payload = kwargs["payload"]
    assert payload["message_id"] == msg_id
    assert payload["workspace_id"] == ws_id
    assert payload["dispatch_generation"] == 2

    # Verify outbox delivery marked PUBLISHED in database
    delivery_after = db_admin.execute(
        text("SELECT state, published_at, attempts FROM outbox_deliveries WHERE id = :id"),
        {"id": delivery_row["id"]},
    ).mappings().one()

    assert delivery_after["state"] == "PUBLISHED"
    assert delivery_after["published_at"] is not None
    assert delivery_after["attempts"] == 1


def test_scheduler_due_filtering_boundaries(
    api_client: TestClient, make_test_user, db_admin: Session
) -> None:
    user = make_test_user()
    setup = _build_and_activate_campaign(
        api_client, user, "Boundary Filter", db_admin=db_admin, n_leads=3
    )
    ws_id = setup["ws_id"]
    m_past, m_future, m_retry = setup["message_ids"]

    now = datetime.now(UTC)

    # 1. Past due -> Should be eligible
    db_admin.execute(
        text("UPDATE messages SET due_at = :due, anchor_at = :due WHERE id = :id"),
        {"due": now - timedelta(seconds=10), "id": m_past},
    )

    # 2. Future due -> Excluded
    db_admin.execute(
        text("UPDATE messages SET due_at = :due, anchor_at = :due WHERE id = :id"),
        {"due": now + timedelta(hours=1), "id": m_future},
    )

    # 3. RETRY_SCHEDULED past due -> Should be eligible
    db_admin.execute(
        text(
            """
            UPDATE messages
            SET status = 'RETRY_SCHEDULED', due_at = :due, anchor_at = :due,
                retry_count = 1, next_retry_at = :due
            WHERE id = :id
            """
        ),
        {"due": now - timedelta(seconds=5), "id": m_retry},
    )
    db_admin.commit()

    scheduler = SchedulerService(db_admin)
    candidates = scheduler.repository.find_due_messages(limit=50)

    candidate_ids = {str(c.id) for c in candidates}
    assert m_past in candidate_ids
    assert m_retry in candidate_ids
    assert m_future not in candidate_ids


def test_scheduler_campaign_and_mailbox_gates(
    api_client: TestClient, make_test_user, db_admin: Session
) -> None:
    user = make_test_user()
    setup = _build_and_activate_campaign(
        api_client, user, "Gates Test", db_admin=db_admin, n_leads=2
    )
    ws_id = setup["ws_id"]
    cid = setup["campaign_id"]
    mbx_id = setup["mailbox_id"]
    msg_id = setup["message_ids"][0]

    now = datetime.now(UTC)
    db_admin.execute(
        text("UPDATE messages SET due_at = :due, anchor_at = :due WHERE id = :id"),
        {"due": now - timedelta(seconds=10), "id": msg_id},
    )
    db_admin.commit()

    scheduler = SchedulerService(db_admin)

    # 1. Message on running campaign and healthy mailbox is eligible
    candidates = scheduler.repository.find_due_messages(limit=50)
    assert any(str(c.id) == msg_id for c in candidates)
    db_admin.commit()

    # 2. Block the mailbox -> Candidate is excluded
    db_admin.execute(
        text("UPDATE mailboxes SET blocked_until = :blocked WHERE id = :id"),
        {"blocked": now + timedelta(hours=1), "id": mbx_id},
    )
    db_admin.commit()

    candidates_blocked = scheduler.repository.find_due_messages(limit=50)
    assert not any(str(c.id) == msg_id for c in candidates_blocked)
    db_admin.commit()

    # Unblock mailbox
    db_admin.execute(
        text("UPDATE mailboxes SET blocked_until = NULL WHERE id = :id"),
        {"id": mbx_id},
    )
    db_admin.commit()

    # 3. Pause the campaign -> Candidate is excluded
    db_admin.execute(
        text("UPDATE campaigns SET status = 'PAUSED' WHERE id = :id"),
        {"id": cid},
    )
    db_admin.commit()

    candidates_paused = scheduler.repository.find_due_messages(limit=50)
    assert not any(str(c.id) == msg_id for c in candidates_paused)
    db_admin.commit()


def test_concurrent_two_schedulers_no_duplicates(
    api_client: TestClient, make_test_user, db_admin: Session
) -> None:
    user = make_test_user()
    setup = _build_and_activate_campaign(
        api_client, user, "Concurrency Test", db_admin=db_admin, n_leads=6
    )
    ws_id = setup["ws_id"]
    message_ids = setup["message_ids"]

    # Set all messages to past due
    now = datetime.now(UTC)
    db_admin.execute(
        text("UPDATE messages SET due_at = :due, anchor_at = :due WHERE id = ANY(:ids)"),
        {"due": now - timedelta(seconds=10), "ids": [uuid.UUID(mid) for mid in message_ids]},
    )
    db_admin.commit()

    from app.core.config import Settings
    from app.db.session import session_scope

    def _run_worker() -> int:
        settings = Settings.current()
        with session_scope(settings) as session:
            service = SchedulerService(session)
            _, claimed = service.discover_and_claim_due_work(batch_size=10)
            return claimed

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        f1 = executor.submit(_run_worker)
        f2 = executor.submit(_run_worker)
        claimed_counts = [f1.result(), f2.result()]

    total_claimed = sum(claimed_counts)
    assert total_claimed == 6

    # Verify each message was claimed exactly once
    rows = db_admin.execute(
        text("SELECT id, status, dispatch_generation FROM messages WHERE id = ANY(:ids)"),
        {"ids": [uuid.UUID(mid) for mid in message_ids]},
    ).mappings().all()

    for r in rows:
        assert r["status"] == "QUEUED"
        assert r["dispatch_generation"] == 2

    # Verify exactly 6 outbox_work and 6 outbox_deliveries rows
    outbox_count = db_admin.execute(
        text("SELECT count(*) FROM outbox_work WHERE resource_id = ANY(:ids)"),
        {"ids": [uuid.UUID(mid) for mid in message_ids]},
    ).scalar()
    assert outbox_count == 6


def test_claim_recovery_unattempted_vs_attempted(
    api_client: TestClient, make_test_user, db_admin: Session
) -> None:
    user = make_test_user()
    setup = _build_and_activate_campaign(
        api_client, user, "Recovery Test", db_admin=db_admin, n_leads=2
    )
    ws_id = setup["ws_id"]
    m_unattempted, m_attempted = setup["message_ids"]

    past = datetime.now(UTC) - timedelta(minutes=10)

    # 1. Unattempted message with expired claim
    db_admin.execute(
        text(
            """
            UPDATE messages
            SET status = 'QUEUED', claim_expires_at = :past, dispatch_origin = 'SCHEDULED'
            WHERE id = :id
            """
        ),
        {"past": past, "id": m_unattempted},
    )

    # Seed outbox work & delivery for unattempted message
    work_id = str(uuid.uuid4())
    db_admin.execute(
        text(
            """
            INSERT INTO outbox_work
                (id, workspace_id, kind, schema_version, resource_id, resource_type,
                 semantic_key, available_at, superseded)
            VALUES
                (:id, :ws, 'email.send', 1, :res_id, 'message', :sem_key, :past, false)
            """
        ),
        {"id": work_id, "ws": ws_id, "res_id": m_unattempted, "sem_key": f"{m_unattempted}:1", "past": past},
    )
    db_admin.execute(
        text(
            """
            INSERT INTO outbox_deliveries
                (id, workspace_id, work_id, consumer, state, attempts, next_attempt_at)
            VALUES
                (:id, :ws, :wid, 'worker-send', 'PENDING', 0, :past)
            """
        ),
        {"id": str(uuid.uuid4()), "ws": ws_id, "wid": work_id, "past": past},
    )

    # 2. Attempted message with expired claim -> Has row in message_attempts
    db_admin.execute(
        text(
            """
            UPDATE messages
            SET status = 'QUEUED', claim_expires_at = :past, dispatch_origin = 'SCHEDULED'
            WHERE id = :id
            """
        ),
        {"past": past, "id": m_attempted},
    )
    mbx_id = setup["mailbox_id"]
    db_admin.execute(
        text(
            """
            INSERT INTO message_attempts
                (id, workspace_id, message_id, mailbox_id, ordinal,
                 invocation_owner, credential_generation, authorization_deadline,
                 dispatch_generation, evidence_state, started_at)
            VALUES
                (:id, :ws, :mid, :mbx, 1,
                 'worker-send-1', 1, :deadline,
                 1, 'PREPARED', :past)
            """
        ),
        {
            "id": str(uuid.uuid4()),
            "ws": ws_id,
            "mid": m_attempted,
            "mbx": mbx_id,
            "deadline": past + timedelta(minutes=5),
            "past": past,
        },
    )
    db_admin.commit()

    # Run claim recovery
    scheduler = SchedulerService(db_admin)
    recovered = scheduler.recover_expired_claims(batch_size=50)
    db_admin.commit()

    assert recovered == 1

    # Verify unattempted reset to SCHEDULED and outbox superseded
    unatt_row = db_admin.execute(
        text("SELECT status, claim_expires_at, dispatch_origin FROM messages WHERE id = :id"),
        {"id": m_unattempted},
    ).mappings().one()
    assert unatt_row["status"] == "SCHEDULED"
    assert unatt_row["claim_expires_at"] is None
    assert unatt_row["dispatch_origin"] is None

    deliv_row = db_admin.execute(
        text("SELECT state FROM outbox_deliveries WHERE work_id = :wid"),
        {"wid": work_id},
    ).mappings().one()
    assert deliv_row["state"] == "SUPERSEDED"

    # Verify attempted remained QUEUED (never reset by recovery)
    att_row = db_admin.execute(
        text("SELECT status FROM messages WHERE id = :id"),
        {"id": m_attempted},
    ).mappings().one()
    assert att_row["status"] == "QUEUED"


def test_stale_outbox_lease_recovery_and_failure_retry(
    api_client: TestClient, make_test_user, db_admin: Session
) -> None:
    user = make_test_user()
    setup = _build_and_activate_campaign(
        api_client, user, "Outbox Failure Test", db_admin=db_admin, n_leads=1
    )
    ws_id = setup["ws_id"]
    msg_id = setup["message_ids"][0]

    # Set message due_at to past
    now = datetime.now(UTC)
    db_admin.execute(
        text("UPDATE messages SET due_at = :due, anchor_at = :due WHERE id = :id"),
        {"due": now - timedelta(seconds=10), "id": msg_id},
    )
    db_admin.commit()

    # 1. Claim message so outbox work and delivery exist
    scheduler = SchedulerService(db_admin)
    scheduler.discover_and_claim_due_work(batch_size=1)
    db_admin.commit()

    # 2. Test publication failure with Redis down
    failing_celery = MagicMock()
    failing_celery.send_task.side_effect = RuntimeError("Redis broker unavailable")

    publisher = OutboxPublisherService(
        db_admin, celery_client=failing_celery, publisher_id="test-fail-publisher"
    )
    published = publisher.publish_pending_deliveries(batch_size=1)
    db_admin.commit()
    assert published == 0

    # Verify delivery marked for retry with backoff and error recorded
    deliv = db_admin.execute(
        text("SELECT state, attempts, next_attempt_at, safe_error FROM outbox_deliveries WHERE workspace_id = :ws"),
        {"ws": ws_id},
    ).mappings().one()

    assert deliv["state"] == "RETRY"
    assert deliv["attempts"] == 1
    assert "Redis broker unavailable" in deliv["safe_error"]
    assert deliv["next_attempt_at"] is not None

    # 3. Test stale lease recovery
    # Artificially set status to LEASED with past lease_expires_at
    past = datetime.now(UTC) - timedelta(minutes=5)
    db_admin.execute(
        text(
            """
            UPDATE outbox_deliveries
            SET state = 'LEASED', lease_owner = 'crashed-worker', lease_expires_at = :past
            WHERE workspace_id = :ws
            """
        ),
        {"ws": ws_id, "past": past},
    )
    db_admin.commit()

    recovered_leases = publisher.recover_stale_leases(limit=10)
    db_admin.commit()
    assert recovered_leases == 1

    deliv_recovered = db_admin.execute(
        text("SELECT state, lease_owner, lease_expires_at FROM outbox_deliveries WHERE workspace_id = :ws"),
        {"ws": ws_id},
    ).mappings().one()

    assert deliv_recovered["state"] == "RETRY"
    assert deliv_recovered["lease_owner"] is None
    assert deliv_recovered["lease_expires_at"] is None


def test_scheduler_explain_index_usage(db_admin: Session) -> None:
    """Verify that PostgreSQL EXPLAIN uses the intended partial indexes."""
    db_admin.execute(text("SET LOCAL enable_seqscan = off"))

    # 1. Due message discovery
    due_explain = db_admin.execute(
        text(
            """
            EXPLAIN
            SELECT m.id, m.workspace_id, m.campaign_id, m.mailbox_id, m.due_at,
                   m.purpose, m.schedule_generation, m.status, m.dispatch_generation
            FROM messages m
            WHERE m.status IN ('SCHEDULED', 'RETRY_SCHEDULED')
              AND m.due_at <= pg_catalog.transaction_timestamp()
            ORDER BY m.due_at, m.id
            LIMIT 50
            """
        )
    ).scalars().all()
    due_plan = "\n".join(due_explain)
    assert "messages_due_idx" in due_plan
    assert "Index Scan" in due_plan

    # 2. Expired claim discovery
    claim_explain = db_admin.execute(
        text(
            """
            EXPLAIN
            SELECT m.id, m.workspace_id, m.dispatch_generation
            FROM messages m
            WHERE m.status = 'QUEUED'
              AND m.claim_expires_at <= pg_catalog.transaction_timestamp()
            ORDER BY m.claim_expires_at, m.id
            LIMIT 50
            """
        )
    ).scalars().all()
    claim_plan = "\n".join(claim_explain)
    assert "messages_claim_expiry_idx" in claim_plan
    assert "Index Scan" in claim_plan
