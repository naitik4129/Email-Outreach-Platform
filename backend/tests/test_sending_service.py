from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest

from app.core.config import Settings
from app.modules.mailboxes.providers.base import ProviderSendResult
from app.modules.mailboxes.providers.registry import ProviderRegistry
from app.modules.rate_limit.schemas import (
    RateLimitDenied,
    RateLimitUnavailable,
    RateReservation,
)
from app.modules.scheduler.schemas import SendTaskPayload
from app.modules.sending.repository import (
    AttemptAlreadyClaimed,
    MessageLockedByAnotherWorker,
)
from app.modules.sending.schemas import LoadedSendContext
from app.modules.sending.service import SendingService


def _payload(
    *, workspace_id: uuid.UUID, message_id: uuid.UUID, dispatch_generation: int = 1
) -> SendTaskPayload:
    return SendTaskPayload(
        schema_version=1,
        work_id=str(uuid.uuid4()),
        delivery_id=str(uuid.uuid4()),
        message_id=str(message_id),
        dispatch_id=str(uuid.uuid4()),
        resource_id=str(message_id),
        workspace_id=str(workspace_id),
        dispatch_generation=dispatch_generation,
    )


def _ctx(**overrides: object) -> LoadedSendContext:
    now = datetime.now(UTC)
    subject, body, renderer_version = "Hello", "<p>Hi</p>", 1
    digest = hashlib.sha256(
        f"{subject}\x00{body}\x00{renderer_version}".encode()
    ).hexdigest()
    workspace_id = overrides.get("workspace_id", uuid.uuid4())
    defaults: dict[str, object] = dict(
        message_id=uuid.uuid4(),
        workspace_id=workspace_id,
        purpose="CAMPAIGN",
        campaign_id=uuid.uuid4(),
        enrollment_id=uuid.uuid4(),
        mailbox_id=uuid.uuid4(),
        address_id=uuid.uuid4(),
        message_status="QUEUED",
        dispatch_generation=1,
        message_version=5,
        retry_count=0,
        retry_budget=3,
        content_subject=subject,
        content_body_html=body,
        content_digest=digest,
        renderer_version=renderer_version,
        rendered_at=now,
        frozen_destination="lead@example.com",
        frozen_sender_address="sender@example.com",
        frozen_sender_name="Sender",
        rfc_message_id="abc@example.com",
        campaign_status="RUNNING",
        campaign_planning_status="READY",
        enrollment_state="ACTIVE",
        canonical_address="lead@example.com",
        mailbox_workspace_id=workspace_id,
        mailbox_provider="GMAIL",
        mailbox_provider_account_id="acct-1",
        mailbox_connection_state="CONNECTED",
        mailbox_health_state="HEALTHY",
        mailbox_policy_state="ENABLED",
        mailbox_policy_reason=None,
        mailbox_circuit_state="CLOSED",
        mailbox_blocked_until=None,
        mailbox_current_connection_generation=1,
        mailbox_original_address="mailbox@example.com",
        mailbox_sender_display_name="Mailbox Sender",
        raw={},
    )
    defaults.update(overrides)
    return LoadedSendContext(**defaults)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _reset_provider_registry() -> None:
    yield
    ProviderRegistry.reset()


def _make_service(ctx: LoadedSendContext | None = None) -> SendingService:
    session = MagicMock()
    # Default: no suppression row found (is_address_suppressed /
    # is_platform_suppressed both do `session.execute(...).first()`).
    # Tests that specifically exercise suppression override this.
    session.execute.return_value.first.return_value = None
    service = SendingService(session, settings=Settings.current())
    service.repository = MagicMock()
    service.rate_policy_repository = MagicMock()
    service.rate_limiter = MagicMock()

    service.repository.load_message_for_send.return_value = ctx
    service.rate_policy_repository.resolve_applicable_policies.return_value = []
    service.rate_limiter.current_generation.return_value = 1
    reservation = RateReservation(
        reservation_id="r1",
        generation=1,
        granted_at=datetime.now(UTC),
        expires_at=datetime.now(UTC),
        scopes=(),
    )
    service.rate_limiter.reserve.return_value = reservation
    service.repository.get_rate_control_status.return_value = {
        "generation": 1,
        "status": "READY",
    }
    # Re-fetch inside the authorization transaction returns the same ctx by
    # default; tests override with side_effect where a second, different
    # load is meaningful.
    service.repository.load_message_for_send.side_effect = None
    service.repository.next_attempt_ordinal.return_value = 1
    service.repository.transition_message_to_sending.return_value = True
    return service


# ---------------------------------------------------------------------------
# Message existence / locking
# ---------------------------------------------------------------------------


def test_message_not_found_is_noop_and_never_touches_provider() -> None:
    fake_provider = MagicMock()
    ProviderRegistry.register("GMAIL", fake_provider)
    ws, mid = uuid.uuid4(), uuid.uuid4()
    service = _make_service()
    service.repository.load_message_for_send.return_value = None

    outcome = service.execute(_payload(workspace_id=ws, message_id=mid))

    assert outcome.outcome == "NOOP"
    assert outcome.reason == "message_not_found"
    fake_provider.send_message.assert_not_called()


def test_message_locked_by_concurrent_worker_is_noop() -> None:
    fake_provider = MagicMock()
    ProviderRegistry.register("GMAIL", fake_provider)
    ws, mid = uuid.uuid4(), uuid.uuid4()
    service = _make_service()
    service.repository.load_message_for_send.side_effect = MessageLockedByAnotherWorker(
        str(mid)
    )

    outcome = service.execute(_payload(workspace_id=ws, message_id=mid))

    assert outcome.outcome == "NOOP"
    assert outcome.reason == "locked"
    fake_provider.send_message.assert_not_called()


@pytest.mark.parametrize(
    "status", ["SENT", "FAILED", "SKIPPED", "CANCELLED", "UNKNOWN_OUTCOME"]
)
def test_duplicate_delivery_after_resolution_is_noop(status: str) -> None:
    """Redelivery after the message already reached a terminal state must
    never call the provider again."""
    fake_provider = MagicMock()
    ProviderRegistry.register("GMAIL", fake_provider)
    ctx = _ctx(message_status=status)
    service = _make_service(ctx)

    outcome = service.execute(
        _payload(workspace_id=ctx.workspace_id, message_id=ctx.message_id)
    )

    assert outcome.outcome == "NOOP"
    fake_provider.send_message.assert_not_called()


def test_stale_dispatch_generation_is_noop() -> None:
    fake_provider = MagicMock()
    ProviderRegistry.register("GMAIL", fake_provider)
    ctx = _ctx(dispatch_generation=5)
    service = _make_service(ctx)

    outcome = service.execute(
        _payload(
            workspace_id=ctx.workspace_id,
            message_id=ctx.message_id,
            dispatch_generation=1,
        )
    )

    assert outcome.outcome == "NOOP"
    fake_provider.send_message.assert_not_called()


def test_attempt_already_claimed_is_noop_and_releases_reservation() -> None:
    fake_provider = MagicMock()
    ProviderRegistry.register("GMAIL", fake_provider)
    ctx = _ctx()
    service = _make_service(ctx)
    service.repository.insert_prepared_attempt.side_effect = AttemptAlreadyClaimed(
        str(ctx.message_id)
    )

    outcome = service.execute(
        _payload(workspace_id=ctx.workspace_id, message_id=ctx.message_id)
    )

    assert outcome.outcome == "NOOP"
    assert outcome.reason == "already_claimed"
    fake_provider.send_message.assert_not_called()
    service.rate_limiter.release.assert_called_once_with("r1")


# ---------------------------------------------------------------------------
# Rate limiting: must defer, never call provider, and fail closed
# ---------------------------------------------------------------------------


def test_rate_limit_unavailable_defers_and_never_calls_provider() -> None:
    fake_provider = MagicMock()
    ProviderRegistry.register("GMAIL", fake_provider)
    ctx = _ctx()
    service = _make_service(ctx)
    service.rate_limiter.reserve.side_effect = RateLimitUnavailable("redis down")

    outcome = service.execute(
        _payload(workspace_id=ctx.workspace_id, message_id=ctx.message_id)
    )

    assert outcome.outcome == "DEFERRED"
    assert outcome.reason == "rate_limit_unavailable"
    fake_provider.send_message.assert_not_called()


def test_rate_limit_denied_defers_and_never_calls_provider() -> None:
    from app.modules.rate_limit.schemas import RateDenial

    fake_provider = MagicMock()
    ProviderRegistry.register("GMAIL", fake_provider)
    ctx = _ctx()
    service = _make_service(ctx)
    service.rate_limiter.reserve.side_effect = RateLimitDenied(
        RateDenial(
            reason="CAPACITY_DENIED",
            failing_kind=None,
            failing_scope_key=None,
            retry_after_seconds=5.0,
        )
    )

    outcome = service.execute(
        _payload(workspace_id=ctx.workspace_id, message_id=ctx.message_id)
    )

    assert outcome.outcome == "DEFERRED"
    assert outcome.reason == "rate_limit_denied"
    fake_provider.send_message.assert_not_called()


def test_rate_control_generation_mismatch_inside_transaction_aborts() -> None:
    fake_provider = MagicMock()
    ProviderRegistry.register("GMAIL", fake_provider)
    ctx = _ctx()
    service = _make_service(ctx)
    service.repository.get_rate_control_status.return_value = {
        "generation": 2,
        "status": "READY",
    }

    outcome = service.execute(
        _payload(workspace_id=ctx.workspace_id, message_id=ctx.message_id)
    )

    assert outcome.outcome == "DEFERRED"
    fake_provider.send_message.assert_not_called()
    service.rate_limiter.release.assert_called_once()


# ---------------------------------------------------------------------------
# Gate rejections that reach SendingService.execute end to end
# ---------------------------------------------------------------------------


def test_suppressed_message_is_skipped_never_calls_provider() -> None:
    fake_provider = MagicMock()
    ProviderRegistry.register("GMAIL", fake_provider)
    ctx = _ctx()
    service = _make_service(ctx)
    service.session.execute.return_value.first.return_value = (1,)  # suppressed

    outcome = service.execute(
        _payload(workspace_id=ctx.workspace_id, message_id=ctx.message_id)
    )

    assert outcome.outcome == "SKIPPED"
    fake_provider.send_message.assert_not_called()
    service.repository.skip_message.assert_called_once()


def test_paused_campaign_defers_leaves_message_untouched() -> None:
    fake_provider = MagicMock()
    ProviderRegistry.register("GMAIL", fake_provider)
    ctx = _ctx(campaign_status="PAUSED")
    service = _make_service(ctx)

    outcome = service.execute(
        _payload(workspace_id=ctx.workspace_id, message_id=ctx.message_id)
    )

    assert outcome.outcome == "DEFERRED"
    fake_provider.send_message.assert_not_called()
    service.repository.skip_message.assert_not_called()
    service.repository.finalize_attempt_result.assert_not_called()


# ---------------------------------------------------------------------------
# Provider outcomes, once authorization succeeds
# ---------------------------------------------------------------------------


def _authorized_service(ctx: LoadedSendContext) -> SendingService:
    service = _make_service(ctx)
    service.repository.get_mailbox_connection.return_value = {
        "credential_ciphertext": b"ciphertext",
        "encryption_key_id": "v1",
        "nonce": b"nonce",
        "protected_config": None,
        "expires_at": None,
    }
    return service


def test_provider_accepted_marks_sent() -> None:
    fake_provider = MagicMock()
    fake_provider.capabilities = frozenset()
    fake_provider.send_message.return_value = ProviderSendResult(
        status="ACCEPTED", provider_message_id="pmid-1"
    )
    ProviderRegistry.register("GMAIL", fake_provider)
    ctx = _ctx()
    service = _authorized_service(ctx)

    with patch(
        "app.modules.sending.service.decrypt_credentials",
        return_value={"access_token": "x"},
    ):
        outcome = service.execute(
            _payload(workspace_id=ctx.workspace_id, message_id=ctx.message_id)
        )

    assert outcome.outcome == "SENT"
    fake_provider.send_message.assert_called_once()
    kwargs = service.repository.finalize_attempt_result.call_args.kwargs
    assert kwargs["message_status"] == "SENT"
    assert kwargs["evidence_state"] == "ACCEPTED"
    assert kwargs["provider_request_id"] == ctx.rfc_message_id


def test_provider_unknown_marks_unknown_outcome_no_retry() -> None:
    fake_provider = MagicMock()
    fake_provider.capabilities = frozenset()
    fake_provider.send_message.return_value = ProviderSendResult(
        status="UNKNOWN", error_category="NETWORK_ERROR", error_code="timeout"
    )
    ProviderRegistry.register("GMAIL", fake_provider)
    ctx = _ctx()
    service = _authorized_service(ctx)

    with patch(
        "app.modules.sending.service.decrypt_credentials",
        return_value={"access_token": "x"},
    ):
        outcome = service.execute(
            _payload(workspace_id=ctx.workspace_id, message_id=ctx.message_id)
        )

    assert outcome.outcome == "UNKNOWN_OUTCOME"
    kwargs = service.repository.finalize_attempt_result.call_args.kwargs
    assert kwargs["message_status"] == "UNKNOWN_OUTCOME"
    assert kwargs["evidence_state"] == "UNKNOWN"
    assert kwargs.get("retry_count") is None  # never auto-retried


def test_provider_exception_treated_as_ambiguous_not_failed() -> None:
    """An exception escaping the adapter must never be classified as a safe
    non-send -- it becomes UNKNOWN_OUTCOME, exactly like a provider timeout."""
    fake_provider = MagicMock()
    fake_provider.capabilities = frozenset()
    fake_provider.send_message.side_effect = RuntimeError("boom")
    ProviderRegistry.register("GMAIL", fake_provider)
    ctx = _ctx()
    service = _authorized_service(ctx)

    with patch(
        "app.modules.sending.service.decrypt_credentials",
        return_value={"access_token": "x"},
    ):
        outcome = service.execute(
            _payload(workspace_id=ctx.workspace_id, message_id=ctx.message_id)
        )

    assert outcome.outcome == "UNKNOWN_OUTCOME"


def test_provider_permanent_rejection_marks_failed_no_retry() -> None:
    fake_provider = MagicMock()
    fake_provider.capabilities = frozenset()
    fake_provider.send_message.return_value = ProviderSendResult(
        status="DEFINITIVELY_REJECTED",
        error_category="PERMANENT_RECIPIENT_FAILURE",
        error_code="invalid_recipient",
    )
    ProviderRegistry.register("GMAIL", fake_provider)
    ctx = _ctx()
    service = _authorized_service(ctx)

    with patch(
        "app.modules.sending.service.decrypt_credentials",
        return_value={"access_token": "x"},
    ):
        outcome = service.execute(
            _payload(workspace_id=ctx.workspace_id, message_id=ctx.message_id)
        )

    assert outcome.outcome == "FAILED"
    kwargs = service.repository.finalize_attempt_result.call_args.kwargs
    assert kwargs["message_status"] == "FAILED"
    assert kwargs["terminal_reason"]


def test_provider_transient_rejection_schedules_retry() -> None:
    fake_provider = MagicMock()
    fake_provider.capabilities = frozenset()
    fake_provider.send_message.return_value = ProviderSendResult(
        status="DEFINITIVELY_REJECTED",
        error_category="TEMPORARY_PROVIDER_ERROR",
        error_code="server_5xx",
    )
    ProviderRegistry.register("GMAIL", fake_provider)
    ctx = _ctx(retry_count=0, retry_budget=3)
    service = _authorized_service(ctx)

    with patch(
        "app.modules.sending.service.decrypt_credentials",
        return_value={"access_token": "x"},
    ):
        outcome = service.execute(
            _payload(workspace_id=ctx.workspace_id, message_id=ctx.message_id)
        )

    assert outcome.outcome == "RETRY_SCHEDULED"
    kwargs = service.repository.finalize_attempt_result.call_args.kwargs
    assert kwargs["message_status"] == "RETRY_SCHEDULED"
    assert kwargs["retry_count"] == 1
    assert kwargs["next_retry_at"] is not None
    assert kwargs["due_at"] == kwargs["next_retry_at"]


def test_retry_budget_exhausted_fails_instead_of_retrying() -> None:
    fake_provider = MagicMock()
    fake_provider.capabilities = frozenset()
    fake_provider.send_message.return_value = ProviderSendResult(
        status="DEFINITIVELY_REJECTED",
        error_category="TEMPORARY_PROVIDER_ERROR",
        error_code="server_5xx",
    )
    ProviderRegistry.register("GMAIL", fake_provider)
    ctx = _ctx(retry_count=3, retry_budget=3)
    service = _authorized_service(ctx)

    with patch(
        "app.modules.sending.service.decrypt_credentials",
        return_value={"access_token": "x"},
    ):
        outcome = service.execute(
            _payload(workspace_id=ctx.workspace_id, message_id=ctx.message_id)
        )

    assert outcome.outcome == "FAILED"


def test_credential_missing_marks_not_invoked_failed_never_calls_provider() -> None:
    fake_provider = MagicMock()
    fake_provider.capabilities = frozenset()
    ProviderRegistry.register("GMAIL", fake_provider)
    ctx = _ctx()
    service = _authorized_service(ctx)
    service.repository.get_mailbox_connection.return_value = None

    outcome = service.execute(
        _payload(workspace_id=ctx.workspace_id, message_id=ctx.message_id)
    )

    assert outcome.outcome == "FAILED"
    fake_provider.send_message.assert_not_called()
    kwargs = service.repository.finalize_attempt_result.call_args.kwargs
    assert kwargs["evidence_state"] == "NOT_INVOKED"


# ---------------------------------------------------------------------------
# Message-ID capture and open-tracking pixel (reply/bounce correlation)
# ---------------------------------------------------------------------------


def _send_capturing_envelope(
    ctx: LoadedSendContext, result: ProviderSendResult, **settings_overrides: object
):
    fake_provider = MagicMock()
    fake_provider.capabilities = frozenset()
    fake_provider.send_message.return_value = result
    ProviderRegistry.register("GMAIL", fake_provider)
    service = _authorized_service(ctx)
    configured = Settings.current().model_copy(update=settings_overrides)
    with (
        patch(
            "app.modules.sending.service.decrypt_credentials",
            return_value={"access_token": "x"},
        ),
        patch("app.modules.sending.service.Settings.current", return_value=configured),
    ):
        service.execute(
            _payload(workspace_id=ctx.workspace_id, message_id=ctx.message_id)
        )
    envelope = fake_provider.send_message.call_args.args[1]
    return envelope, service.repository.finalize_attempt_result.call_args.kwargs


def test_a_message_id_is_generated_when_the_message_has_none_and_recorded() -> None:
    ctx = _ctx(rfc_message_id=None)
    envelope, kwargs = _send_capturing_envelope(
        ctx, ProviderSendResult(status="ACCEPTED", provider_message_id="pmid-1")
    )
    assert envelope.rfc_message_id and envelope.rfc_message_id.endswith("@example.com>")
    # The id that went out is the id recorded (and used as acceptance evidence).
    assert kwargs["rfc_message_id"] == envelope.rfc_message_id
    assert kwargs["provider_request_id"] == envelope.rfc_message_id


def test_the_message_id_the_provider_reports_wins() -> None:
    """Gmail/Graph may assign their own Message-ID; that is what replies quote."""
    ctx = _ctx(rfc_message_id=None)
    envelope, kwargs = _send_capturing_envelope(
        ctx,
        ProviderSendResult(
            status="ACCEPTED",
            provider_message_id="pmid-1",
            provider_thread_id="thread-9",
            rfc_message_id="<provider-assigned@mail.gmail.com>",
        ),
    )
    assert envelope.rfc_message_id != "<provider-assigned@mail.gmail.com>"
    assert kwargs["rfc_message_id"] == "<provider-assigned@mail.gmail.com>"
    assert kwargs["provider_thread_id"] == "thread-9"


def test_an_existing_message_id_is_reused_not_regenerated() -> None:
    ctx = _ctx(rfc_message_id="abc@example.com")
    envelope, _ = _send_capturing_envelope(
        ctx, ProviderSendResult(status="ACCEPTED", provider_message_id="p")
    )
    assert envelope.rfc_message_id == "abc@example.com"


def test_an_ambiguous_send_still_records_the_message_id_for_reconciliation() -> None:
    ctx = _ctx(rfc_message_id=None)
    envelope, kwargs = _send_capturing_envelope(
        ctx,
        ProviderSendResult(
            status="UNKNOWN", error_category="NETWORK_ERROR", error_code="timeout"
        ),
    )
    assert kwargs["message_status"] == "UNKNOWN_OUTCOME"
    assert kwargs["rfc_message_id"] == envelope.rfc_message_id


def test_a_rejected_send_does_not_record_a_message_id() -> None:
    ctx = _ctx(rfc_message_id=None)
    _, kwargs = _send_capturing_envelope(
        ctx,
        ProviderSendResult(
            status="DEFINITIVELY_REJECTED",
            error_category="PERMANENT_RECIPIENT_FAILURE",
            error_code="x",
        ),
    )
    assert kwargs.get("rfc_message_id") is None


TRACKING = dict(
    open_tracking_enabled=True,
    tracking_base_url="https://outly.example.com",
    tracking_signing_key="k" * 16,
)


def test_open_pixel_is_added_at_send_time_naming_this_message() -> None:
    from app.modules.tracking.tokens import parse_open_token

    ctx = _ctx()
    envelope, _ = _send_capturing_envelope(
        ctx, ProviderSendResult(status="ACCEPTED", provider_message_id="p"), **TRACKING
    )
    assert "/api/v1/t/o/" in envelope.body_html
    assert envelope.body_html.startswith("<p>Hi</p>")
    token = envelope.body_html.split("/api/v1/t/o/")[1].split(".gif")[0]
    parsed = parse_open_token(token, TRACKING["tracking_signing_key"])
    assert parsed == (ctx.workspace_id, ctx.message_id)
    # The frozen snapshot is untouched: the pixel exists only on the envelope.
    assert ctx.content_body_html == "<p>Hi</p>"


def test_no_pixel_when_tracking_is_off_or_unconfigured() -> None:
    accepted = ProviderSendResult(status="ACCEPTED", provider_message_id="p")
    for overrides in (
        {},
        {**TRACKING, "open_tracking_enabled": False},
        {**TRACKING, "tracking_signing_key": ""},
    ):
        envelope, _ = _send_capturing_envelope(_ctx(), accepted, **overrides)
        assert envelope.body_html == "<p>Hi</p>"


def test_the_pixel_does_not_break_the_content_digest_check() -> None:
    """Adding the pixel must not make the send gate think the body changed."""
    accepted = ProviderSendResult(status="ACCEPTED", provider_message_id="p")
    envelope, kwargs = _send_capturing_envelope(_ctx(), accepted, **TRACKING)
    assert kwargs["message_status"] == "SENT" and "<img" in envelope.body_html
