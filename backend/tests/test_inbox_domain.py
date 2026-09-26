from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.api.deps import WorkspaceContext
from app.modules.inbox.repository import InboxRepository
from app.modules.inbox.schemas import ConversationFilter, MessageDirection
from app.modules.inbox.service import InboxService


@pytest.fixture
def db_session() -> Session:
    engine = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    session_factory = sessionmaker(bind=engine)
    session = session_factory()
    session.execute(text("ATTACH DATABASE ':memory:' AS public;"))

    session.execute(
        text(
            """
        CREATE TABLE public.sequence_steps (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            position INTEGER NOT NULL
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
            lead_id TEXT
        );
        """
        )
    )
    session.execute(
        text(
            """
        CREATE TABLE public.conversations (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            mailbox_id TEXT NOT NULL,
            provider_thread_id TEXT,
            local_anchor_id TEXT,
            campaign_summary_id TEXT,
            created_at TIMESTAMP,
            latest_activity_at TIMESTAMP,
            read_at TIMESTAMP,
            archived_at TIMESTAMP,
            version INTEGER NOT NULL DEFAULT 1,
            updated_at TIMESTAMP
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
            provider TEXT NOT NULL,
            connection_state TEXT NOT NULL DEFAULT 'CONNECTED',
            health_state TEXT NOT NULL DEFAULT 'HEALTHY',
            original_address TEXT NOT NULL,
            version INTEGER NOT NULL DEFAULT 1,
            updated_at TIMESTAMP
        );
        """
        )
    )

    session.execute(
        text(
            """
        CREATE TABLE public.mailbox_sync_states (
            id TEXT PRIMARY KEY,
            workspace_id TEXT NOT NULL,
            mailbox_id TEXT NOT NULL,
            connection_generation INTEGER NOT NULL DEFAULT 1,
            sync_scope TEXT NOT NULL DEFAULT 'INBOX',
            cursor_data TEXT,
            status TEXT NOT NULL DEFAULT 'HEALTHY',
            failure_count INTEGER NOT NULL DEFAULT 0,
            last_complete_at TIMESTAMP,
            version INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMP,
            updated_at TIMESTAMP,
            UNIQUE (workspace_id, mailbox_id, sync_scope)
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
            status TEXT NOT NULL DEFAULT 'RUNNING',
            created_at TIMESTAMP,
            updated_at TIMESTAMP
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
            mailbox_id TEXT NOT NULL,
            conversation_id TEXT NOT NULL,
            connection_generation INTEGER NOT NULL DEFAULT 1,
            provider_message_id TEXT NOT NULL,
            rfc_message_id TEXT,
            in_reply_to TEXT,
            references_header TEXT,
            participants TEXT NOT NULL DEFAULT '{}',
            subject TEXT,
            content_text TEXT,
            received_at TIMESTAMP NOT NULL,
            observed_at TIMESTAMP NOT NULL,
            direction TEXT NOT NULL DEFAULT 'INBOUND',
            classification TEXT,
            association_status TEXT NOT NULL DEFAULT 'UNRESOLVED',
            version INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMP,
            updated_at TIMESTAMP
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
            mailbox_id TEXT NOT NULL,
            campaign_id TEXT,
            enrollment_id TEXT,
            sequence_id TEXT,
            step_id TEXT,
            conversation_id TEXT,
            rfc_message_id TEXT,
            content_subject TEXT,
            content_body_html TEXT,
            frozen_destination TEXT,
            frozen_sender_address TEXT,
            frozen_sender_name TEXT,
            status TEXT NOT NULL DEFAULT 'SENT',
            accepted_at TIMESTAMP,
            created_at TIMESTAMP,
            updated_at TIMESTAMP
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
            status TEXT NOT NULL DEFAULT 'CONFIRMED',
            matched_at TIMESTAMP,
            version INTEGER NOT NULL DEFAULT 1,
            created_at TIMESTAMP,
            updated_at TIMESTAMP
        );
        """
        )
    )

    session.commit()
    yield session
    session.close()


def _seed_mailbox(session: Session, ws_id: uuid.UUID, provider: str = "GMAIL", addr: str = "sales@company.com") -> uuid.UUID:
    mb_id = uuid.uuid4()
    session.execute(
        text(
            """
            INSERT INTO public.mailboxes (id, workspace_id, provider, connection_state, health_state, original_address)
            VALUES (:id, :ws, :prov, 'CONNECTED', 'HEALTHY', :addr)
            """
        ),
        {"id": str(mb_id), "ws": str(ws_id), "prov": provider, "addr": addr},
    )
    session.execute(
        text(
            """
            INSERT INTO public.mailbox_sync_states (id, workspace_id, mailbox_id, status, last_complete_at)
            VALUES (:id, :ws, :mbid, 'HEALTHY', :now)
            """
        ),
        {"id": str(uuid.uuid4()), "ws": str(ws_id), "mbid": str(mb_id), "now": datetime.now(UTC)},
    )
    session.commit()
    return mb_id


def _seed_campaign(session: Session, ws_id: uuid.UUID, name: str = "Outreach Q3") -> uuid.UUID:
    camp_id = uuid.uuid4()
    session.execute(
        text(
            """
            INSERT INTO public.campaigns (id, workspace_id, name)
            VALUES (:id, :ws, :name)
            """
        ),
        {"id": str(camp_id), "ws": str(ws_id), "name": name},
    )
    session.commit()
    return camp_id


class TestInboxDomain:
    def test_keyset_cursor_pagination_and_ordering(self, db_session: Session) -> None:
        ws_id = uuid.uuid4()
        mb_id = _seed_mailbox(db_session, ws_id)
        now = datetime.now(UTC)

        # Create 15 conversations spaced 1 minute apart
        conv_ids = []
        for i in range(15):
            cid = uuid.uuid4()
            conv_ids.append(cid)
            act_time = now - timedelta(minutes=15 - i)
            db_session.execute(
                text(
                    """
                    INSERT INTO public.conversations (id, workspace_id, mailbox_id, latest_activity_at, created_at)
                    VALUES (:id, :ws, :mbid, :act, :act)
                    """
                ),
                {"id": str(cid), "ws": str(ws_id), "mbid": str(mb_id), "act": act_time},
            )
            # Add an inbound message to each
            db_session.execute(
                text(
                    """
                    INSERT INTO public.inbound_messages (id, workspace_id, mailbox_id, conversation_id, provider_message_id, subject, content_text, received_at, observed_at)
                    VALUES (:id, :ws, :mbid, :cid, :pmid, :sub, :txt, :act, :act)
                    """
                ),
                {
                    "id": str(uuid.uuid4()),
                    "ws": str(ws_id),
                    "mbid": str(mb_id),
                    "cid": str(cid),
                    "pmid": f"prov-{i}",
                    "sub": f"Subject {i}",
                    "txt": f"Message body content {i}",
                    "act": act_time,
                },
            )
        db_session.commit()

        context = WorkspaceContext(workspace_id=ws_id, user_id=uuid.uuid4(), role_code="MEMBER")
        service = InboxService(db_session)

        # Fetch page 1 with limit 5
        page1 = service.list_conversations(context, limit=5)
        assert len(page1.items) == 5
        assert page1.has_more is True
        assert page1.next_cursor is not None
        # Must be descending by latest_activity_at
        assert page1.items[0].latest_activity_at >= page1.items[1].latest_activity_at
        assert page1.items[0].subject == "Subject 14"

        # Fetch page 2 using cursor
        page2 = service.list_conversations(context, limit=5, cursor=page1.next_cursor)
        assert len(page2.items) == 5
        assert page2.has_more is True
        assert page2.items[0].subject == "Subject 9"

        # Fetch page 3
        page3 = service.list_conversations(context, limit=5, cursor=page2.next_cursor)
        assert len(page3.items) == 5
        assert page3.has_more is False
        assert page3.items[0].subject == "Subject 4"
        assert page3.items[-1].subject == "Subject 0"

        # Verify no duplicate IDs across all 3 pages
        all_ids = [item.id for item in page1.items + page2.items + page3.items]
        assert len(set(all_ids)) == 15

    def test_filter_unread_and_read(self, db_session: Session) -> None:
        ws_id = uuid.uuid4()
        mb_id = _seed_mailbox(db_session, ws_id)
        now = datetime.now(UTC)

        cid_unread = uuid.uuid4()
        cid_read = uuid.uuid4()

        # Unread conversation (read_at is NULL)
        db_session.execute(
            text(
                """
                INSERT INTO public.conversations (id, workspace_id, mailbox_id, latest_activity_at, read_at)
                VALUES (:id, :ws, :mbid, :act, NULL)
                """
            ),
            {"id": str(cid_unread), "ws": str(ws_id), "mbid": str(mb_id), "act": now},
        )

        # Read conversation (read_at >= latest_activity_at)
        db_session.execute(
            text(
                """
                INSERT INTO public.conversations (id, workspace_id, mailbox_id, latest_activity_at, read_at)
                VALUES (:id, :ws, :mbid, :act, :read)
                """
            ),
            {"id": str(cid_read), "ws": str(ws_id), "mbid": str(mb_id), "act": now - timedelta(hours=1), "read": now},
        )
        db_session.commit()

        context = WorkspaceContext(workspace_id=ws_id, user_id=uuid.uuid4(), role_code="MEMBER")
        service = InboxService(db_session)

        # Filter UNREAD
        unread_page = service.list_conversations(context, filter_mode=ConversationFilter.UNREAD)
        assert len(unread_page.items) == 1
        assert unread_page.items[0].id == cid_unread
        assert unread_page.items[0].is_read is False

        # Filter READ
        read_page = service.list_conversations(context, filter_mode=ConversationFilter.READ)
        assert len(read_page.items) == 1
        assert read_page.items[0].id == cid_read
        assert read_page.items[0].is_read is True

    def test_mark_read_and_unread_idempotency_and_auto_unread(self, db_session: Session) -> None:
        ws_id = uuid.uuid4()
        mb_id = _seed_mailbox(db_session, ws_id)
        now = datetime.now(UTC)
        cid = uuid.uuid4()

        db_session.execute(
            text(
                """
                INSERT INTO public.conversations (id, workspace_id, mailbox_id, latest_activity_at, read_at)
                VALUES (:id, :ws, :mbid, :act, NULL)
                """
            ),
            {"id": str(cid), "ws": str(ws_id), "mbid": str(mb_id), "act": now - timedelta(hours=2)},
        )
        db_session.commit()

        context = WorkspaceContext(workspace_id=ws_id, user_id=uuid.uuid4(), role_code="MEMBER")
        service = InboxService(db_session)

        # 1. Mark read
        res1 = service.mark_read(context, cid)
        assert res1.is_read is True
        assert res1.read_at is not None

        # 2. Mark read again (idempotent)
        res2 = service.mark_read(context, cid)
        assert res2.is_read is True

        # 3. New reply arrives: latest_activity_at advances beyond read_at
        newer_act = datetime.now(UTC) + timedelta(minutes=10)
        db_session.execute(
            text(
                """
                UPDATE public.conversations
                SET latest_activity_at = :act
                WHERE id = :cid
                """
            ),
            {"act": newer_act, "cid": str(cid)},
        )
        db_session.commit()

        # Conversation must automatically be recognized as unread without manual intervention
        detail = service.get_conversation(context, cid)
        assert detail.is_read is False

        # 4. Mark unread explicitly
        res_unread = service.mark_unread(context, cid)
        assert res_unread.is_read is False
        assert res_unread.read_at is None

        # 5. Mark unread again (idempotent)
        res_unread2 = service.mark_unread(context, cid)
        assert res_unread2.is_read is False

    def test_archive_and_unarchive_idempotency(self, db_session: Session) -> None:
        ws_id = uuid.uuid4()
        mb_id = _seed_mailbox(db_session, ws_id)
        now = datetime.now(UTC)
        cid = uuid.uuid4()

        db_session.execute(
            text(
                """
                INSERT INTO public.conversations (id, workspace_id, mailbox_id, latest_activity_at, archived_at)
                VALUES (:id, :ws, :mbid, :act, NULL)
                """
            ),
            {"id": str(cid), "ws": str(ws_id), "mbid": str(mb_id), "act": now},
        )
        db_session.commit()

        context = WorkspaceContext(workspace_id=ws_id, user_id=uuid.uuid4(), role_code="MANAGER")
        service = InboxService(db_session)

        # 1. Conversation appears in ALL filter
        page_before = service.list_conversations(context, filter_mode=ConversationFilter.ALL)
        assert any(item.id == cid for item in page_before.items)

        # 2. Archive
        arch_res = service.archive(context, cid)
        assert arch_res.archived_at is not None

        # 3. Archive again (idempotent)
        arch_res2 = service.archive(context, cid)
        assert arch_res2.archived_at is not None

        # 4. No longer in ALL, but present in ARCHIVED
        page_all = service.list_conversations(context, filter_mode=ConversationFilter.ALL)
        assert not any(item.id == cid for item in page_all.items)

        page_archived = service.list_conversations(context, filter_mode=ConversationFilter.ARCHIVED)
        assert any(item.id == cid for item in page_archived.items)

        # 5. Unarchive
        unarch_res = service.unarchive(context, cid)
        assert unarch_res.archived_at is None

        # 6. Returns to ALL
        page_after = service.list_conversations(context, filter_mode=ConversationFilter.ALL)
        assert any(item.id == cid for item in page_after.items)

    def test_search_conversations(self, db_session: Session) -> None:
        ws_id = uuid.uuid4()
        mb_id = _seed_mailbox(db_session, ws_id)
        camp_id = _seed_campaign(db_session, ws_id, name="Acme Partnership")
        now = datetime.now(UTC)

        cid1 = uuid.uuid4()
        cid2 = uuid.uuid4()

        # Conversation 1: linked to Acme campaign
        db_session.execute(
            text(
                """
                INSERT INTO public.conversations (id, workspace_id, mailbox_id, campaign_summary_id, latest_activity_at)
                VALUES (:id, :ws, :mbid, :camp, :act)
                """
            ),
            {"id": str(cid1), "ws": str(ws_id), "mbid": str(mb_id), "camp": str(camp_id), "act": now},
        )
        db_session.execute(
            text(
                """
                INSERT INTO public.inbound_messages (id, workspace_id, mailbox_id, conversation_id, provider_message_id, subject, participants, received_at, observed_at)
                VALUES (:id, :ws, :mbid, :cid, :pmid, 'Re: Acme Partnership Proposal', :parts, :act, :act)
                """
            ),
            {
                "id": str(uuid.uuid4()),
                "ws": str(ws_id),
                "mbid": str(mb_id),
                "cid": str(cid1),
                "pmid": "pmid-1",
                "parts": json.dumps({"from": {"email": "sarah@acmecorp.com", "name": "Sarah Connor"}}),
                "act": now,
            },
        )

        # Conversation 2: unrelated
        db_session.execute(
            text(
                """
                INSERT INTO public.conversations (id, workspace_id, mailbox_id, latest_activity_at)
                VALUES (:id, :ws, :mbid, :act)
                """
            ),
            {"id": str(cid2), "ws": str(ws_id), "mbid": str(mb_id), "act": now - timedelta(hours=1)},
        )
        db_session.execute(
            text(
                """
                INSERT INTO public.inbound_messages (id, workspace_id, mailbox_id, conversation_id, provider_message_id, subject, participants, received_at, observed_at)
                VALUES (:id, :ws, :mbid, :cid, :pmid, 'Product Inquiry', :parts, :act, :act)
                """
            ),
            {
                "id": str(uuid.uuid4()),
                "ws": str(ws_id),
                "mbid": str(mb_id),
                "cid": str(cid2),
                "pmid": "pmid-2",
                "parts": json.dumps({"from": {"email": "john@othercorp.com", "name": "John Doe"}}),
                "act": now - timedelta(hours=1),
            },
        )
        db_session.commit()

        context = WorkspaceContext(workspace_id=ws_id, user_id=uuid.uuid4(), role_code="MEMBER")
        service = InboxService(db_session)

        # Search by email
        res_email = service.list_conversations(context, search_query="sarah@acmecorp.com")
        assert len(res_email.items) == 1
        assert res_email.items[0].id == cid1

        # Search by name
        res_name = service.list_conversations(context, search_query="Connor")
        assert len(res_name.items) == 1
        assert res_name.items[0].id == cid1

        # Search by campaign name
        res_camp = service.list_conversations(context, search_query="Acme")
        assert len(res_camp.items) == 1
        assert res_camp.items[0].id == cid1

    def test_conversation_detail_thread_reconstruction_and_html_sanitizer(self, db_session: Session) -> None:
        ws_id = uuid.uuid4()
        mb_id = _seed_mailbox(db_session, ws_id)
        now = datetime.now(UTC)
        cid = uuid.uuid4()

        db_session.execute(
            text(
                """
                INSERT INTO public.conversations (id, workspace_id, mailbox_id, latest_activity_at)
                VALUES (:id, :ws, :mbid, :act)
                """
            ),
            {"id": str(cid), "ws": str(ws_id), "mbid": str(mb_id), "act": now},
        )

        # 1. Outbound message sent at T-2h with malicious HTML attempt
        outbound_id = uuid.uuid4()
        raw_html = "<p>Hi Alice!</p><script>alert('xss')</script><a href='javascript:stealCookies()'>Click</a>"
        db_session.execute(
            text(
                """
                INSERT INTO public.messages (id, workspace_id, mailbox_id, conversation_id, content_subject, content_body_html, frozen_destination, frozen_sender_address, created_at, accepted_at)
                VALUES (:id, :ws, :mbid, :cid, 'Intro to Platform', :html, 'alice@client.com', 'rep@company.com', :t, :t)
                """
            ),
            {"id": str(outbound_id), "ws": str(ws_id), "mbid": str(mb_id), "cid": str(cid), "html": raw_html, "t": now - timedelta(hours=2)},
        )

        # 2. Inbound reply received at T-1h
        inbound_id = uuid.uuid4()
        db_session.execute(
            text(
                """
                INSERT INTO public.inbound_messages (id, workspace_id, mailbox_id, conversation_id, provider_message_id, subject, content_text, participants, association_status, received_at, observed_at)
                VALUES (:id, :ws, :mbid, :cid, 'prov-reply-1', 'Re: Intro to Platform', 'Thanks, let us chat tomorrow.', :parts, 'MATCHED', :t, :t)
                """
            ),
            {
                "id": str(inbound_id),
                "ws": str(ws_id),
                "mbid": str(mb_id),
                "cid": str(cid),
                "parts": json.dumps({"from": {"email": "alice@client.com", "name": "Alice Smith"}}),
                "t": now - timedelta(hours=1),
            },
        )
        db_session.commit()

        context = WorkspaceContext(workspace_id=ws_id, user_id=uuid.uuid4(), role_code="MEMBER")
        service = InboxService(db_session)

        detail = service.get_conversation(context, cid)
        assert detail.id == cid
        assert detail.participant_email == "alice@client.com"
        assert detail.reply_status == "MATCHED"
        assert len(detail.messages) == 2

        # Message 1: Outbound
        msg1 = detail.messages[0]
        assert msg1.direction == MessageDirection.OUTBOUND
        assert msg1.recipient_email == "alice@client.com"
        assert msg1.content_html is not None
        # Verify script and javascript: href are stripped
        assert "<script>" not in msg1.content_html
        assert "javascript:" not in msg1.content_html
        assert "<p>Hi Alice!</p>" in msg1.content_html

        # Message 2: Inbound
        msg2 = detail.messages[1]
        assert msg2.direction == MessageDirection.INBOUND
        assert msg2.sender_email == "alice@client.com"
        assert msg2.content_text == "Thanks, let us chat tomorrow."
        assert msg2.association_status == "MATCHED"

    def test_sync_status_reporting(self, db_session: Session) -> None:
        ws_id = uuid.uuid4()
        mb1 = _seed_mailbox(db_session, ws_id, provider="GMAIL", addr="gmail@company.com")
        mb2 = _seed_mailbox(db_session, ws_id, provider="SMTP", addr="smtp@company.com")

        context = WorkspaceContext(workspace_id=ws_id, user_id=uuid.uuid4(), role_code="MEMBER")
        service = InboxService(db_session)

        status_res = service.get_sync_status(context)
        assert len(status_res.mailboxes) == 2

        gmail_status = next(m for m in status_res.mailboxes if m.provider == "GMAIL")
        assert gmail_status.email_address == "gmail@company.com"
        assert gmail_status.sync_status == "HEALTHY"

        smtp_status = next(m for m in status_res.mailboxes if m.provider == "SMTP")
        assert smtp_status.email_address == "smtp@company.com"
        assert smtp_status.sync_status == "NOT_SUPPORTED"
