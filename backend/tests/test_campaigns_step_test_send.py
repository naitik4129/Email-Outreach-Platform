"""Unit tests for the campaign step test send and the preview-recipients query.

No email is ever sent: the provider is a recording fake registered in
ProviderRegistry, and the DB layer is mocked.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

import pytest
from app.api.deps import WorkspaceContext
from app.core.crypto import encrypt_credentials
from app.core.errors import AppError
from app.modules.campaigns.schemas import StepTestSendIn
from app.modules.campaigns.sequence_service import SequenceService
from app.modules.campaigns.step_test_send_service import (
    TEST_SUBJECT_PREFIX,
    StepTestSendService,
)
from app.modules.mailboxes.providers.base import (
    EmailProvider,
    OutboundMessageEnvelope,
    ProviderCapability,
    ProviderSendResult,
)
from app.modules.mailboxes.providers.registry import ProviderRegistry
from app.modules.mailboxes.repository import MailboxRepository
from pydantic import ValidationError

WORKSPACE_ID = uuid.uuid4()
USER_ID = uuid.uuid4()
CAMPAIGN_ID = uuid.uuid4()
STEP_ID = uuid.uuid4()
MAILBOX_ID = uuid.uuid4()
MEMBER_ID = uuid.uuid4()


class RecordingProvider(EmailProvider):
    """Records what would have been sent; never touches the network."""

    capabilities = frozenset({ProviderCapability.SEND})

    def __init__(self) -> None:
        self.sent: list[OutboundMessageEnvelope] = []

    def send_message(self, credential, envelope):  # type: ignore[override]
        self.sent.append(envelope)
        return ProviderSendResult(status="ACCEPTED", accepted_at=datetime.now(UTC))


def _context() -> WorkspaceContext:
    ctx = MagicMock(spec=WorkspaceContext)
    ctx.workspace_id = WORKSPACE_ID
    ctx.user_id = USER_ID
    return ctx


def _payload(**overrides) -> StepTestSendIn:
    data = {
        "mailbox_id": MAILBOX_ID,
        "recipient_email": "qa@example.com",
        "confirm_recipient": True,
    }
    data.update(overrides)
    return StepTestSendIn(**data)


def _step(**overrides):
    row = {
        "id": STEP_ID,
        "campaign_id": CAMPAIGN_ID,
        "kind": "EMAIL",
        "email_subject": "Hi {{first_name|there}}",
        "email_body_html": "<p>Hello {{first_name|there}} from {{company|us}}</p>",
        "email_preheader": None,
    }
    row.update(overrides)
    return row


@pytest.fixture
def provider():
    fake = RecordingProvider()
    ProviderRegistry.register("GMAIL", fake)
    yield fake
    ProviderRegistry.reset()


@pytest.fixture
def mailbox_repo(monkeypatch: pytest.MonkeyPatch) -> MagicMock:
    repo = MagicMock(spec=MailboxRepository)
    workspace_creds, key_id, nonce = encrypt_credentials(
        {"access_token": "t", "refresh_token": "r", "token_type": "Bearer"},
        WORKSPACE_ID,
        MAILBOX_ID,
        "GMAIL",
    )
    repo.get_mailbox.return_value = {
        "id": MAILBOX_ID,
        "original_address": "sender@example.com",
        "sender_display_name": "Sender",
        "connection_state": "CONNECTED",
        "health_state": "HEALTHY",
        "policy_state": "ENABLED",
        "policy_reason": None,
        "current_connection_generation": 1,
        "provider": "GMAIL",
    }
    repo.ensure_recipient_address.return_value = uuid.uuid4()
    repo.is_address_suppressed.return_value = False
    repo.get_user_membership_id.return_value = uuid.uuid4()
    repo.ensure_command_receipt.return_value = uuid.uuid4()
    repo.get_mailbox_connection.return_value = {
        "credential_ciphertext": workspace_creds,
        "nonce": nonce,
        "encryption_key_id": key_id,
        "expires_at": datetime.now(UTC) + timedelta(hours=1),
    }
    monkeypatch.setattr(
        "app.modules.campaigns.step_test_send_service.MailboxRepository",
        lambda _session: repo,
    )
    return repo


def _service(
    *,
    campaign=True,
    step=None,
    assigned=(MAILBOX_ID,),
    member=None,
) -> StepTestSendService:
    service = StepTestSendService(MagicMock())
    repo = MagicMock()
    repo.get_campaign.return_value = {"id": CAMPAIGN_ID} if campaign else None
    repo.get_step.return_value = _step() if step is None else step
    repo.list_campaign_mailboxes.return_value = [{"mailbox_id": m} for m in assigned]
    repo.get_accepted_audience_member.return_value = member
    repo.list_step_attachments.return_value = []
    service.repo = repo
    return service


def _send(service: StepTestSendService, payload: StepTestSendIn | None = None):
    return service.send(
        _context(), CAMPAIGN_ID, STEP_ID, payload or _payload(), "key-1"
    )


class TestValidation:
    def test_confirmation_is_required(self, mailbox_repo, provider) -> None:
        with pytest.raises(AppError) as exc:
            _send(_service(), _payload(confirm_recipient=False))
        assert exc.value.status_code == 422
        assert provider.sent == []

    def test_unknown_campaign_is_404(self, mailbox_repo, provider) -> None:
        with pytest.raises(AppError) as exc:
            _send(_service(campaign=False))
        assert exc.value.status_code == 404
        assert provider.sent == []

    def test_step_of_another_campaign_is_404(self, mailbox_repo, provider) -> None:
        with pytest.raises(AppError) as exc:
            _send(_service(step=_step(campaign_id=uuid.uuid4())))
        assert exc.value.status_code == 404

    def test_missing_step_is_404(self, mailbox_repo, provider) -> None:
        service = _service()
        service.repo.get_step.return_value = None
        with pytest.raises(AppError) as exc:
            _send(service)
        assert exc.value.status_code == 404

    def test_wait_step_cannot_be_sent(self, mailbox_repo, provider) -> None:
        with pytest.raises(AppError) as exc:
            _send(_service(step=_step(kind="WAIT")))
        assert exc.value.status_code == 422

    def test_mailbox_must_belong_to_the_campaign(self, mailbox_repo, provider) -> None:
        with pytest.raises(AppError) as exc:
            _send(_service(assigned=(uuid.uuid4(),)))
        assert exc.value.status_code == 422
        assert "not assigned" in exc.value.message
        mailbox_repo.get_mailbox.assert_not_called()
        assert provider.sent == []

    def test_no_mailbox_at_all(self, mailbox_repo, provider) -> None:
        with pytest.raises(AppError) as exc:
            _send(_service(assigned=()))
        assert exc.value.status_code == 422

    def test_prospect_from_another_campaign_or_tenant_is_404(
        self, mailbox_repo, provider
    ) -> None:
        # The repository query is scoped to (workspace, campaign); an id that
        # doesn't resolve there must not be rendered.
        service = _service(member=None)
        with pytest.raises(AppError) as exc:
            _send(service, _payload(audience_member_id=MEMBER_ID))
        assert exc.value.status_code == 404
        service.repo.get_accepted_audience_member.assert_called_once_with(
            workspace_id=WORKSPACE_ID, campaign_id=CAMPAIGN_ID, member_id=MEMBER_ID
        )
        assert provider.sent == []

    def test_empty_subject_rejected(self, mailbox_repo, provider) -> None:
        with pytest.raises(AppError) as exc:
            _send(_service(step=_step(email_subject="  ")))
        assert exc.value.status_code == 422
        assert provider.sent == []

    def test_invalid_variable_rejected_before_sending(
        self, mailbox_repo, provider
    ) -> None:
        with pytest.raises(AppError) as exc:
            _send(_service(), _payload(email_body_html="<p>{{bogus}}</p>"))
        assert exc.value.status_code == 422
        assert provider.sent == []

    def test_schema_bounds(self) -> None:
        with pytest.raises(ValidationError):
            _payload(recipient_email="a")
        with pytest.raises(ValidationError):
            _payload(email_subject="")


class TestSending:
    def test_renders_with_the_chosen_prospect_and_marks_the_subject(
        self, mailbox_repo, provider
    ) -> None:
        member = {
            "id": MEMBER_ID,
            "lead_id": uuid.uuid4(),
            "frozen_variables": {"first_name": "Grace", "company": "Navy"},
        }
        result = _send(_service(member=member), _payload(audience_member_id=MEMBER_ID))

        assert result.status == "SENT"
        assert len(provider.sent) == 1
        envelope = provider.sent[0]
        assert envelope.to_address == "qa@example.com"
        assert envelope.from_address == "sender@example.com"
        assert envelope.subject == f"{TEST_SUBJECT_PREFIX}Hi Grace"
        assert "Hello Grace from Navy" in envelope.body_html

    def test_uses_sample_data_without_a_prospect(self, mailbox_repo, provider) -> None:
        _send(_service())
        envelope = provider.sent[0]
        assert envelope.subject == f"{TEST_SUBJECT_PREFIX}Hi Alex"
        assert "Hello Alex from Acme Corp" in envelope.body_html

    def test_missing_prospect_data_uses_fallbacks_not_blanks(
        self, mailbox_repo, provider
    ) -> None:
        member = {"id": MEMBER_ID, "lead_id": uuid.uuid4(), "frozen_variables": {}}
        _send(_service(member=member), _payload(audience_member_id=MEMBER_ID))
        envelope = provider.sent[0]
        assert envelope.subject == f"{TEST_SUBJECT_PREFIX}Hi there"
        assert "Hello there from us" in envelope.body_html

    def test_unsaved_draft_content_overrides_the_saved_step(
        self, mailbox_repo, provider
    ) -> None:
        _send(
            _service(),
            _payload(
                email_subject="Draft {{first_name}}",
                email_body_html='<p onclick="x()">Draft body</p><script>bad()</script>',
                email_preheader="Draft teaser",
            ),
        )
        envelope = provider.sent[0]
        assert envelope.subject == f"{TEST_SUBJECT_PREFIX}Draft Alex"
        # Draft HTML is sanitized exactly as it would be on save.
        assert "onclick" not in envelope.body_html
        assert "bad()" not in envelope.body_html
        assert "Draft body" in envelope.body_html
        # ...and the pre-header is injected the way a real send injects it.
        assert envelope.body_html.startswith('<div style="display:none')
        assert "Draft teaser" in envelope.body_html

    def test_saved_preheader_is_used_when_no_draft_is_sent(
        self, mailbox_repo, provider
    ) -> None:
        _send(_service(step=_step(email_preheader="Saved teaser")))
        assert "Saved teaser" in provider.sent[0].body_html

    def test_long_subjects_are_capped(self, mailbox_repo, provider) -> None:
        _send(_service(step=_step(email_subject="x" * 500)))
        assert len(provider.sent[0].subject) == 500

    def test_uses_a_controlled_test_pipeline_with_its_own_scope(
        self, mailbox_repo, provider
    ) -> None:
        _send(_service())
        # Same authorization + message + attempt bookkeeping as the mailbox
        # connectivity test, under its own idempotency scope.
        assert mailbox_repo.insert_controlled_send_authorization.called
        assert mailbox_repo.insert_test_message.called
        assert mailbox_repo.insert_message_attempt.called
        receipt_args = mailbox_repo.ensure_command_receipt.call_args.args
        assert receipt_args[2] == "campaign.step_test_send"
        assert receipt_args[3] == "key-1"
        message = mailbox_repo.insert_test_message.call_args.kwargs
        assert message["content_subject"].startswith(TEST_SUBJECT_PREFIX)
        assert message["frozen_destination"] == "qa@example.com"

    def test_receipt_payload_is_bound_to_the_step_and_content(
        self, mailbox_repo, provider
    ) -> None:
        _send(_service())
        first = mailbox_repo.ensure_command_receipt.call_args.args[4]
        _send(_service(), _payload(email_subject="Different"))
        second = mailbox_repo.ensure_command_receipt.call_args.args[4]
        assert first != second

    def test_suppressed_recipient_is_blocked_and_nothing_is_sent(
        self, mailbox_repo, provider
    ) -> None:
        mailbox_repo.is_address_suppressed.return_value = True
        with pytest.raises(AppError) as exc:
            _send(_service())
        assert exc.value.code == "suppressed_recipient"
        assert provider.sent == []

    def test_disconnected_mailbox_is_blocked(self, mailbox_repo, provider) -> None:
        mailbox_repo.get_mailbox.return_value = {
            **mailbox_repo.get_mailbox.return_value,
            "connection_state": "DISCONNECTED",
        }
        with pytest.raises(AppError) as exc:
            _send(_service())
        assert exc.value.status_code == 409
        assert provider.sent == []

    def test_provider_rejection_is_reported_not_hidden(
        self, mailbox_repo, provider
    ) -> None:
        provider.send_message = lambda credential, envelope: ProviderSendResult(  # type: ignore[method-assign]
            status="DEFINITIVELY_REJECTED", error_category="AUTH"
        )
        result = _send(_service())
        assert result.status == "FAILED"
        assert "rejected" in (result.error_message or "").lower()


class TestMailboxConnectivityTestUnchanged:
    def test_default_content_is_still_the_connectivity_message(
        self, mailbox_repo, provider
    ) -> None:
        from app.modules.mailboxes.service import MailboxService

        MailboxService(mailbox_repo).send_controlled_test_email(
            workspace_id=WORKSPACE_ID,
            user_id=USER_ID,
            mailbox_id=MAILBOX_ID,
            recipient_email="qa@example.com",
        )
        envelope = provider.sent[0]
        assert envelope.subject == "Test Email from Sender"
        assert "controlled test email" in envelope.body_html
        assert mailbox_repo.ensure_command_receipt.call_args.args[2] == (
            "mailbox.test_send"
        )


class TestPreviewRecipients:
    def _service(self, *, campaign, audience, rows, accepted=3) -> SequenceService:
        service = SequenceService(MagicMock())
        repo = MagicMock()
        repo.get_campaign.return_value = campaign
        repo.get_audience.return_value = audience
        repo.get_latest_audience.return_value = audience
        repo.list_accepted_audience_members.return_value = rows
        repo.get_audience_member_counts.return_value = {
            "accepted": accepted,
            "excluded": 0,
        }
        service.repo = repo
        return service

    def _row(self, ordinal: int, **variables):
        return {
            "id": uuid.uuid4(),
            "lead_id": uuid.uuid4(),
            "capture_ordinal": ordinal,
            "frozen_variables": {"email": f"p{ordinal}@example.com", **variables},
        }

    def test_returns_the_snapshot_the_message_will_render_with(self) -> None:
        campaign = {"activated_audience_id": None, "draft_audience_id": uuid.uuid4()}
        audience = {"id": uuid.uuid4(), "status": "READY"}
        service = self._service(
            campaign=campaign,
            audience=audience,
            rows=[self._row(0, first_name="Ada", company="Engine")],
        )
        out = service.list_preview_recipients(
            _context(), CAMPAIGN_ID, limit=25, after_ordinal=None
        )
        assert out.source == "AUDIENCE"
        assert out.total == 3
        assert out.items[0].email == "p0@example.com"
        assert out.items[0].first_name == "Ada"
        assert out.items[0].variables["company"] == "Engine"
        assert out.next_cursor is None

    def test_paginates_by_capture_ordinal(self) -> None:
        campaign = {"activated_audience_id": None, "draft_audience_id": uuid.uuid4()}
        audience = {"id": uuid.uuid4(), "status": "READY"}
        service = self._service(
            campaign=campaign,
            audience=audience,
            rows=[self._row(0), self._row(1), self._row(2)],
        )
        out = service.list_preview_recipients(
            _context(), CAMPAIGN_ID, limit=2, after_ordinal=None
        )
        assert [i.email for i in out.items] == ["p0@example.com", "p1@example.com"]
        assert out.next_cursor == 1
        service.repo.list_accepted_audience_members.assert_called_once()
        call = service.repo.list_accepted_audience_members.call_args
        assert call.kwargs["limit"] == 3

    def test_prefers_the_activated_audience(self) -> None:
        activated = uuid.uuid4()
        campaign = {
            "activated_audience_id": activated,
            "draft_audience_id": uuid.uuid4(),
        }
        service = self._service(
            campaign=campaign,
            audience={"id": activated, "status": "READY"},
            rows=[],
        )
        service.list_preview_recipients(
            _context(), CAMPAIGN_ID, limit=5, after_ordinal=None
        )
        assert service.repo.get_audience.call_args.kwargs["audience_id"] == activated

    @pytest.mark.parametrize(
        "audience", [None, {"id": uuid.uuid4(), "status": "CAPTURING"}]
    )
    def test_no_ready_audience_means_none(self, audience) -> None:
        campaign = {"activated_audience_id": None, "draft_audience_id": None}
        service = self._service(campaign=campaign, audience=audience, rows=[])
        out = service.list_preview_recipients(
            _context(), CAMPAIGN_ID, limit=5, after_ordinal=None
        )
        assert out.source == "NONE"
        assert out.items == []
        service.repo.list_accepted_audience_members.assert_not_called()

    def test_unknown_campaign_is_404(self) -> None:
        service = self._service(campaign=None, audience=None, rows=[])
        with pytest.raises(AppError) as exc:
            service.list_preview_recipients(
                _context(), CAMPAIGN_ID, limit=5, after_ordinal=None
            )
        assert exc.value.status_code == 404


class TestStepAttachments:
    PDF = b"%PDF-1.7\n%x\n"
    PNG = b"\x89PNG\r\n\x1a\n"

    def _row(self, **kw):
        row = {
            "storage_key": "k/a.pdf",
            "filename": "a.pdf",
            "content_type": "application/pdf",
            "size_bytes": len(self.PDF),
            "sha256": hashlib.sha256(self.PDF).hexdigest(),
            "disposition": "ATTACHMENT",
            "content_id": "tok12345",
        }
        row.update(kw)
        return row

    def _with_files(self, rows, objects):
        service = _service()
        service.repo.list_step_attachments.return_value = rows

        class Storage:
            def download_object(self, key):
                from app.modules.imports.storage import StorageObjectMissingError

                if key not in objects:
                    raise StorageObjectMissingError("missing")
                return objects[key]

        service._storage_factory = lambda: Storage()
        return service

    def test_files_are_verified_and_sent_with_the_test_email(
        self, mailbox_repo, provider
    ) -> None:
        image = self._row(
            disposition="INLINE",
            content_type="image/png",
            filename="i.png",
            storage_key="k/i.png",
            content_id="imgtok01",
            size_bytes=len(self.PNG),
            sha256=hashlib.sha256(self.PNG).hexdigest(),
        )
        service = self._with_files(
            [self._row(), image], {"k/a.pdf": self.PDF, "k/i.png": self.PNG}
        )
        _send(service)
        sent = provider.sent[0].attachments
        assert [(a.filename, a.content_id) for a in sent] == [
            ("a.pdf", None),
            ("i.png", "imgtok01"),
        ]

    def test_a_missing_file_stops_the_test_before_anything_is_sent(
        self, mailbox_repo, provider
    ) -> None:
        service = self._with_files([self._row()], {})
        with pytest.raises(AppError) as exc:
            _send(service)
        assert exc.value.code == "attachment_missing"
        assert exc.value.status_code == 409
        assert provider.sent == []
        mailbox_repo.insert_test_message.assert_not_called()

    def test_a_tampered_file_is_refused(self, mailbox_repo, provider) -> None:
        service = self._with_files([self._row()], {"k/a.pdf": self.PDF + b"x"})
        with pytest.raises(AppError) as exc:
            _send(service)
        assert exc.value.code == "attachment_corrupt"
        assert provider.sent == []

    def test_no_attachments_never_touches_storage(self, mailbox_repo, provider) -> None:
        service = _service()

        def boom():
            raise AssertionError("storage must not be used")

        service._storage_factory = boom
        _send(service)
        assert provider.sent[0].attachments == ()
