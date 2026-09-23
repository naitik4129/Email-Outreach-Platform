from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.crypto import decrypt_credentials, encrypt_credentials
from app.core.errors import AppError
from app.modules.mailboxes.providers.base import (
    EmailProvider,
    OutboundMessageEnvelope,
    ProviderCapability,
    ProviderSendResult,
)
from app.modules.mailboxes.providers.registry import ProviderRegistry
from app.modules.rate_limit.limiter import RedisRateLimiter
from app.modules.rate_limit.repository import RatePolicyRepository
from app.modules.rate_limit.schemas import (
    RateLimitDenied,
    RateLimitGenerationStale,
    RateLimitUnavailable,
    RateReservationRequest,
)
from app.modules.scheduler.schemas import SendTaskPayload
from app.modules.sending import gates
from app.modules.sending.repository import (
    AttemptAlreadyClaimed,
    MessageLockedByAnotherWorker,
    SendingRepository,
)
from app.modules.sending.retry_policy import classify_and_decide
from app.modules.sending.schemas import LoadedSendContext, SendOutcome

logger = logging.getLogger(__name__)

CAPACITY_UNIT = "MESSAGE"
AUTHORIZATION_WINDOW_SECONDS = 30
CREDENTIAL_REFRESH_SAFETY_MARGIN = timedelta(minutes=5)


class SendingService:
    """Orchestrates one `email.send` task end to end. The worker MUST NEVER
    make a send decision from the task payload alone -- every method here
    reloads authoritative PostgreSQL state before deciding anything, and the
    provider is invoked only after a committed Postgres authorization
    transaction with no open lock or transaction held (see module-level
    flow in the Phase 10 plan / docs/architecture/*).
    """

    def __init__(
        self,
        session: Session,
        settings: Settings | None = None,
        rate_limiter: RedisRateLimiter | None = None,
    ) -> None:
        self.session = session
        self.settings = settings or Settings.current()
        self.repository = SendingRepository(session)
        self.rate_policy_repository = RatePolicyRepository(session)
        self.rate_limiter = rate_limiter or RedisRateLimiter(settings=self.settings)

    def execute(self, payload: SendTaskPayload) -> SendOutcome:
        workspace_id = UUID(payload.workspace_id)
        message_id = UUID(payload.message_id)

        try:
            ctx = self.repository.load_message_for_send(
                workspace_id=workspace_id, message_id=message_id
            )
        except MessageLockedByAnotherWorker:
            self.session.rollback()
            logger.info(
                "Message locked by a concurrent worker; safe no-op",
                extra={"message_id": str(message_id)},
            )
            return SendOutcome(message_id=message_id, outcome="NOOP", reason="locked")

        if ctx is None:
            self.session.rollback()
            logger.warning(
                "email.send task references a message that does not exist; "
                "terminal, non-retryable",
                extra={"message_id": str(message_id)},
            )
            return SendOutcome(
                message_id=message_id, outcome="NOOP", reason="message_not_found"
            )

        try:
            gates.run_pre_authorization_gates(
                self.session,
                ctx,
                expected_dispatch_generation=payload.dispatch_generation,
            )
        except (gates.MessageAlreadyResolved, gates.StaleDispatchGeneration) as exc:
            self.session.rollback()
            return SendOutcome(
                message_id=message_id, outcome="NOOP", reason=exc.terminal_reason
            )
        except (
            gates.CampaignStateRejected,
            gates.EnrollmentStateRejected,
            gates.MailboxStateRejected,
        ) as exc:
            # Temporarily ineligible, may become eligible again later (e.g.
            # campaign resumes). Leave the message QUEUED and untouched --
            # Phase 9's existing claim-lease recovery sweep will revert it
            # to SCHEDULED/RETRY_SCHEDULED once claim_expires_at passes, and
            # the scheduler will reconsider it naturally. No destructive
            # state change here.
            self._audit(ctx, action="message.send_deferred", reason=exc.terminal_reason)
            self.session.commit()
            logger.info(
                "Send deferred (temporary gate rejection)",
                extra={"message_id": str(message_id), "reason": exc.terminal_reason},
            )
            return SendOutcome(
                message_id=message_id, outcome="DEFERRED", reason=exc.terminal_reason
            )
        except gates.SendGateRejected as exc:
            # Permanent for this message as constructed (suppressed,
            # invalid recipient, corrupted snapshot, cross-tenant/provider
            # mismatch) -- terminal SKIPPED, never sent.
            return self._skip(ctx, exc.terminal_reason)

        return self._authorize_and_send(ctx, payload)

    # -------------------------------------------------------------------------
    # Rate reservation + authorization transaction
    # -------------------------------------------------------------------------

    def _authorize_and_send(
        self, ctx: LoadedSendContext, payload: SendTaskPayload
    ) -> SendOutcome:
        policies = self.rate_policy_repository.resolve_applicable_policies(
            workspace_id=ctx.workspace_id,
            campaign_id=ctx.campaign_id,
            mailbox_id=ctx.mailbox_id,
            provider=ctx.mailbox_provider,
            provider_account_id=ctx.mailbox_provider_account_id,
            unit=CAPACITY_UNIT,
        )

        try:
            expected_generation = self.rate_limiter.current_generation()
            if expected_generation is None:
                raise RateLimitGenerationStale("no generation set")
            reservation = self.rate_limiter.reserve(
                RateReservationRequest(
                    message_id=str(ctx.message_id), quantity=1, policies=tuple(policies)
                ),
                expected_generation=expected_generation,
            )
        except RateLimitUnavailable as exc:
            # Fail closed: no provider call without a confirmed-healthy
            # limiter. RATE_LIMITING.md: "A missing key never means an
            # unused full budget."
            self.session.rollback()
            self._audit(ctx, action="message.rate_limit_unavailable", reason=str(exc))
            self.session.commit()
            logger.warning(
                "Rate limiter unavailable; deferring send (fail closed)",
                extra={"message_id": str(ctx.message_id), "error": str(exc)},
            )
            return SendOutcome(
                message_id=ctx.message_id,
                outcome="DEFERRED",
                reason="rate_limit_unavailable",
            )
        except RateLimitDenied as exc:
            self.session.rollback()
            logger.info(
                "Rate limit capacity denied; deferring send",
                extra={
                    "message_id": str(ctx.message_id),
                    "reason": exc.denial.reason,
                    "failing_kind": exc.denial.failing_kind,
                },
            )
            return SendOutcome(
                message_id=ctx.message_id,
                outcome="DEFERRED",
                reason="rate_limit_denied",
            )

        try:
            outcome = self._run_authorization_transaction(
                ctx, payload, policies, reservation, expected_generation
            )
        except _AuthorizationAborted as exc:
            self.rate_limiter.release(reservation.reservation_id)
            self.session.rollback()
            return SendOutcome(
                message_id=ctx.message_id, outcome="DEFERRED", reason=exc.reason
            )
        except AttemptAlreadyClaimed:
            self.rate_limiter.release(reservation.reservation_id)
            self.session.rollback()
            logger.info(
                "Attempt already claimed by a concurrent worker; safe no-op",
                extra={"message_id": str(ctx.message_id)},
            )
            return SendOutcome(
                message_id=ctx.message_id, outcome="NOOP", reason="already_claimed"
            )

        # Authorization transaction committed: message is now SENDING, a
        # PREPARED attempt exists, and capacity has been durably debited.
        # From here on, capacity is NEVER released/refunded regardless of
        # outcome (RATE_LIMITING.md: no refunds for uncertain provider
        # usage) -- only the pre-commit abort paths above call release().
        return self._invoke_provider_and_finalize(*outcome)

    def _run_authorization_transaction(
        self,
        ctx: LoadedSendContext,
        payload: SendTaskPayload,
        policies: list[Any],
        reservation: Any,
        expected_generation: int,
    ) -> tuple[UUID, LoadedSendContext, int]:
        """Re-verify everything under a fresh, lock-holding reload, then
        claim the attempt and commit SENDING atomically. Returns
        (attempt_id, fresh_ctx, credential_generation) for the caller to use
        outside this (now-committed) transaction."""
        rate_status = self.repository.get_rate_control_status()
        if (
            rate_status is None
            or rate_status["status"] != "READY"
            or int(rate_status["generation"]) != reservation.generation
        ):
            raise _AuthorizationAborted("rate_control_generation_mismatch")

        try:
            fresh_ctx = self.repository.load_message_for_send(
                workspace_id=ctx.workspace_id, message_id=ctx.message_id
            )
        except MessageLockedByAnotherWorker:
            raise _AuthorizationAborted("locked_by_concurrent_worker") from None
        if fresh_ctx is None:
            raise _AuthorizationAborted("message_not_found")

        try:
            gates.run_pre_authorization_gates(
                self.session,
                fresh_ctx,
                expected_dispatch_generation=payload.dispatch_generation,
            )
        except gates.SendGateRejected as exc:
            raise _AuthorizationAborted(exc.terminal_reason) from exc

        attempt_id = uuid4()
        ordinal = self.repository.next_attempt_ordinal(
            workspace_id=fresh_ctx.workspace_id, message_id=fresh_ctx.message_id
        )
        now = datetime.now(UTC)
        self.repository.insert_prepared_attempt(
            attempt_id=attempt_id,
            workspace_id=fresh_ctx.workspace_id,
            message_id=fresh_ctx.message_id,
            mailbox_id=fresh_ctx.mailbox_id,
            ordinal=ordinal,
            invocation_owner=f"worker-send:{payload.work_id}",
            credential_generation=fresh_ctx.mailbox_current_connection_generation,
            authorization_deadline=now
            + timedelta(seconds=AUTHORIZATION_WINDOW_SECONDS),
            dispatch_generation=fresh_ctx.dispatch_generation,
        )

        scope_rows = [
            {
                "rate_scope_id": (
                    p.source_id
                    if p.kind.value in ("PLATFORM", "PROVIDER", "PROVIDER_ACCOUNT")
                    else None
                ),
                "tenant_rate_policy_id": (
                    p.source_id
                    if p.kind.value in ("WORKSPACE", "CAMPAIGN", "MAILBOX")
                    else None
                ),
                "policy_version": p.policy_version,
                "policy_snapshot": {
                    "kind": p.kind.value,
                    "scope_key": p.scope_key,
                    "window_seconds": p.window_seconds,
                    "limit_value": p.limit_value,
                },
                "window_kind": p.window_kind,
            }
            for p in policies
        ]
        self.repository.insert_capacity_debit(
            workspace_id=fresh_ctx.workspace_id,
            attempt_id=attempt_id,
            reservation_id=reservation.reservation_id,
            unit=CAPACITY_UNIT,
            quantity=1,
            scopes=scope_rows,
        )

        transitioned = self.repository.transition_message_to_sending(
            workspace_id=fresh_ctx.workspace_id,
            message_id=fresh_ctx.message_id,
            expected_version=fresh_ctx.message_version,
        )
        if not transitioned:
            raise _AuthorizationAborted("message_version_conflict")

        self.session.commit()
        return attempt_id, fresh_ctx, fresh_ctx.mailbox_current_connection_generation

    # -------------------------------------------------------------------------
    # Provider invocation (no open transaction/lock) + result persistence
    # -------------------------------------------------------------------------

    def _invoke_provider_and_finalize(
        self, attempt_id: UUID, ctx: LoadedSendContext, credential_generation: int
    ) -> SendOutcome:
        provider = ProviderRegistry.get(ctx.mailbox_provider)

        try:
            credential, credential_generation = self._load_and_refresh_credential(
                ctx, provider, credential_generation
            )
        except _CredentialFailure as exc:
            self._finalize_not_invoked(ctx, attempt_id, exc)
            self.session.commit()
            return SendOutcome(
                message_id=ctx.message_id,
                outcome=exc.message_status,
                reason=exc.error_category,
                attempt_id=attempt_id,
            )

        envelope = OutboundMessageEnvelope(
            to_address=ctx.frozen_destination or "",
            from_address=ctx.frozen_sender_address or ctx.mailbox_original_address,
            from_name=ctx.frozen_sender_name or ctx.mailbox_sender_display_name,
            subject=ctx.content_subject or "",
            body_html=ctx.content_body_html or "",
            rfc_message_id=ctx.rfc_message_id,
        )

        try:
            send_result = provider.send_message(credential, envelope)
        except Exception as exc:  # noqa: BLE001 -- see comment below
            # Any exception escaping the adapter is treated as ambiguous,
            # never as "definitely not sent": the adapters are documented to
            # return ProviderSendResult(status="UNKNOWN"/"DEFINITIVELY_REJECTED")
            # for every case they can classify, so an escaping exception
            # means this worker cannot determine what happened. Per
            # MESSAGE_STATE_MACHINE.md / RATE_LIMITING.md: never blindly
            # treat an unclassified failure as safe-to-retry.
            logger.error(
                "Unexpected exception from provider adapter; treating as "
                "ambiguous outcome",
                extra={
                    "message_id": str(ctx.message_id),
                    "provider": ctx.mailbox_provider,
                },
            )
            send_result = ProviderSendResult(
                status="UNKNOWN",
                error_category="NETWORK_ERROR",
                error_code=type(exc).__name__,
            )

        outcome = self._finalize_send_result(ctx, attempt_id, send_result)
        self.session.commit()
        return outcome

    def _load_and_refresh_credential(
        self,
        ctx: LoadedSendContext,
        provider: EmailProvider,
        credential_generation: int,
    ) -> tuple[dict[str, Any], int]:
        conn = self.repository.get_mailbox_connection(
            workspace_id=ctx.workspace_id,
            mailbox_id=ctx.mailbox_id,
            generation=credential_generation,
        )
        if not conn or not conn["credential_ciphertext"]:
            raise _CredentialFailure(
                error_category="CONFIGURATION_FAILURE",
                error_code="credentials_missing",
                message_status="FAILED",
            )

        try:
            creds = decrypt_credentials(
                conn["credential_ciphertext"],
                conn["nonce"],
                ctx.workspace_id,
                ctx.mailbox_id,
                ctx.mailbox_provider,
                conn["encryption_key_id"],
            )
        except AppError:
            raise _CredentialFailure(
                error_category="CONFIGURATION_FAILURE",
                error_code="credential_decrypt_failed",
                message_status="FAILED",
            ) from None

        credential: dict[str, Any] = dict(creds)
        if conn.get("protected_config"):
            credential.update(conn["protected_config"])

        expires_at = conn["expires_at"]
        now = datetime.now(UTC)
        if (
            ProviderCapability.CREDENTIAL_REFRESH in provider.capabilities
            and expires_at
            and (now + CREDENTIAL_REFRESH_SAFETY_MARGIN >= expires_at)
        ):
            refresh_token = creds.get("refresh_token")
            if not refresh_token:
                self.repository.update_mailbox_connection_state(
                    workspace_id=ctx.workspace_id,
                    mailbox_id=ctx.mailbox_id,
                    connection_state="RECONNECT_REQUIRED",
                    health_state="DEGRADED",
                    connected_generation=None,
                    current_connection_generation=credential_generation,
                )
                raise _CredentialFailure(
                    error_category="AUTH_FAILURE",
                    error_code="refresh_token_unavailable",
                    message_status="FAILED",
                )
            try:
                refresh_res = provider.refresh_token(refresh_token)
            except AppError:
                self.repository.update_mailbox_connection_state(
                    workspace_id=ctx.workspace_id,
                    mailbox_id=ctx.mailbox_id,
                    connection_state="RECONNECT_REQUIRED",
                    health_state="DEGRADED",
                    connected_generation=None,
                    current_connection_generation=credential_generation,
                )
                raise _CredentialFailure(
                    error_category="AUTH_FAILURE",
                    error_code="refresh_failed",
                    message_status="FAILED",
                ) from None

            new_refresh_token = refresh_res.refresh_token or refresh_token
            new_ct, new_key_id, new_nonce = encrypt_credentials(
                {
                    "access_token": refresh_res.access_token,
                    "refresh_token": new_refresh_token,
                    "token_type": refresh_res.token_type,
                },
                ctx.workspace_id,
                ctx.mailbox_id,
                ctx.mailbox_provider,
            )
            new_generation = credential_generation + 1
            self.repository.insert_mailbox_connection(
                connection_id=uuid4(),
                workspace_id=ctx.workspace_id,
                mailbox_id=ctx.mailbox_id,
                generation=new_generation,
                credential_ciphertext=new_ct,
                encryption_key_id=new_key_id,
                nonce=new_nonce,
                auth_mechanism="OAUTH",
                granted_scopes=refresh_res.granted_scopes,
                expires_at=now + timedelta(seconds=refresh_res.expires_in),
            )
            self.repository.update_mailbox_connection_state(
                workspace_id=ctx.workspace_id,
                mailbox_id=ctx.mailbox_id,
                connection_state="CONNECTED",
                health_state="HEALTHY",
                connected_generation=new_generation,
                current_connection_generation=new_generation,
            )
            credential["access_token"] = refresh_res.access_token
            credential_generation = new_generation

        return credential, credential_generation

    def _finalize_send_result(
        self, ctx: LoadedSendContext, attempt_id: UUID, send_result: ProviderSendResult
    ) -> SendOutcome:
        if send_result.status == "ACCEPTED":
            self.repository.finalize_attempt_result(
                workspace_id=ctx.workspace_id,
                message_id=ctx.message_id,
                attempt_id=attempt_id,
                message_status="SENT",
                evidence_state="ACCEPTED",
                provider_message_id=send_result.provider_message_id,
                provider_thread_id=send_result.provider_thread_id,
                # Locally-generated attempt identity, always present as
                # acceptance evidence regardless of whether the provider
                # itself returned a message id (Graph/SMTP often don't) --
                # required by message_attempts_acceptance_evidence_check.
                provider_request_id=ctx.rfc_message_id,
            )
            self._audit(ctx, action="message.sent", reason=None)
            return SendOutcome(
                message_id=ctx.message_id,
                outcome="SENT",
                attempt_id=attempt_id,
                provider_message_id=send_result.provider_message_id,
            )

        if send_result.status == "UNKNOWN":
            self.repository.finalize_attempt_result(
                workspace_id=ctx.workspace_id,
                message_id=ctx.message_id,
                attempt_id=attempt_id,
                message_status="UNKNOWN_OUTCOME",
                evidence_state="UNKNOWN",
                error_category=send_result.error_category,
                error_code=send_result.error_code,
            )
            self._audit(
                ctx,
                action="message.ambiguous_outcome",
                reason=send_result.error_category,
            )
            return SendOutcome(
                message_id=ctx.message_id,
                outcome="UNKNOWN_OUTCOME",
                reason=send_result.error_category,
                attempt_id=attempt_id,
            )

        # DEFINITIVELY_REJECTED
        decision = classify_and_decide(
            error_category=send_result.error_category,
            is_retryable=None,
            retry_count=ctx.retry_count,
            retry_budget=ctx.retry_budget,
        )
        if decision.should_retry:
            self.repository.finalize_attempt_result(
                workspace_id=ctx.workspace_id,
                message_id=ctx.message_id,
                attempt_id=attempt_id,
                message_status="RETRY_SCHEDULED",
                evidence_state="REJECTED",
                error_category=send_result.error_category,
                error_code=send_result.error_code,
                retry_count=ctx.retry_count + 1,
                next_retry_at=decision.next_retry_at,
                due_at=decision.next_retry_at,
            )
            self._audit(
                ctx, action="message.retry_scheduled", reason=send_result.error_category
            )
            return SendOutcome(
                message_id=ctx.message_id,
                outcome="RETRY_SCHEDULED",
                reason=send_result.error_category,
                attempt_id=attempt_id,
            )

        self.repository.finalize_attempt_result(
            workspace_id=ctx.workspace_id,
            message_id=ctx.message_id,
            attempt_id=attempt_id,
            message_status="FAILED",
            evidence_state="REJECTED",
            error_category=send_result.error_category,
            error_code=send_result.error_code,
            terminal_reason=decision.terminal_reason or "send_rejected",
        )
        self._audit(ctx, action="message.failed", reason=send_result.error_category)
        return SendOutcome(
            message_id=ctx.message_id,
            outcome="FAILED",
            reason=send_result.error_category,
            attempt_id=attempt_id,
        )

    def _finalize_not_invoked(
        self, ctx: LoadedSendContext, attempt_id: UUID, failure: _CredentialFailure
    ) -> None:
        self.repository.finalize_attempt_result(
            workspace_id=ctx.workspace_id,
            message_id=ctx.message_id,
            attempt_id=attempt_id,
            message_status=failure.message_status,
            evidence_state="NOT_INVOKED",
            error_category=failure.error_category,
            error_code=failure.error_code,
            terminal_reason=(
                failure.error_code if failure.message_status == "FAILED" else None
            ),
        )
        self._audit(ctx, action="message.credential_failure", reason=failure.error_code)

    # -------------------------------------------------------------------------
    # Shared helpers
    # -------------------------------------------------------------------------

    def _skip(self, ctx: LoadedSendContext, terminal_reason: str) -> SendOutcome:
        self.repository.skip_message(
            workspace_id=ctx.workspace_id,
            message_id=ctx.message_id,
            expected_version=ctx.message_version,
            terminal_reason=terminal_reason,
        )
        self._audit(ctx, action="message.skipped", reason=terminal_reason)
        self.session.commit()
        logger.info(
            "Message skipped (terminal gate rejection)",
            extra={"message_id": str(ctx.message_id), "reason": terminal_reason},
        )
        return SendOutcome(
            message_id=ctx.message_id, outcome="SKIPPED", reason=terminal_reason
        )

    def _audit(
        self, ctx: LoadedSendContext, *, action: str, reason: str | None
    ) -> None:
        self.repository.record_audit_event(
            workspace_id=ctx.workspace_id,
            action=action,
            target_id=ctx.message_id,
            after_state={"mailbox_id": str(ctx.mailbox_id), "purpose": ctx.purpose},
            reason=reason,
        )


class _AuthorizationAborted(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class _CredentialFailure(Exception):
    def __init__(
        self, *, error_category: str, error_code: str, message_status: str
    ) -> None:
        self.error_category = error_category
        self.error_code = error_code
        self.message_status = message_status
        super().__init__(error_code)
