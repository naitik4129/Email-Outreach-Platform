from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session, sessionmaker

from app.modules.mailboxes.providers.base import (
    EmailProvider,
    ProviderCapability,
    ProviderInboundMessage,
    SyncPageResult,
    TokenExchangeResult,
)
from app.modules.replies.matcher import (
    OutboundMessageCandidate,
    ReplyMatcher,
)
from app.modules.replies.normalizer import (
    classify_inbound_message,
    extract_rfc_message_ids,
    extract_text_from_html,
    normalize_inbound_message,
)
from app.modules.replies.repository import ReplyRepository
from app.modules.replies.schemas import (
    InboundClassification,
    MailboxSyncResult,
    NormalizedInboundMessage,
    ReplyConfidence,
    ReplyEvidenceType,
    SyncCheckpoint,
)
from app.modules.replies.service import ReplySyncService
from app.modules.sending import gates
from app.modules.sending.schemas import LoadedSendContext


# =============================================================================
# 1. Normalizer and RFC Header Parser Tests
# =============================================================================


class TestNormalizerAndClassification:
    def test_extract_text_from_html_cleans_tags_and_scripts(self) -> None:
        html = """
        <html>
            <head><style>body { color: red; }</style></head>
            <body>
                <script>alert('malicious');</script>
                <p>Hello <b>World</b>!</p>
                <div>Let's schedule a call for <i>Tuesday</i>.</div>
                <!-- comment -->
                <a href="https://example.com">Click here</a>
            </body>
        </html>
        """
        extracted = extract_text_from_html(html)
        assert "Hello World!" in extracted
        assert "Let's schedule a call for Tuesday." in extracted
        assert "Click here" in extracted
        assert "alert" not in extracted
        assert "color: red" not in extracted
        assert "<!--" not in extracted

    def test_extract_rfc_message_ids_handles_various_formats(self) -> None:
        header = "<msg1@domain.com>  <msg2@domain.com>, msg3@domain.com"
        ids = extract_rfc_message_ids(header)
        assert ids == ["<msg1@domain.com>", "<msg2@domain.com>", "<msg3@domain.com>"]

    def test_extract_rfc_message_ids_empty(self) -> None:
        assert extract_rfc_message_ids(None) == []
        assert extract_rfc_message_ids("") == []
        assert extract_rfc_message_ids("   ") == []

    def test_classify_inbound_message_human_reply(self) -> None:
        body = "Sounds interesting! Let's talk tomorrow at 3pm."
        subject = "Re: Quick question"
        headers = {}
        classification = classify_inbound_message(subject=subject, headers=headers, content_text=body)
        assert classification == InboundClassification.HUMAN_REPLY.value

    @pytest.mark.parametrize(
        "phrase",
        [
            "I am currently out of the office with limited access to email",
            "Automatic reply: I will return on Monday",
            "I am away from the office on vacation",
            "This is an automated response acknowledging your message",
        ],
    )
    def test_classify_inbound_message_out_of_office(self, phrase: str) -> None:
        body = f"Hello, {phrase}."
        subject = "Auto: Out of Office"
        headers = {}
        classification = classify_inbound_message(subject=subject, headers=headers, content_text=body)
        assert classification == InboundClassification.OUT_OF_OFFICE.value

    def test_classify_inbound_message_auto_submitted_header(self) -> None:
        body = "Thank you for contacting us."
        subject = "Support Ticket Received"
        headers = {"auto-submitted": "auto-replied"}
        classification = classify_inbound_message(subject=subject, headers=headers, content_text=body)
        assert classification in (
            InboundClassification.OUT_OF_OFFICE.value,
            InboundClassification.AUTOMATED.value,
        )

    def test_normalize_inbound_message_strips_control_characters(self) -> None:
        raw = ProviderInboundMessage(
            provider_message_id="msg_123",
            provider_thread_id="th_456",
            rfc_message_id="<inbound@customer.com>",
            in_reply_to="<outbound1@platform.com>\r\n",
            references=["<root@platform.com>", "<outbound1@platform.com>"],
            from_address="Alice Smith <alice@customer.com>\n",
            to_addresses=["rep@mycompany.com"],
            subject="Re: Quick intro\r\n",
            body_text="Let's chat!",
            body_html=None,
            received_at=datetime.now(UTC),
            headers={"X-Test": "value\r\n"},
        )
        normalized = normalize_inbound_message(raw, provider="GMAIL")
        assert "\r" not in normalized.from_address
        assert "\n" not in normalized.from_address
        assert normalized.from_address == "alice@customer.com"
        assert "\r" not in normalized.subject
        assert "\n" not in normalized.subject
        assert normalized.in_reply_to == "<outbound1@platform.com>"


# =============================================================================
# 2. Conservative Reply Matcher Tests
# =============================================================================


class TestReplyMatcher:
    @pytest.fixture
    def outbound_candidate(self) -> OutboundMessageCandidate:
        return OutboundMessageCandidate(
            message_id=uuid.uuid4(),
            campaign_id=uuid.uuid4(),
            enrollment_id=uuid.uuid4(),
            mailbox_id=uuid.uuid4(),
            rfc_message_id="<outbound-step1@platform.com>",
            provider_message_id="prov_out_1",
            provider_thread_id="thread_shared_123",
            frozen_destination="prospect@company.com",
        )

    def test_exact_in_reply_to_match_with_sender_corroboration(
        self, outbound_candidate: OutboundMessageCandidate
    ) -> None:
        inbound = NormalizedInboundMessage(
            provider="GMAIL",
            provider_message_id="in_1",
            provider_thread_id="thread_shared_123",
            rfc_message_id="<inbound-reply@company.com>",
            in_reply_to="<outbound-step1@platform.com>",
            references=["<outbound-step1@platform.com>"],
            from_address="prospect@company.com",
            from_name="Prospect",
            to_addresses=["rep@outreach.com"],
            cc_addresses=[],
            bcc_addresses=[],
            subject="Re: Partnership",
            content_text="I am interested, let's talk.",
            content_html=None,
            received_at=datetime.now(UTC),
        )
        result = ReplyMatcher.match(inbound, [outbound_candidate])
        assert result.is_matched is True
        assert result.association_status == "MATCHED"
        assert result.matched_candidate is not None
        assert result.matched_candidate.outbound_message_id == outbound_candidate.message_id
        assert result.matched_candidate.campaign_id == outbound_candidate.campaign_id
        assert result.matched_candidate.enrollment_id == outbound_candidate.enrollment_id
        assert result.matched_candidate.evidence_type == ReplyEvidenceType.RFC_HEADER_MATCH.value
        assert result.matched_candidate.confidence == ReplyConfidence.HIGH.value

    def test_references_match_when_in_reply_to_missing(
        self, outbound_candidate: OutboundMessageCandidate
    ) -> None:
        inbound = NormalizedInboundMessage(
            provider="MICROSOFT",
            provider_message_id="in_2",
            provider_thread_id="thread_other",
            rfc_message_id="<inbound2@company.com>",
            in_reply_to=None,
            references=["<outbound-step1@platform.com>"],
            from_address="prospect@company.com",
            from_name="Prospect",
            to_addresses=["rep@outreach.com"],
            cc_addresses=[],
            bcc_addresses=[],
            subject="Re: Partnership",
            content_text="Yes, call me.",
            content_html=None,
            received_at=datetime.now(UTC),
        )
        result = ReplyMatcher.match(inbound, [outbound_candidate])
        assert result.is_matched is True
        assert result.association_status == "MATCHED"
        assert result.matched_candidate is not None
        assert result.matched_candidate.outbound_message_id == outbound_candidate.message_id

    def test_fallback_provider_thread_corroboration(
        self, outbound_candidate: OutboundMessageCandidate
    ) -> None:
        inbound = NormalizedInboundMessage(
            provider="GMAIL",
            provider_message_id="in_3",
            provider_thread_id="thread_shared_123",
            rfc_message_id="<inbound3@company.com>",
            in_reply_to=None,
            references=[],
            from_address="prospect@company.com",
            from_name="Prospect",
            to_addresses=["rep@outreach.com"],
            cc_addresses=[],
            bcc_addresses=[],
            subject="Re: Partnership",
            content_text="Thanks",
            content_html=None,
            received_at=datetime.now(UTC),
        )
        result = ReplyMatcher.match(inbound, [outbound_candidate])
        assert result.is_matched is True
        assert result.matched_candidate is not None
        assert (
            result.matched_candidate.evidence_type
            == ReplyEvidenceType.PROVIDER_THREAD_CORROBORATED.value
        )
        assert result.matched_candidate.confidence == ReplyConfidence.MEDIUM.value

    def test_sender_mismatch_prevents_match(
        self, outbound_candidate: OutboundMessageCandidate
    ) -> None:
        # Inbound references the outbound message RFC ID, but comes from an unrelated sender
        inbound = NormalizedInboundMessage(
            provider="GMAIL",
            provider_message_id="in_4",
            provider_thread_id="thread_shared_123",
            rfc_message_id="<inbound4@random.com>",
            in_reply_to="<outbound-step1@platform.com>",
            references=["<outbound-step1@platform.com>"],
            from_address="unrelated_attacker@random.com",
            from_name="Random",
            to_addresses=["rep@outreach.com"],
            cc_addresses=[],
            bcc_addresses=[],
            subject="Re: Partnership",
            content_text="Hey",
            content_html=None,
            received_at=datetime.now(UTC),
        )
        result = ReplyMatcher.match(inbound, [outbound_candidate])
        assert result.is_matched is False
        assert result.association_status == "UNRESOLVED"
        assert result.matched_candidate is None

    def test_multiple_campaigns_same_recipient_isolated_stopping(self) -> None:
        # Recipient is enrolled in Campaign A AND Campaign B
        camp_a_msg = OutboundMessageCandidate(
            message_id=uuid.uuid4(),
            campaign_id=uuid.uuid4(),
            enrollment_id=uuid.uuid4(),
            mailbox_id=uuid.uuid4(),
            rfc_message_id="<camp-a-step1@platform.com>",
            provider_message_id="out_a",
            provider_thread_id="thread_a",
            frozen_destination="lead@domain.com",
        )
        camp_b_msg = OutboundMessageCandidate(
            message_id=uuid.uuid4(),
            campaign_id=uuid.uuid4(),
            enrollment_id=uuid.uuid4(),
            mailbox_id=uuid.uuid4(),
            rfc_message_id="<camp-b-step1@platform.com>",
            provider_message_id="out_b",
            provider_thread_id="thread_b",
            frozen_destination="lead@domain.com",
        )

        # Inbound specifically replies to Campaign A's RFC ID
        inbound = NormalizedInboundMessage(
            provider="GMAIL",
            provider_message_id="in_reply_a",
            provider_thread_id="thread_a",
            rfc_message_id="<lead-reply@domain.com>",
            in_reply_to="<camp-a-step1@platform.com>",
            references=["<camp-a-step1@platform.com>"],
            from_address="lead@domain.com",
            from_name="Lead",
            to_addresses=["rep@outreach.com"],
            cc_addresses=[],
            bcc_addresses=[],
            subject="Re: Campaign A",
            content_text="Stop emailing me about Product A",
            content_html=None,
            received_at=datetime.now(UTC),
        )

        result = ReplyMatcher.match(inbound, [camp_a_msg, camp_b_msg])
        assert result.is_matched is True
        # Matched specifically to Campaign A, not Campaign B!
        assert result.matched_candidate.campaign_id == camp_a_msg.campaign_id
        assert result.matched_candidate.enrollment_id == camp_a_msg.enrollment_id
        assert result.matched_candidate.campaign_id != camp_b_msg.campaign_id

    def test_conflicting_ambiguous_campaigns_marked_unresolved(self) -> None:
        # Inbound references BOTH Campaign A and Campaign B equally (or thread collision)
        camp_a_msg = OutboundMessageCandidate(
            message_id=uuid.uuid4(),
            campaign_id=uuid.uuid4(),
            enrollment_id=uuid.uuid4(),
            mailbox_id=uuid.uuid4(),
            rfc_message_id="<step-a@platform.com>",
            provider_message_id="out_a",
            provider_thread_id="thread_shared",
            frozen_destination="lead@domain.com",
        )
        camp_b_msg = OutboundMessageCandidate(
            message_id=uuid.uuid4(),
            campaign_id=uuid.uuid4(),
            enrollment_id=uuid.uuid4(),
            mailbox_id=uuid.uuid4(),
            rfc_message_id="<step-b@platform.com>",
            provider_message_id="out_b",
            provider_thread_id="thread_shared",
            frozen_destination="lead@domain.com",
        )

        # No in_reply_to, but references contains BOTH
        inbound = NormalizedInboundMessage(
            provider="GMAIL",
            provider_message_id="in_ambiguous",
            provider_thread_id="thread_shared",
            rfc_message_id="<ambiguous-reply@domain.com>",
            in_reply_to=None,
            references=["<step-a@platform.com>", "<step-b@platform.com>"],
            from_address="lead@domain.com",
            from_name="Lead",
            to_addresses=["rep@outreach.com"],
            cc_addresses=[],
            bcc_addresses=[],
            subject="Re: Ambiguous",
            content_text="Unclear reply",
            content_html=None,
            received_at=datetime.now(UTC),
        )

        result = ReplyMatcher.match(inbound, [camp_a_msg, camp_b_msg])
        # Conservative matching: do not guess between conflicting campaigns!
        assert result.is_matched is False
        assert result.association_status == "UNRESOLVED"
        assert len(result.candidates) == 2

    def test_never_match_by_subject_alone(
        self, outbound_candidate: OutboundMessageCandidate
    ) -> None:
        inbound = NormalizedInboundMessage(
            provider="GMAIL",
            provider_message_id="in_no_headers",
            provider_thread_id=None,
            rfc_message_id="<random-rfc@company.com>",
            in_reply_to=None,
            references=[],
            from_address="prospect@company.com",
            from_name="Prospect",
            to_addresses=["rep@outreach.com"],
            cc_addresses=[],
            bcc_addresses=[],
            subject="Partnership discussion",  # Subject matches candidate's topic, but no headers/thread
            content_text="Let's connect",
            content_html=None,
            received_at=datetime.now(UTC),
        )
        result = ReplyMatcher.match(inbound, [outbound_candidate])
        assert result.is_matched is False
        assert result.association_status == "UNRESOLVED"
        assert result.matched_candidate is None


# =============================================================================
# 3. Reply Repository SQLite In-Memory Tests
# =============================================================================


class TestReplyRepository:
    @pytest.fixture
    def sqlite_session(self) -> Session:
        from sqlalchemy.pool import StaticPool

        engine = create_engine(
            "sqlite://",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        session_factory = sessionmaker(bind=engine)
        session = session_factory()
        session.execute(text("ATTACH DATABASE ':memory:' AS public;"))

        # Create minimal required tables in SQLite for testing repository queries
        session.execute(
            text(
                """
            CREATE TABLE public.mailbox_sync_states (
                id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(16)))),
                workspace_id TEXT NOT NULL,
                mailbox_id TEXT NOT NULL,
                connection_generation INTEGER NOT NULL,
                sync_scope TEXT NOT NULL,
                cursor_data TEXT,
                lease_owner TEXT,
                lease_generation INTEGER NOT NULL DEFAULT 1,
                lease_expires_at TIMESTAMP,
                next_due_at TIMESTAMP,
                last_complete_at TIMESTAMP,
                status TEXT NOT NULL DEFAULT 'INITIALIZING',
                failure_count INTEGER NOT NULL DEFAULT 0,
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
            CREATE TABLE public.conversations (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                mailbox_id TEXT NOT NULL,
                provider_thread_id TEXT,
                local_anchor_id TEXT,
                campaign_summary_id TEXT,
                created_at TIMESTAMP,
                latest_activity_at TIMESTAMP,
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
            CREATE TABLE public.messages (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                mailbox_id TEXT NOT NULL,
                campaign_id TEXT,
                enrollment_id TEXT,
                conversation_id TEXT,
                rfc_message_id TEXT,
                provider_message_id TEXT,
                frozen_destination TEXT,
                status TEXT NOT NULL DEFAULT 'PLANNED',
                terminal_reason TEXT,
                purpose TEXT NOT NULL DEFAULT 'CAMPAIGN',
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
            CREATE TABLE public.inbound_messages (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                mailbox_id TEXT NOT NULL,
                conversation_id TEXT NOT NULL,
                connection_generation INTEGER NOT NULL,
                provider_message_id TEXT NOT NULL,
                rfc_message_id TEXT,
                in_reply_to TEXT,
                references_header TEXT,
                participants TEXT,
                subject TEXT,
                content_text TEXT,
                received_at TIMESTAMP,
                observed_at TIMESTAMP,
                direction TEXT NOT NULL DEFAULT 'INBOUND',
                classification TEXT,
                association_status TEXT NOT NULL DEFAULT 'UNRESOLVED',
                version INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMP,
                updated_at TIMESTAMP,
                UNIQUE (workspace_id, mailbox_id, provider_message_id)
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
                status TEXT NOT NULL DEFAULT 'CANDIDATE',
                matched_at TIMESTAMP,
                version INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMP,
                updated_at TIMESTAMP,
                UNIQUE (workspace_id, inbound_message_id, outbound_message_id)
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
                observed_at TIMESTAMP NOT NULL,
                inbound_message_id TEXT,
                created_at TIMESTAMP,
                UNIQUE (workspace_id, enrollment_id, kind, source_key)
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
                state TEXT NOT NULL DEFAULT 'ACTIVE',
                stop_reason TEXT,
                next_step_id TEXT,
                version INTEGER NOT NULL DEFAULT 1,
                updated_at TIMESTAMP
            );
            """
            )
        )
        session.execute(
            text(
                """
            CREATE TABLE public.domain_events (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                event_type TEXT NOT NULL,
                aggregate_type TEXT NOT NULL,
                aggregate_id TEXT NOT NULL,
                semantic_key TEXT NOT NULL,
                occurred_at TIMESTAMP NOT NULL,
                recorded_at TIMESTAMP NOT NULL,
                payload TEXT NOT NULL,
                UNIQUE (workspace_id, event_type, semantic_key)
            );
            """
            )
        )
        session.execute(
            text(
                """
            CREATE TABLE public.outbox_work (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                resource_id TEXT NOT NULL,
                resource_type TEXT NOT NULL,
                event_id TEXT,
                semantic_key TEXT NOT NULL,
                created_at TIMESTAMP,
                available_at TIMESTAMP,
                UNIQUE (workspace_id, kind, semantic_key)
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
                current_connection_generation INTEGER NOT NULL DEFAULT 1,
                connected_generation INTEGER NOT NULL DEFAULT 1,
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
            CREATE TABLE public.mailbox_connections (
                id TEXT PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                mailbox_id TEXT NOT NULL,
                generation INTEGER NOT NULL,
                credential_ciphertext BLOB,
                encryption_key_id TEXT,
                nonce BLOB,
                auth_mechanism TEXT NOT NULL DEFAULT 'OAUTH',
                granted_scopes TEXT NOT NULL DEFAULT '[]',
                protected_config TEXT,
                expires_at TIMESTAMP,
                version INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMP,
                updated_at TIMESTAMP,
                UNIQUE (workspace_id, mailbox_id, generation)
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
                source_work_identity TEXT NOT NULL,
                target_kind TEXT NOT NULL,
                target_mailbox_id TEXT,
                status TEXT NOT NULL DEFAULT 'ACTIVE',
                reason TEXT NOT NULL,
                created_at TIMESTAMP,
                resolved_at TIMESTAMP,
                version INTEGER NOT NULL DEFAULT 1,
                updated_at TIMESTAMP,
                UNIQUE (workspace_id, source_work_identity)
            );
            """
            )
        )
        session.commit()
        return session

    def test_ensure_and_acquire_sync_lease(self, sqlite_session: Session) -> None:
        repo = ReplyRepository(sqlite_session)
        ws_id = uuid.uuid4()
        mb_id = uuid.uuid4()

        # 1. Ensure sync state created
        state = repo.ensure_sync_state(
            workspace_id=ws_id,
            mailbox_id=mb_id,
            connection_generation=1,
        )
        sqlite_session.commit()
        assert state is not None
        assert state["status"] == "INITIALIZING"
        assert state["lease_owner"] is None

        # 2. Acquire lease
        lease = repo.acquire_sync_lease(
            workspace_id=ws_id,
            mailbox_id=mb_id,
            lease_owner="worker-1",
            lease_duration_seconds=120,
        )
        sqlite_session.commit()
        assert lease is not None
        assert lease["lease_owner"] == "worker-1"
        assert lease["lease_generation"] == 2

        # 3. Competing worker fails to acquire active lease
        competing = repo.acquire_sync_lease(
            workspace_id=ws_id,
            mailbox_id=mb_id,
            lease_owner="worker-2",
        )
        assert competing is None

        # 4. Advance checkpoint
        repo.advance_sync_checkpoint(
            workspace_id=ws_id,
            mailbox_id=mb_id,
            cursor_data='{"confirmed_cursor": "123"}',
            status="CURRENT",
        )
        sqlite_session.commit()

        # 5. Release lease
        repo.release_sync_lease(
            workspace_id=ws_id,
            mailbox_id=mb_id,
            lease_owner="worker-1",
        )
        sqlite_session.commit()

        refreshed = repo.get_sync_state(workspace_id=ws_id, mailbox_id=mb_id)
        assert refreshed["lease_owner"] is None
        assert refreshed["status"] == "CURRENT"

    def test_scheduler_claim_hands_lease_to_the_task_with_the_same_owner(
        self, sqlite_session: Session
    ) -> None:
        repo = ReplyRepository(sqlite_session)
        ws_id, mb_id = uuid.uuid4(), uuid.uuid4()
        repo.ensure_sync_state(
            workspace_id=ws_id, mailbox_id=mb_id, connection_generation=1
        )
        sqlite_session.commit()

        now = datetime.now(UTC) + timedelta(seconds=5)
        claimed = repo.claim_due_sync_states(lease_owner="scheduler", now=now)
        sqlite_session.commit()

        assert len(claimed) == 1
        owner = claimed[0]["lease_owner"]
        # Each claim gets its own token so two dispatches can never share one.
        assert owner.startswith("scheduler:") and owner != "scheduler:"
        # A lease that is already claimed is not claimed a second time.
        assert repo.claim_due_sync_states(lease_owner="scheduler", now=now) == []

        # The mailbox.sync task receives that token and must be able to take
        # over the lease the scheduler claimed for it ...
        lease = repo.acquire_sync_lease(
            workspace_id=ws_id,
            mailbox_id=mb_id,
            lease_owner=owner,
            lease_duration_seconds=120,
        )
        assert lease is not None
        assert lease["lease_owner"] == owner
        # ... while any other worker is still locked out.
        assert (
            repo.acquire_sync_lease(
                workspace_id=ws_id, mailbox_id=mb_id, lease_owner="worker-2"
            )
            is None
        )

    def test_recover_stale_sync_leases_clears_only_expired_leases(
        self, sqlite_session: Session
    ) -> None:
        repo = ReplyRepository(sqlite_session)
        ws_id = uuid.uuid4()
        expired_mb, active_mb = uuid.uuid4(), uuid.uuid4()
        for mailbox_id in (expired_mb, active_mb):
            repo.ensure_sync_state(
                workspace_id=ws_id, mailbox_id=mailbox_id, connection_generation=1
            )
            assert repo.acquire_sync_lease(
                workspace_id=ws_id,
                mailbox_id=mailbox_id,
                lease_owner=f"worker-{mailbox_id}",
                lease_duration_seconds=120,
            )
        sqlite_session.commit()

        now = datetime.now(UTC)
        sqlite_session.execute(
            text(
                "UPDATE public.mailbox_sync_states SET lease_expires_at = :past "
                "WHERE mailbox_id = :mb"
            ),
            {"past": now - timedelta(seconds=30), "mb": str(expired_mb)},
        )
        sqlite_session.commit()

        assert repo.recover_stale_sync_leases(now=now) == 1
        sqlite_session.commit()

        owners = dict(
            sqlite_session.execute(
                text(
                    "SELECT mailbox_id, lease_owner FROM public.mailbox_sync_states"
                )
            ).all()
        )
        assert owners[str(expired_mb)] is None
        assert owners[str(active_mb)] == f"worker-{active_mb}"

    def test_inbound_message_deduplication(self, sqlite_session: Session) -> None:
        repo = ReplyRepository(sqlite_session)
        ws_id = uuid.uuid4()
        mb_id = uuid.uuid4()
        conv_id = uuid.uuid4()

        inbound = NormalizedInboundMessage(
            provider="GMAIL",
            provider_message_id="p_mid_999",
            provider_thread_id="th_1",
            rfc_message_id="<rfc999@test.com>",
            in_reply_to=None,
            references=[],
            from_address="lead@test.com",
            from_name="Lead",
            to_addresses=["rep@outreach.com"],
            cc_addresses=[],
            bcc_addresses=[],
            subject="Hello",
            content_text="Hey",
            content_html=None,
            received_at=datetime.now(UTC),
        )

        # First insert
        msg_id, is_dup = repo.insert_inbound_message(
            workspace_id=ws_id,
            mailbox_id=mb_id,
            conversation_id=conv_id,
            connection_generation=1,
            inbound=inbound,
            association_status="UNRESOLVED",
        )
        sqlite_session.commit()
        assert msg_id is not None
        assert is_dup is False

        # Duplicate insert attempt
        dup_id, is_dup_2 = repo.insert_inbound_message(
            workspace_id=ws_id,
            mailbox_id=mb_id,
            conversation_id=conv_id,
            connection_generation=1,
            inbound=inbound,
            association_status="UNRESOLVED",
        )
        sqlite_session.commit()
        assert dup_id == msg_id
        assert is_dup_2 is True

    def test_record_reply_outcome_and_stop_campaign(self, sqlite_session: Session) -> None:
        repo = ReplyRepository(sqlite_session)
        ws_id = uuid.uuid4()
        mb_id = uuid.uuid4()
        camp_id = uuid.uuid4()
        enr_id = uuid.uuid4()
        inbound_id = uuid.uuid4()

        # Seed active enrollment
        sqlite_session.execute(
            text(
                "INSERT INTO public.campaign_enrollments (id, workspace_id, state, next_step_id) "
                "VALUES (:id, :ws, 'ACTIVE', 'step_2')"
            ),
            {"id": str(enr_id), "ws": str(ws_id)},
        )

        # Seed future scheduled and queued messages
        msg1_id = uuid.uuid4()
        msg2_id = uuid.uuid4()
        sqlite_session.execute(
            text(
                "INSERT INTO public.messages (id, workspace_id, mailbox_id, campaign_id, enrollment_id, status) "
                "VALUES (:id, :ws, :mbid, :cid, :eid, 'QUEUED')"
            ),
            {
                "id": str(msg1_id),
                "ws": str(ws_id),
                "mbid": str(mb_id),
                "cid": str(camp_id),
                "eid": str(enr_id),
            },
        )
        sqlite_session.execute(
            text(
                "INSERT INTO public.messages (id, workspace_id, mailbox_id, campaign_id, enrollment_id, status) "
                "VALUES (:id, :ws, :mbid, :cid, :eid, 'SCHEDULED')"
            ),
            {
                "id": str(msg2_id),
                "ws": str(ws_id),
                "mbid": str(mb_id),
                "cid": str(camp_id),
                "eid": str(enr_id),
            },
        )
        sqlite_session.commit()

        # Apply campaign safety stop
        now = datetime.now(UTC)
        stopped, cancelled = repo.record_reply_outcome_and_stop_campaign(
            workspace_id=ws_id,
            enrollment_id=enr_id,
            campaign_id=camp_id,
            inbound_message_id=inbound_id,
            provider_message_id="p_reply_1",
            occurred_at=now,
        )
        sqlite_session.commit()

        assert stopped == 1
        assert cancelled == 2

        # Verify enrollment is STOPPED
        enr_row = sqlite_session.execute(
            text("SELECT state, stop_reason, next_step_id FROM public.campaign_enrollments WHERE id = :id"),
            {"id": str(enr_id)},
        ).mappings().first()
        assert enr_row["state"] == "STOPPED"
        assert enr_row["stop_reason"] == "REPLIED"
        assert enr_row["next_step_id"] is None

        # Verify messages are CANCELLED
        msg_rows = sqlite_session.execute(
            text("SELECT id, status, terminal_reason FROM public.messages WHERE enrollment_id = :eid"),
            {"eid": str(enr_id)},
        ).mappings().all()
        for m in msg_rows:
            assert m["status"] == "CANCELLED"
            assert m["terminal_reason"] == "recipient_replied"

        # Verify recipient_outcomes record
        outcome = sqlite_session.execute(
            text("SELECT kind, source_key FROM public.recipient_outcomes WHERE enrollment_id = :eid"),
            {"eid": str(enr_id)},
        ).mappings().first()
        assert outcome["kind"] == "REPLIED"
        assert outcome["source_key"] == "p_reply_1"

        # Verify domain_events & outbox_work
        evt = sqlite_session.execute(
            text("SELECT event_type, aggregate_id FROM public.domain_events WHERE workspace_id = :ws"),
            {"ws": str(ws_id)},
        ).mappings().first()
        assert evt["event_type"] == "enrollment.replied"
        assert evt["aggregate_id"] == str(enr_id)

        work = sqlite_session.execute(
            text("SELECT kind, resource_id FROM public.outbox_work WHERE workspace_id = :ws"),
            {"ws": str(ws_id)},
        ).mappings().first()
        assert work["kind"] == "domain_event"
        assert work["resource_id"] == str(enr_id)


# =============================================================================
# 4. ReplySyncService Orchestration Tests
# =============================================================================


class TestReplySyncService:
    def test_sync_mailbox_end_to_end(self) -> None:
        session = MagicMock()
        mock_provider = MagicMock(spec=EmailProvider)
        mock_provider.capabilities = {
            ProviderCapability.REPLY_SYNC,
            ProviderCapability.CREDENTIAL_REFRESH,
        }

        ws_id = uuid.uuid4()
        mb_id = uuid.uuid4()
        outbound_msg_id = uuid.uuid4()
        camp_id = uuid.uuid4()
        enr_id = uuid.uuid4()

        service = ReplySyncService(session, providers={"GMAIL": mock_provider})
        service.repository = MagicMock()

        # Setup repository mocks
        service.repository.get_mailbox_for_sync.return_value = {
            "id": mb_id,
            "workspace_id": ws_id,
            "provider": "GMAIL",
            "connection_state": "CONNECTED",
            "current_connection_generation": 1,
            "original_address": "rep@outreach.com",
        }
        service.repository.ensure_sync_state.return_value = {
            "id": uuid.uuid4(),
            "connection_generation": 1,
            "status": "INITIALIZING",
            "cursor_data": None,
        }
        service.repository.acquire_sync_lease.return_value = {
            "id": uuid.uuid4(),
            "connection_generation": 1,
            "status": "CURRENT",
            "cursor_data": None,
            "lease_owner": "worker-sync",
            "lease_generation": 2,
        }
        service.repository.get_mailbox_connection.return_value = {
            "credential_ciphertext": b"dummy",
            "nonce": b"dummy12345678",
            "encryption_key_id": "v1",
            "protected_config": None,
            "expires_at": datetime.now(UTC) + timedelta(hours=1),
        }

        # Mock candidate message in DB
        candidate = OutboundMessageCandidate(
            message_id=outbound_msg_id,
            campaign_id=camp_id,
            enrollment_id=enr_id,
            mailbox_id=mb_id,
            rfc_message_id="<outbound1@platform.com>",
            provider_message_id="prov_out_1",
            provider_thread_id="th_100",
            frozen_destination="lead@customer.com",
        )
        service.repository.load_outbound_candidates.return_value = [candidate]
        service.repository.find_or_create_conversation.return_value = uuid.uuid4()
        service.repository.insert_inbound_message.return_value = (uuid.uuid4(), False)
        service.repository.record_reply_outcome_and_stop_campaign.return_value = (1, 1)

        # Mock provider returning 1 reply message
        raw_msg = ProviderInboundMessage(
            provider_message_id="gmail_in_1",
            provider_thread_id="th_100",
            rfc_message_id="<lead-reply@customer.com>",
            in_reply_to="<outbound1@platform.com>",
            references=["<outbound1@platform.com>"],
            from_address="lead@customer.com",
            to_addresses=["rep@outreach.com"],
            subject="Re: Quick intro",
            body_text="Yes, let's connect!",
            body_html=None,
            received_at=datetime.now(UTC),
        )
        mock_provider.sync_inbound_messages.return_value = SyncPageResult(
            messages=[raw_msg],
            has_more=False,
            next_cursor="cursor_v2",
        )

        with patch("app.modules.replies.service.decrypt_credentials", return_value={"access_token": "token"}):
            result = service.sync_mailbox(workspace_id=ws_id, mailbox_id=mb_id)

        assert result.status == "CURRENT"
        assert result.messages_discovered == 1
        assert result.messages_persisted == 1
        assert result.replies_matched == 1
        assert result.enrollments_stopped == 1
        assert result.future_messages_cancelled == 1
        service.repository.insert_outreach_link.assert_called_once()
        service.repository.record_reply_outcome_and_stop_campaign.assert_called_once()
        service.repository.release_sync_lease.assert_called_once()

    def test_sync_mailbox_resync_handling(self) -> None:
        session = MagicMock()
        mock_provider = MagicMock(spec=EmailProvider)
        mock_provider.capabilities = {ProviderCapability.REPLY_SYNC}

        ws_id = uuid.uuid4()
        mb_id = uuid.uuid4()

        service = ReplySyncService(session, providers={"GMAIL": mock_provider})
        service.repository = MagicMock()
        service.repository.get_mailbox_for_sync.return_value = {
            "id": mb_id,
            "workspace_id": ws_id,
            "provider": "GMAIL",
            "connection_state": "CONNECTED",
            "current_connection_generation": 1,
        }
        service.repository.acquire_sync_lease.return_value = {
            "connection_generation": 1,
            "status": "CURRENT",
            "cursor_data": '{"confirmed_cursor": "expired_cursor"}',
        }
        service.repository.get_mailbox_connection.return_value = {
            "credential_ciphertext": b"dummy",
            "nonce": b"dummy12345678",
            "encryption_key_id": "v1",
            "expires_at": datetime.now(UTC) + timedelta(hours=1),
        }

        # Provider indicates expired history ID -> resync required
        mock_provider.sync_inbound_messages.return_value = SyncPageResult(
            messages=[],
            has_more=False,
            resync_required=True,
        )

        with patch("app.modules.replies.service.decrypt_credentials", return_value={"access_token": "token"}):
            result = service.sync_mailbox(workspace_id=ws_id, mailbox_id=mb_id)

        assert result.status == "RESYNC_REQUIRED"
        assert result.resync_required is True
        service.repository.mark_resync_required.assert_called_once_with(
            workspace_id=ws_id,
            mailbox_id=mb_id,
        )
        service.repository.insert_safety_hold.assert_called_once()


# =============================================================================
# 5. Pre-Send Gate Safety Race Tests
# =============================================================================


class TestSendGateReplySafety:
    def _create_context(self, **overrides: object) -> LoadedSendContext:
        defaults = dict(
            message_id=uuid.uuid4(),
            workspace_id=uuid.uuid4(),
            purpose="CAMPAIGN",
            campaign_id=uuid.uuid4(),
            enrollment_id=uuid.uuid4(),
            mailbox_id=uuid.uuid4(),
            address_id=uuid.uuid4(),
            message_status="QUEUED",
            dispatch_generation=1,
            message_version=1,
            retry_count=0,
            retry_budget=3,
            content_subject="Hello",
            content_body_html="<p>Test</p>",
            content_digest=None,
            renderer_version=1,
            rendered_at=datetime.now(UTC),
            frozen_destination="lead@target.com",
            frozen_sender_address="rep@outreach.com",
            frozen_sender_name="Rep",
            rfc_message_id="<out123@platform.com>",
            campaign_status="RUNNING",
            campaign_planning_status="READY",
            enrollment_state="ACTIVE",
            canonical_address="lead@target.com",
            mailbox_workspace_id=uuid.uuid4(),
            mailbox_provider="GMAIL",
            mailbox_provider_account_id="acc1",
            mailbox_connection_state="CONNECTED",
            mailbox_health_state="HEALTHY",
            mailbox_policy_state="ENABLED",
            mailbox_policy_reason=None,
            mailbox_circuit_state="CLOSED",
            mailbox_blocked_until=None,
            mailbox_current_connection_generation=1,
            mailbox_original_address="rep@outreach.com",
            mailbox_sender_display_name="Rep",
            raw={},
            mailbox_pending_safety_count=0,
        )
        defaults.update(overrides)
        if defaults["mailbox_workspace_id"] != defaults["workspace_id"]:
            defaults["mailbox_workspace_id"] = defaults["workspace_id"]
        return LoadedSendContext(**defaults)

    def test_check_reply_outcome_blocks_send_when_reply_recorded(self) -> None:
        ctx = self._create_context()
        session = MagicMock()
        # Simulate row returned from recipient_outcomes with kind = 'REPLIED'
        session.execute.return_value.first.return_value = (1,)

        with pytest.raises(gates.RecipientRepliedRejected) as exc_info:
            gates.check_reply_outcome(session, ctx)

        assert "recorded reply outcome" in str(exc_info.value)
        assert exc_info.value.terminal_reason == "recipient_replied"

    def test_check_reply_outcome_allows_send_when_no_reply(self) -> None:
        ctx = self._create_context()
        session = MagicMock()
        session.execute.return_value.first.return_value = None

        gates.check_reply_outcome(session, ctx)  # must not raise

    def test_triple_layer_protection_under_race(self) -> None:
        # Layer 1: message_status was updated to CANCELLED by reply sync
        ctx_cancelled = self._create_context(message_status="CANCELLED")
        with pytest.raises(gates.MessageAlreadyResolved):
            gates.check_message_state(ctx_cancelled)

        # Layer 2: enrollment_state was updated to STOPPED by reply sync
        ctx_stopped_enr = self._create_context(enrollment_state="STOPPED")
        with pytest.raises(gates.EnrollmentStateRejected):
            gates.check_enrollment_state(ctx_stopped_enr)

        # Layer 3: recipient_outcomes check explicitly catches reply outcome
        ctx_active = self._create_context(enrollment_state="ACTIVE", message_status="QUEUED")
        session = MagicMock()
        session.execute.return_value.first.return_value = (1,)
        with pytest.raises(gates.RecipientRepliedRejected):
            gates.check_reply_outcome(session, ctx_active)

        # Layer 4: safety hold active
        ctx_hold = self._create_context(mailbox_pending_safety_count=1)
        with pytest.raises(gates.SafetyHoldRejected):
            gates.check_safety_holds(ctx_hold)
