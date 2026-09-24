from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import WorkspaceContext
from app.modules.analytics.repository import AnalyticsRepository
from app.modules.analytics.service import AnalyticsService


@pytest.fixture
def analytics_db() -> Session:
    engine = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    session_factory = sessionmaker(bind=engine)
    session = session_factory()
    session.execute(text("ATTACH DATABASE ':memory:' AS public;"))

    # Create tables needed for analytics
    session.execute(
        text(
            """
        CREATE TABLE public.workspaces (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL
        );
        """
        )
    )
    session.execute(
        text(
            """
        CREATE TABLE public.mailboxes (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            original_address TEXT NOT NULL,
            provider TEXT NOT NULL,
            connection_state TEXT NOT NULL DEFAULT 'CONNECTED',
            health_state TEXT NOT NULL DEFAULT 'HEALTHY',
            policy_state TEXT NOT NULL DEFAULT 'ENABLED'
        );
        """
        )
    )
    session.execute(
        text(
            """
        CREATE TABLE public.campaigns (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'RUNNING'
        );
        """
        )
    )
    session.execute(
        text(
            """
        CREATE TABLE public.campaign_sequences (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            campaign_id TEXT NOT NULL
        );
        """
        )
    )
    session.execute(
        text(
            """
        CREATE TABLE public.sequence_steps (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            sequence_id TEXT NOT NULL,
            position INTEGER NOT NULL,
            kind TEXT NOT NULL,
            email_subject TEXT
        );
        """
        )
    )
    session.execute(
        text(
            """
        CREATE TABLE public.campaign_enrollments (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            campaign_id TEXT NOT NULL,
            address_id TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'ACTIVE'
        );
        """
        )
    )
    session.execute(
        text(
            """
        CREATE TABLE public.messages (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            campaign_id TEXT,
            enrollment_id TEXT,
            sequence_id TEXT,
            step_id TEXT,
            mailbox_id TEXT NOT NULL,
            address_id TEXT NOT NULL,
            status TEXT NOT NULL,
            accepted_at TIMESTAMP,
            updated_at TIMESTAMP
        );
        """
        )
    )
    session.execute(
        text(
            """
        CREATE TABLE public.message_attempts (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            message_id TEXT NOT NULL,
            mailbox_id TEXT NOT NULL,
            ordinal INTEGER NOT NULL,
            evidence_state TEXT NOT NULL,
            error_category TEXT,
            started_at TIMESTAMP
        );
        """
        )
    )
    session.execute(
        text(
            """
        CREATE TABLE public.recipient_outcomes (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            enrollment_id TEXT NOT NULL,
            kind TEXT NOT NULL,
            source_key TEXT NOT NULL,
            occurred_at TIMESTAMP NOT NULL,
            inbound_message_id TEXT
        );
        """
        )
    )
    session.execute(
        text(
            """
        CREATE TABLE public.inbound_outreach_links (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            mailbox_id TEXT NOT NULL,
            inbound_message_id TEXT NOT NULL,
            outbound_message_id TEXT NOT NULL,
            campaign_id TEXT,
            enrollment_id TEXT,
            evidence_type TEXT NOT NULL,
            confidence TEXT NOT NULL,
            status TEXT NOT NULL,
            matched_at TIMESTAMP
        );
        """
        )
    )
    session.execute(
        text(
            """
        CREATE TABLE public.inbound_messages (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            mailbox_id TEXT NOT NULL
        );
        """
        )
    )
    session.execute(
        text(
            """
        CREATE TABLE public.safety_holds (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            target_kind TEXT NOT NULL,
            target_mailbox_id TEXT,
            status TEXT NOT NULL DEFAULT 'ACTIVE',
            reason TEXT NOT NULL
        );
        """
        )
    )
    session.commit()
    return session


def test_campaign_analytics_metric_calculations(analytics_db: Session) -> None:
    repo = AnalyticsRepository(analytics_db)
    ws_id = uuid.uuid4()
    camp_id = uuid.uuid4()
    mb_id = uuid.uuid4()
    addr_1 = uuid.uuid4()
    addr_2 = uuid.uuid4()
    enr_1 = uuid.uuid4()
    enr_2 = uuid.uuid4()

    # Seed campaign, mailboxes, enrollments
    analytics_db.execute(
        text("INSERT INTO public.workspaces (id, name) VALUES (:id, 'Test WS')"),
        {"id": str(ws_id)},
    )
    analytics_db.execute(
        text(
            "INSERT INTO public.campaigns (id, workspace_id, name, status) VALUES (:id, :ws, 'Q1 Outreach', 'RUNNING')"
        ),
        {"id": str(camp_id), "ws": str(ws_id)},
    )
    analytics_db.execute(
        text(
            "INSERT INTO public.mailboxes (id, workspace_id, original_address, provider) VALUES (:id, :ws, 'sender@test.com', 'GMAIL')"
        ),
        {"id": str(mb_id), "ws": str(ws_id)},
    )
    analytics_db.execute(
        text(
            """
            INSERT INTO public.campaign_enrollments (id, workspace_id, campaign_id, address_id)
            VALUES (:e1, :ws, :cid, :a1), (:e2, :ws, :cid, :a2)
            """
        ),
        {
            "e1": str(enr_1),
            "e2": str(enr_2),
            "ws": str(ws_id),
            "cid": str(camp_id),
            "a1": str(addr_1),
            "a2": str(addr_2),
        },
    )

    now = datetime.now(UTC)
    # Seed messages: 2 sent, 1 scheduled, 1 failed
    msg_1 = uuid.uuid4()
    msg_2 = uuid.uuid4()
    msg_3 = uuid.uuid4()
    msg_4 = uuid.uuid4()

    analytics_db.execute(
        text(
            """
            INSERT INTO public.messages (id, workspace_id, campaign_id, enrollment_id, mailbox_id, address_id, status, accepted_at)
            VALUES
                (:m1, :ws, :cid, :e1, :mb, :a1, 'SENT', :now),
                (:m2, :ws, :cid, :e2, :mb, :a2, 'SENT', :now),
                (:m3, :ws, :cid, :e1, :mb, :a1, 'SCHEDULED', NULL),
                (:m4, :ws, :cid, :e2, :mb, :a2, 'FAILED', NULL)
            """
        ),
        {
            "m1": str(msg_1),
            "m2": str(msg_2),
            "m3": str(msg_3),
            "m4": str(msg_4),
            "ws": str(ws_id),
            "cid": str(camp_id),
            "e1": str(enr_1),
            "e2": str(enr_2),
            "mb": str(mb_id),
            "a1": str(addr_1),
            "a2": str(addr_2),
            "now": now,
        },
    )

    # Seed outcomes: 1 reply on enr_1, 1 bounce on enr_2
    analytics_db.execute(
        text(
            """
            INSERT INTO public.recipient_outcomes (id, workspace_id, enrollment_id, kind, source_key, occurred_at)
            VALUES
                (:o1, :ws, :e1, 'REPLIED', 'rep-1', :now),
                (:o2, :ws, :e2, 'HARD_BOUNCE', 'bnc-1', :now)
            """
        ),
        {
            "o1": str(uuid.uuid4()),
            "o2": str(uuid.uuid4()),
            "ws": str(ws_id),
            "e1": str(enr_1),
            "e2": str(enr_2),
            "now": now,
        },
    )
    analytics_db.commit()

    stats = repo.get_campaign_analytics(ws_id, camp_id)
    assert stats is not None
    assert stats["total_recipients"] == 2
    assert stats["enrolled"] == 2
    assert stats["sent"] == 2
    assert stats["scheduled"] == 1
    assert stats["failed"] == 1
    assert stats["bounced"] == 1
    assert stats["replied"] == 1
    assert stats["bounce_rate"] == 50.0  # 1 / 2 = 50%
    assert stats["reply_rate"] == 50.0  # 1 / 2 = 50%
    assert stats["open_tracking_supported"] is False


def test_retry_isolation_does_not_inflate_sent_count(analytics_db: Session) -> None:
    """A message with 3 retry attempts must count as exactly 1 sent message."""
    repo = AnalyticsRepository(analytics_db)
    ws_id = uuid.uuid4()
    camp_id = uuid.uuid4()
    mb_id = uuid.uuid4()
    addr_id = uuid.uuid4()
    enr_id = uuid.uuid4()
    msg_id = uuid.uuid4()

    analytics_db.execute(
        text("INSERT INTO public.workspaces (id, name) VALUES (:id, 'WS')"),
        {"id": str(ws_id)},
    )
    analytics_db.execute(
        text(
            "INSERT INTO public.campaigns (id, workspace_id, name) VALUES (:id, :ws, 'Camp')"
        ),
        {"id": str(camp_id), "ws": str(ws_id)},
    )
    analytics_db.execute(
        text(
            "INSERT INTO public.mailboxes (id, workspace_id, original_address, provider) VALUES (:id, :ws, 'mb@test.com', 'SMTP')"
        ),
        {"id": str(mb_id), "ws": str(ws_id)},
    )
    analytics_db.execute(
        text(
            "INSERT INTO public.campaign_enrollments (id, workspace_id, campaign_id, address_id) VALUES (:e, :ws, :cid, :a)"
        ),
        {
            "e": str(enr_id),
            "ws": str(ws_id),
            "cid": str(camp_id),
            "a": str(addr_id),
        },
    )

    now = datetime.now(UTC)
    # Message row is SENT
    analytics_db.execute(
        text(
            """
            INSERT INTO public.messages (id, workspace_id, campaign_id, enrollment_id, mailbox_id, address_id, status, accepted_at)
            VALUES (:m, :ws, :cid, :e, :mb, :a, 'SENT', :now)
            """
        ),
        {
            "m": str(msg_id),
            "ws": str(ws_id),
            "cid": str(camp_id),
            "e": str(enr_id),
            "mb": str(mb_id),
            "a": str(addr_id),
            "now": now,
        },
    )

    # 3 attempts for this 1 message (attempt 1 failed, attempt 2 failed, attempt 3 accepted)
    analytics_db.execute(
        text(
            """
            INSERT INTO public.message_attempts (id, workspace_id, message_id, mailbox_id, ordinal, evidence_state, error_category, started_at)
            VALUES
                (:a1, :ws, :m, :mb, 1, 'REJECTED', 'TEMPORARY_PROVIDER_ERROR', :now),
                (:a2, :ws, :m, :mb, 2, 'REJECTED', 'RATE_LIMIT', :now),
                (:a3, :ws, :m, :mb, 3, 'ACCEPTED', NULL, :now)
            """
        ),
        {
            "a1": str(uuid.uuid4()),
            "a2": str(uuid.uuid4()),
            "a3": str(uuid.uuid4()),
            "ws": str(ws_id),
            "m": str(msg_id),
            "mb": str(mb_id),
            "now": now,
        },
    )
    analytics_db.commit()

    stats = repo.get_campaign_analytics(ws_id, camp_id)
    assert stats is not None
    # Must be 1 sent, NOT 3!
    assert stats["sent"] == 1


def test_duplicate_events_do_not_inflate_outcome_counts(
    analytics_db: Session,
) -> None:
    """Duplicate bounce / reply records for the same enrollment must deduplicate."""
    repo = AnalyticsRepository(analytics_db)
    ws_id = uuid.uuid4()
    camp_id = uuid.uuid4()
    mb_id = uuid.uuid4()
    addr_id = uuid.uuid4()
    enr_id = uuid.uuid4()

    analytics_db.execute(
        text("INSERT INTO public.workspaces (id, name) VALUES (:id, 'WS')"),
        {"id": str(ws_id)},
    )
    analytics_db.execute(
        text(
            "INSERT INTO public.campaigns (id, workspace_id, name) VALUES (:id, :ws, 'Camp')"
        ),
        {"id": str(camp_id), "ws": str(ws_id)},
    )
    analytics_db.execute(
        text(
            "INSERT INTO public.mailboxes (id, workspace_id, original_address, provider) VALUES (:id, :ws, 'mb@test.com', 'GMAIL')"
        ),
        {"id": str(mb_id), "ws": str(ws_id)},
    )
    analytics_db.execute(
        text(
            "INSERT INTO public.campaign_enrollments (id, workspace_id, campaign_id, address_id) VALUES (:e, :ws, :cid, :a)"
        ),
        {
            "e": str(enr_id),
            "ws": str(ws_id),
            "cid": str(camp_id),
            "a": str(addr_id),
        },
    )

    now = datetime.now(UTC)
    analytics_db.execute(
        text(
            "INSERT INTO public.messages (id, workspace_id, campaign_id, enrollment_id, mailbox_id, address_id, status, accepted_at) VALUES (:m, :ws, :cid, :e, :mb, :a, 'SENT', :now)"
        ),
        {
            "m": str(uuid.uuid4()),
            "ws": str(ws_id),
            "cid": str(camp_id),
            "e": str(enr_id),
            "mb": str(mb_id),
            "a": str(addr_id),
            "now": now,
        },
    )

    # 3 duplicate bounce rows for same enrollment
    analytics_db.execute(
        text(
            """
            INSERT INTO public.recipient_outcomes (id, workspace_id, enrollment_id, kind, source_key, occurred_at)
            VALUES
                (:o1, :ws, :e, 'HARD_BOUNCE', 'b1', :now),
                (:o2, :ws, :e, 'HARD_BOUNCE', 'b2', :now),
                (:o3, :ws, :e, 'HARD_BOUNCE', 'b3', :now)
            """
        ),
        {
            "o1": str(uuid.uuid4()),
            "o2": str(uuid.uuid4()),
            "o3": str(uuid.uuid4()),
            "ws": str(ws_id),
            "e": str(enr_id),
            "now": now,
        },
    )
    analytics_db.commit()

    stats = repo.get_campaign_analytics(ws_id, camp_id)
    assert stats is not None
    # Deduplicated by recipient enrollment: exactly 1 bounced recipient
    assert stats["bounced"] == 1


def test_unique_recipients_contacted(analytics_db: Session) -> None:
    """1 recipient receiving 3 messages must count as 1 unique recipient contacted, not 3."""
    repo = AnalyticsRepository(analytics_db)
    ws_id = uuid.uuid4()
    camp_id = uuid.uuid4()
    mb_id = uuid.uuid4()
    addr_id = uuid.uuid4()
    enr_id = uuid.uuid4()

    analytics_db.execute(
        text("INSERT INTO public.workspaces (id, name) VALUES (:id, 'WS')"),
        {"id": str(ws_id)},
    )
    analytics_db.execute(
        text(
            "INSERT INTO public.campaigns (id, workspace_id, name) VALUES (:id, :ws, 'Camp')"
        ),
        {"id": str(camp_id), "ws": str(ws_id)},
    )
    analytics_db.execute(
        text(
            "INSERT INTO public.mailboxes (id, workspace_id, original_address, provider) VALUES (:id, :ws, 'mb@test.com', 'GMAIL')"
        ),
        {"id": str(mb_id), "ws": str(ws_id)},
    )
    analytics_db.execute(
        text(
            "INSERT INTO public.campaign_enrollments (id, workspace_id, campaign_id, address_id) VALUES (:e, :ws, :cid, :a)"
        ),
        {
            "e": str(enr_id),
            "ws": str(ws_id),
            "cid": str(camp_id),
            "a": str(addr_id),
        },
    )

    now = datetime.now(UTC)
    for _ in range(3):
        analytics_db.execute(
            text(
                "INSERT INTO public.messages (id, workspace_id, campaign_id, enrollment_id, mailbox_id, address_id, status, accepted_at) VALUES (:m, :ws, :cid, :e, :mb, :a, 'SENT', :now)"
            ),
            {
                "m": str(uuid.uuid4()),
                "ws": str(ws_id),
                "cid": str(camp_id),
                "e": str(enr_id),
                "mb": str(mb_id),
                "a": str(addr_id),
                "now": now,
            },
        )
    analytics_db.commit()

    stats = repo.get_campaign_analytics(ws_id, camp_id)
    assert stats is not None
    assert stats["sent"] == 3
    assert stats["unique_recipients_contacted"] == 1


def test_sequence_step_reply_attribution(analytics_db: Session) -> None:
    """Replies must be attributed only to the specific step that triggered them."""
    repo = AnalyticsRepository(analytics_db)
    ws_id = uuid.uuid4()
    camp_id = uuid.uuid4()
    seq_id = uuid.uuid4()
    mb_id = uuid.uuid4()
    step_1 = uuid.uuid4()
    step_2 = uuid.uuid4()
    enr_id = uuid.uuid4()
    addr_id = uuid.uuid4()

    analytics_db.execute(
        text("INSERT INTO public.workspaces (id, name) VALUES (:id, 'WS')"),
        {"id": str(ws_id)},
    )
    analytics_db.execute(
        text(
            "INSERT INTO public.campaigns (id, workspace_id, name) VALUES (:id, :ws, 'Camp')"
        ),
        {"id": str(camp_id), "ws": str(ws_id)},
    )
    analytics_db.execute(
        text(
            "INSERT INTO public.mailboxes (id, workspace_id, original_address, provider) VALUES (:id, :ws, 'mb@test.com', 'GMAIL')"
        ),
        {"id": str(mb_id), "ws": str(ws_id)},
    )
    analytics_db.execute(
        text(
            "INSERT INTO public.campaign_sequences (id, workspace_id, campaign_id) VALUES (:id, :ws, :cid)"
        ),
        {"id": str(seq_id), "ws": str(ws_id), "cid": str(camp_id)},
    )
    analytics_db.execute(
        text(
            """
            INSERT INTO public.sequence_steps (id, workspace_id, sequence_id, position, kind, email_subject)
            VALUES
                (:s1, :ws, :seq, 1, 'EMAIL', 'Intro email'),
                (:s2, :ws, :seq, 2, 'EMAIL', 'Follow up')
            """
        ),
        {
            "s1": str(step_1),
            "s2": str(step_2),
            "ws": str(ws_id),
            "seq": str(seq_id),
        },
    )
    analytics_db.execute(
        text(
            "INSERT INTO public.campaign_enrollments (id, workspace_id, campaign_id, address_id) VALUES (:e, :ws, :cid, :a)"
        ),
        {
            "e": str(enr_id),
            "ws": str(ws_id),
            "cid": str(camp_id),
            "a": str(addr_id),
        },
    )

    now = datetime.now(UTC)
    msg_step_1 = uuid.uuid4()
    msg_step_2 = uuid.uuid4()

    analytics_db.execute(
        text(
            """
            INSERT INTO public.messages (id, workspace_id, campaign_id, sequence_id, step_id, enrollment_id, mailbox_id, address_id, status, accepted_at)
            VALUES
                (:m1, :ws, :cid, :seq, :s1, :e, :mb, :a, 'SENT', :now),
                (:m2, :ws, :cid, :seq, :s2, :e, :mb, :a, 'SENT', :now)
            """
        ),
        {
            "m1": str(msg_step_1),
            "m2": str(msg_step_2),
            "ws": str(ws_id),
            "cid": str(camp_id),
            "seq": str(seq_id),
            "s1": str(step_1),
            "s2": str(step_2),
            "e": str(enr_id),
            "mb": str(mb_id),
            "a": str(addr_id),
            "now": now,
        },
    )

    # Inbound message that replied to Step 2!
    inbound_id = uuid.uuid4()
    analytics_db.execute(
        text(
            "INSERT INTO public.inbound_messages (id, workspace_id, mailbox_id) VALUES (:id, :ws, :mb)"
        ),
        {"id": str(inbound_id), "ws": str(ws_id), "mb": str(mb_id)},
    )
    analytics_db.execute(
        text(
            """
            INSERT INTO public.inbound_outreach_links (id, workspace_id, mailbox_id, inbound_message_id, outbound_message_id, campaign_id, enrollment_id, evidence_type, confidence, status, matched_at)
            VALUES (:id, :ws, :mb, :inb, :outb, :cid, :e, 'IN_REPLY_TO', 'HIGH', 'CONFIRMED', :now)
            """
        ),
        {
            "id": str(uuid.uuid4()),
            "ws": str(ws_id),
            "mb": str(mb_id),
            "inb": str(inbound_id),
            "outb": str(msg_step_2),  # Linked to msg_step_2!
            "cid": str(camp_id),
            "e": str(enr_id),
            "now": now,
        },
    )
    analytics_db.commit()

    steps = repo.get_sequence_analytics(ws_id, camp_id)
    assert steps is not None
    assert len(steps) == 2

    # Step 1 has 0 replies
    assert steps[0]["step_id"] == step_1
    assert steps[0]["replied"] == 0
    assert steps[0]["reply_rate"] == 0.0

    # Step 2 has 1 reply!
    assert steps[1]["step_id"] == step_2
    assert steps[1]["replied"] == 1
    assert steps[1]["reply_rate"] == 100.0


def test_deliverability_overview_and_warnings(analytics_db: Session) -> None:
    repo = AnalyticsRepository(analytics_db)
    ws_id = uuid.uuid4()
    mb_healthy = uuid.uuid4()
    mb_bouncing = uuid.uuid4()
    mb_disconnected = uuid.uuid4()

    analytics_db.execute(
        text("INSERT INTO public.workspaces (id, name) VALUES (:id, 'WS')"),
        {"id": str(ws_id)},
    )
    analytics_db.execute(
        text(
            """
            INSERT INTO public.mailboxes (id, workspace_id, original_address, provider, connection_state, health_state)
            VALUES
                (:m1, :ws, 'healthy@test.com', 'GMAIL', 'CONNECTED', 'HEALTHY'),
                (:m2, :ws, 'bouncing@test.com', 'SMTP', 'CONNECTED', 'HEALTHY'),
                (:m3, :ws, 'disco@test.com', 'MICROSOFT', 'DISCONNECTED', 'HEALTHY')
            """
        ),
        {
            "m1": str(mb_healthy),
            "m2": str(mb_bouncing),
            "m3": str(mb_disconnected),
            "ws": str(ws_id),
        },
    )

    now = datetime.now(UTC)
    # Mailbox 2: 25 sends, 3 bounces -> 12% bounce rate (> 5% threshold)
    for i in range(25):
        m_id = uuid.uuid4()
        e_id = uuid.uuid4()
        analytics_db.execute(
            text(
                "INSERT INTO public.messages (id, workspace_id, mailbox_id, enrollment_id, address_id, status, accepted_at) VALUES (:m, :ws, :mb, :e, :a, 'SENT', :now)"
            ),
            {
                "m": str(m_id),
                "ws": str(ws_id),
                "mb": str(mb_bouncing),
                "e": str(e_id),
                "a": str(uuid.uuid4()),
                "now": now,
            },
        )
        if i < 3:
            analytics_db.execute(
                text(
                    "INSERT INTO public.recipient_outcomes (id, workspace_id, enrollment_id, kind, source_key, occurred_at) VALUES (:o, :ws, :e, 'HARD_BOUNCE', :sk, :now)"
                ),
                {
                    "o": str(uuid.uuid4()),
                    "ws": str(ws_id),
                    "e": str(e_id),
                    "sk": f"b-{i}",
                    "now": now,
                },
            )

    # Seed failure breakdown in message_attempts
    for cat in ["RATE_LIMIT", "RATE_LIMIT", "AUTH_FAILURE"]:
        analytics_db.execute(
            text(
                """
                INSERT INTO public.message_attempts (id, workspace_id, message_id, mailbox_id, ordinal, evidence_state, error_category, started_at)
                VALUES (:id, :ws, :m, :mb, 1, 'REJECTED', :cat, :now)
                """
            ),
            {
                "id": str(uuid.uuid4()),
                "ws": str(ws_id),
                "m": str(uuid.uuid4()),
                "mb": str(mb_bouncing),
                "cat": cat,
                "now": now,
            },
        )

    analytics_db.commit()

    res = repo.get_deliverability_overview(
        ws_id, now - timedelta(days=7), now + timedelta(days=1)
    )
    assert res["total_sent"] == 25
    assert res["bounce_rate"] == 12.0
    assert len(res["warnings"]) >= 2

    # Check warnings
    codes = [w["code"] for w in res["warnings"]]
    assert "HIGH_BOUNCE_RATE" in codes
    assert "MAILBOX_CONNECTION_ISSUE" in codes

    # Check failure breakdown
    categories = {f["category"]: f["count"] for f in res["failure_breakdown"]}
    assert categories.get("RATE_LIMIT") == 2
    assert categories.get("AUTH_FAILURE") == 1
