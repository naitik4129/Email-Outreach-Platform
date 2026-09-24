from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.crypto import decrypt_credentials, encrypt_credentials
from app.core.errors import AppError
from app.core.metrics import (
    record_campaign_stopped_by_reply,
    record_future_message_cancelled_by_reply,
    record_inbound_message_deduplicated,
    record_reply_matched,
    record_reply_sync_messages_discovered,
    record_reply_sync_run,
    record_sync_resync_triggered,
)
from app.modules.mailboxes.providers.base import (
    EmailProvider,
    ProviderCapability,
)
from app.modules.mailboxes.providers.gmail import GmailProvider
from app.modules.mailboxes.providers.microsoft import MicrosoftGraphProvider
from app.modules.mailboxes.providers.smtp import SmtpProvider
from app.modules.replies.matcher import ReplyMatcher
from app.modules.replies.normalizer import normalize_inbound_message
from app.modules.replies.repository import ReplyRepository
from app.modules.replies.schemas import (
    InboundClassification,
    MailboxSyncResult,
    SyncCheckpoint,
)

logger = logging.getLogger(__name__)

CREDENTIAL_REFRESH_SAFETY_MARGIN = timedelta(minutes=5)


class ReplySyncService:
    """Orchestrates inbound reply synchronization, provider delta tracking,
    conservative correlation, and campaign safety enforcement.
    """

    def __init__(
        self,
        session: Session,
        providers: dict[str, EmailProvider] | None = None,
    ) -> None:
        self.session = session
        self.repository = ReplyRepository(session)
        self.providers: dict[str, EmailProvider] = providers or {
            "GMAIL": GmailProvider(),
            "MICROSOFT": MicrosoftGraphProvider(),
            "SMTP": SmtpProvider(),
        }

    def sync_mailbox(
        self,
        *,
        workspace_id: UUID,
        mailbox_id: UUID,
        lease_owner: str = "worker-sync",
        max_pages: int = 10,
        lease_duration_seconds: int = 120,
        sync_interval_seconds: int = 300,
    ) -> MailboxSyncResult:
        """Execute incremental reply synchronization for a single connected mailbox."""
        mailbox = self.repository.get_mailbox_for_sync(
            workspace_id=workspace_id,
            mailbox_id=mailbox_id,
        )
        if not mailbox:
            logger.warning("Mailbox %s not found for sync", mailbox_id)
            return MailboxSyncResult(
                mailbox_id=mailbox_id,
                workspace_id=workspace_id,
                status="NOT_FOUND",
                error="Mailbox not found",
            )

        provider_name = (mailbox["provider"] or "").upper()
        provider = self.providers.get(provider_name)
        if not provider or ProviderCapability.REPLY_SYNC not in provider.capabilities:
            logger.info("Provider %s does not support REPLY_SYNC; skipping", provider_name)
            self.repository.advance_sync_checkpoint(
                workspace_id=workspace_id,
                mailbox_id=mailbox_id,
                cursor_data="",
                status="UNAVAILABLE",
            )
            self.session.commit()
            return MailboxSyncResult(
                mailbox_id=mailbox_id,
                workspace_id=workspace_id,
                status="UNSUPPORTED_PROVIDER",
            )

        if mailbox["connection_state"] != "CONNECTED":
            logger.info(
                "Mailbox %s is not CONNECTED (state=%s); skipping sync",
                mailbox_id,
                mailbox["connection_state"],
            )
            return MailboxSyncResult(
                mailbox_id=mailbox_id,
                workspace_id=workspace_id,
                status="NOT_CONNECTED",
                error=f"Mailbox state is {mailbox['connection_state']}",
            )

        # Ensure sync state exists
        connection_generation = mailbox["current_connection_generation"]
        self.repository.ensure_sync_state(
            workspace_id=workspace_id,
            mailbox_id=mailbox_id,
            connection_generation=connection_generation,
        )
        self.session.commit()

        # Acquire distributed lease
        lease = self.repository.acquire_sync_lease(
            workspace_id=workspace_id,
            mailbox_id=mailbox_id,
            lease_owner=lease_owner,
            lease_duration_seconds=lease_duration_seconds,
        )
        self.session.commit()

        if not lease:
            logger.debug("Sync lease for mailbox %s is held by another worker", mailbox_id)
            return MailboxSyncResult(
                mailbox_id=mailbox_id,
                workspace_id=workspace_id,
                status="LOCKED",
                error="Sync lease held by another worker",
            )

        # Execute sync under lease
        try:
            result = self._execute_sync(
                workspace_id=workspace_id,
                mailbox_id=mailbox_id,
                provider_name=provider_name,
                provider=provider,
                sync_state=lease,
                lease_owner=lease_owner,
                max_pages=max_pages,
                sync_interval_seconds=sync_interval_seconds,
            )
            record_reply_sync_run(provider_name, result.status)
            return result
        except Exception as e:
            logger.exception("Error syncing mailbox %s: %s", mailbox_id, e)
            self.session.rollback()
            self.repository.release_sync_lease(
                workspace_id=workspace_id,
                mailbox_id=mailbox_id,
                lease_owner=lease_owner,
                next_due_at=datetime.now(UTC) + timedelta(minutes=2),
                failure=True,
            )
            self.session.commit()
            record_reply_sync_run(provider_name, "error")
            return MailboxSyncResult(
                mailbox_id=mailbox_id,
                workspace_id=workspace_id,
                status="ERROR",
                error=str(e),
            )

    def _execute_sync(
        self,
        *,
        workspace_id: UUID,
        mailbox_id: UUID,
        provider_name: str,
        provider: EmailProvider,
        sync_state: dict[str, Any],
        lease_owner: str,
        max_pages: int,
        sync_interval_seconds: int,
    ) -> MailboxSyncResult:
        now = datetime.now(UTC)
        connection_generation = sync_state["connection_generation"]

        # Load and refresh credential
        credential, cred_gen = self._load_and_refresh_credential(
            workspace_id=workspace_id,
            mailbox_id=mailbox_id,
            provider_name=provider_name,
            provider=provider,
            credential_generation=connection_generation,
        )

        # Parse checkpoint
        checkpoint = self._load_checkpoint(
            provider_name=provider_name,
            cursor_data=sync_state.get("cursor_data"),
            is_resync=sync_state.get("status") == "RESYNC_REQUIRED",
        )

        discovered = 0
        persisted = 0
        deduplicated = 0
        matched = 0
        unresolved = 0
        stopped_enr = 0
        cancelled_msg = 0
        pages_processed = 0

        while pages_processed < max_pages:
            page_result = provider.sync_inbound_messages(
                credential=credential,
                cursor=checkpoint.confirmed_cursor,
                page_token=checkpoint.pending_page_token,
                max_results=50,
            )

            # Check if resync was triggered by provider (e.g. 404/410 cursor expiration)
            if page_result.resync_required:
                logger.warning(
                    "Resync required reported by provider %s for mailbox %s",
                    provider_name,
                    mailbox_id,
                )
                self.repository.mark_resync_required(
                    workspace_id=workspace_id,
                    mailbox_id=mailbox_id,
                )
                self.repository.insert_safety_hold(
                    workspace_id=workspace_id,
                    mailbox_id=mailbox_id,
                    source_identity=f"resync:{mailbox_id}",
                    reason="Provider checkpoint expired; resync required",
                )
                self.repository.release_sync_lease(
                    workspace_id=workspace_id,
                    mailbox_id=mailbox_id,
                    lease_owner=lease_owner,
                    next_due_at=now,
                )
                self.session.commit()
                record_sync_resync_triggered(provider_name)
                return MailboxSyncResult(
                    mailbox_id=mailbox_id,
                    workspace_id=workspace_id,
                    status="RESYNC_REQUIRED",
                    resync_required=True,
                )

            if page_result.retry_after_seconds and page_result.retry_after_seconds > 0:
                logger.warning(
                    "Provider %s requested rate limit backoff: %s s",
                    provider_name,
                    page_result.retry_after_seconds,
                )
                next_due = now + timedelta(seconds=page_result.retry_after_seconds)
                self.repository.release_sync_lease(
                    workspace_id=workspace_id,
                    mailbox_id=mailbox_id,
                    lease_owner=lease_owner,
                    next_due_at=next_due,
                    failure=False,
                )
                self.session.commit()
                return MailboxSyncResult(
                    mailbox_id=mailbox_id,
                    workspace_id=workspace_id,
                    status="RATE_LIMITED",
                    messages_discovered=discovered,
                    messages_persisted=persisted,
                    messages_deduplicated=deduplicated,
                    replies_matched=matched,
                    replies_unresolved=unresolved,
                    enrollments_stopped=stopped_enr,
                    future_messages_cancelled=cancelled_msg,
                )

            page_messages = page_result.messages
            discovered += len(page_messages)
            record_reply_sync_messages_discovered(provider_name, len(page_messages))

            for raw_msg in page_messages:
                normalized = normalize_inbound_message(raw_msg, provider=provider_name)

                # Find or create conversation
                local_anchor = normalized.rfc_message_id or normalized.provider_message_id
                conv_id = self.repository.find_or_create_conversation(
                    workspace_id=workspace_id,
                    mailbox_id=mailbox_id,
                    provider_thread_id=normalized.provider_thread_id,
                    local_anchor_id=local_anchor,
                    campaign_summary_id=None,
                    activity_at=normalized.received_at,
                )

                # Load outbound candidate messages
                rfc_ids = list(normalized.references)
                if normalized.in_reply_to:
                    rfc_ids.insert(0, normalized.in_reply_to)

                candidates = self.repository.load_outbound_candidates(
                    workspace_id=workspace_id,
                    mailbox_id=mailbox_id,
                    rfc_message_ids=rfc_ids,
                    sender_address=normalized.from_address,
                    provider_thread_id=normalized.provider_thread_id,
                )

                # Match
                match_result = ReplyMatcher.match(normalized, candidates)

                # Insert inbound message (deduplicated)
                inbound_id, is_duplicate = self.repository.insert_inbound_message(
                    workspace_id=workspace_id,
                    mailbox_id=mailbox_id,
                    conversation_id=conv_id,
                    connection_generation=cred_gen,
                    inbound=normalized,
                    association_status=match_result.association_status,
                )

                if is_duplicate or not inbound_id:
                    deduplicated += 1
                    record_inbound_message_deduplicated(provider_name)
                    continue

                persisted += 1

                # Correlation & Campaign Safety
                if match_result.is_matched and match_result.matched_candidate:
                    matched_cand = match_result.matched_candidate
                    matched += 1
                    record_reply_matched(matched_cand.evidence_type, matched_cand.confidence)

                    # Insert confirmed outreach link
                    self.repository.insert_outreach_link(
                        workspace_id=workspace_id,
                        mailbox_id=mailbox_id,
                        inbound_message_id=inbound_id,
                        outbound_message_id=matched_cand.outbound_message_id,
                        campaign_id=matched_cand.campaign_id,
                        enrollment_id=matched_cand.enrollment_id,
                        evidence_type=matched_cand.evidence_type,
                        confidence=matched_cand.confidence,
                        status="CONFIRMED",
                        matched_at=now,
                    )

                    # Qualifying reply check (OOO/auto replies do not terminate campaign)
                    is_qualifying = normalized.classification not in (
                        InboundClassification.OUT_OF_OFFICE.value,
                        InboundClassification.AUTOMATED.value,
                    )

                    if is_qualifying:
                        st_enr, cn_msg = self.repository.record_reply_outcome_and_stop_campaign(
                            workspace_id=workspace_id,
                            enrollment_id=matched_cand.enrollment_id,
                            campaign_id=matched_cand.campaign_id,
                            inbound_message_id=inbound_id,
                            provider_message_id=normalized.provider_message_id,
                            occurred_at=normalized.received_at,
                        )
                        stopped_enr += st_enr
                        cancelled_msg += cn_msg
                        if st_enr > 0:
                            record_campaign_stopped_by_reply()
                        if cn_msg > 0:
                            record_future_message_cancelled_by_reply(cn_msg)
                else:
                    unresolved += 1
                    # Record ambiguous candidate links if present
                    if match_result.candidates:
                        for amb_cand in match_result.candidates:
                            self.repository.insert_outreach_link(
                                workspace_id=workspace_id,
                                mailbox_id=mailbox_id,
                                inbound_message_id=inbound_id,
                                outbound_message_id=amb_cand.outbound_message_id,
                                campaign_id=amb_cand.campaign_id,
                                enrollment_id=amb_cand.enrollment_id,
                                evidence_type=amb_cand.evidence_type,
                                confidence=amb_cand.confidence,
                                status="CANDIDATE",
                            )

            # Update checkpoint state
            if page_result.next_cursor:
                checkpoint.confirmed_cursor = page_result.next_cursor
            checkpoint.pending_page_token = page_result.next_page_token
            checkpoint.last_sync_at = datetime.now(UTC).isoformat()

            new_status = "CURRENT" if not page_result.has_more else "LAGGING"
            self.repository.advance_sync_checkpoint(
                workspace_id=workspace_id,
                mailbox_id=mailbox_id,
                cursor_data=checkpoint.model_dump_json(),
                status=new_status,
            )
            self.session.commit()
            pages_processed += 1

            if not page_result.has_more:
                break

        # Release lease and schedule next sync run
        next_due = datetime.now(UTC) + timedelta(seconds=sync_interval_seconds)
        self.repository.release_sync_lease(
            workspace_id=workspace_id,
            mailbox_id=mailbox_id,
            lease_owner=lease_owner,
            next_due_at=next_due,
            failure=False,
        )
        self.session.commit()

        return MailboxSyncResult(
            mailbox_id=mailbox_id,
            workspace_id=workspace_id,
            status="CURRENT" if checkpoint.pending_page_token is None else "LAGGING",
            messages_discovered=discovered,
            messages_persisted=persisted,
            messages_deduplicated=deduplicated,
            replies_matched=matched,
            replies_unresolved=unresolved,
            enrollments_stopped=stopped_enr,
            future_messages_cancelled=cancelled_msg,
            resync_required=False,
        )

    def _load_checkpoint(
        self,
        provider_name: str,
        cursor_data: str | None,
        is_resync: bool,
    ) -> SyncCheckpoint:
        if is_resync or not cursor_data:
            return SyncCheckpoint(provider=provider_name)
        try:
            data = json.loads(cursor_data)
            cp = SyncCheckpoint.model_validate(data)
            if cp.provider != provider_name:
                return SyncCheckpoint(provider=provider_name)
            return cp
        except Exception:
            logger.warning("Failed to deserialize cursor_data, resetting checkpoint")
            return SyncCheckpoint(provider=provider_name)

    def _load_and_refresh_credential(
        self,
        *,
        workspace_id: UUID,
        mailbox_id: UUID,
        provider_name: str,
        provider: EmailProvider,
        credential_generation: int,
    ) -> tuple[dict[str, Any], int]:
        """Load and decrypt mailbox credential, performing proactive refresh if required."""
        conn = self.repository.get_mailbox_connection(
            workspace_id=workspace_id,
            mailbox_id=mailbox_id,
            generation=credential_generation,
        )
        if not conn or not conn["credential_ciphertext"]:
            raise AppError(
                code="credentials_missing",
                message=f"Credentials missing for mailbox {mailbox_id}",
            )

        creds = decrypt_credentials(
            conn["credential_ciphertext"],
            conn["nonce"],
            workspace_id,
            mailbox_id,
            provider_name,
            conn["encryption_key_id"],
        )
        credential: dict[str, Any] = dict(creds)
        if conn.get("protected_config"):
            credential.update(conn["protected_config"])

        expires_at = conn["expires_at"]
        if expires_at and expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=UTC)

        now = datetime.now(UTC)
        if (
            ProviderCapability.CREDENTIAL_REFRESH in provider.capabilities
            and expires_at
            and (now + CREDENTIAL_REFRESH_SAFETY_MARGIN >= expires_at)
        ):
            refresh_token = creds.get("refresh_token")
            if refresh_token:
                logger.info(
                    "Proactively refreshing access token for mailbox %s (expires at %s)",
                    mailbox_id,
                    expires_at.isoformat(),
                )
                refresh_res = provider.refresh_token(refresh_token)
                new_refresh_token = refresh_res.refresh_token or refresh_token

                new_ct, new_key_id, new_nonce = encrypt_credentials(
                    {
                        "access_token": refresh_res.access_token,
                        "refresh_token": new_refresh_token,
                        "token_type": refresh_res.token_type,
                    },
                    workspace_id,
                    mailbox_id,
                    provider_name,
                )
                new_gen = credential_generation + 1
                new_expires_at = now + timedelta(seconds=refresh_res.expires_in)

                self.repository.rotate_mailbox_connection(
                    workspace_id=workspace_id,
                    mailbox_id=mailbox_id,
                    new_generation=new_gen,
                    credential_ciphertext=new_ct,
                    encryption_key_id=new_key_id,
                    nonce=new_nonce,
                    auth_mechanism=conn.get("auth_mechanism", "OAUTH"),
                    granted_scopes=refresh_res.granted_scopes,
                    expires_at=new_expires_at,
                )
                self.session.commit()
                credential["access_token"] = refresh_res.access_token
                credential_generation = new_gen

        return credential, credential_generation

    def reconcile_unresolved_inbound_messages(
        self,
        *,
        workspace_id: UUID,
        mailbox_id: UUID,
        limit: int = 50,
    ) -> int:
        """Periodic sweep to correlate previously UNRESOLVED inbound messages
        whose corresponding outbound messages arrived or completed after the reply.
        """
        unresolved_rows = self.repository.find_unresolved_inbound_messages(
            workspace_id=workspace_id,
            mailbox_id=mailbox_id,
            limit=limit,
        )
        if not unresolved_rows:
            return 0

        now = datetime.now(UTC)
        newly_matched_count = 0

        for row in unresolved_rows:
            inbound_id = row["id"]
            participants = row.get("participants") or {}
            from_part = participants.get("from") or {}
            sender_addr = from_part.get("address", "")
            in_reply_to = row.get("in_reply_to")
            refs_str = row.get("references_header") or ""
            references = refs_str.split() if refs_str else []

            rfc_ids = list(references)
            if in_reply_to:
                rfc_ids.insert(0, in_reply_to)

            candidates = self.repository.load_outbound_candidates(
                workspace_id=workspace_id,
                mailbox_id=mailbox_id,
                rfc_message_ids=rfc_ids,
                sender_address=sender_addr,
            )

            # Re-construct minimal normalized representation for matcher
            from app.modules.replies.schemas import NormalizedInboundMessage

            dummy_normalized = NormalizedInboundMessage(
                provider="UNKNOWN",
                provider_message_id=row["provider_message_id"],
                provider_thread_id=None,
                rfc_message_id=row["rfc_message_id"],
                in_reply_to=in_reply_to,
                references=references,
                from_address=sender_addr,
                from_name=from_part.get("name"),
                to_addresses=[],
                cc_addresses=[],
                bcc_addresses=[],
                subject=row.get("subject") or "",
                content_text=row.get("content_text"),
                content_html=None,
                received_at=row["received_at"],
                classification=row.get("classification") or InboundClassification.HUMAN_REPLY.value,
            )

            match_result = ReplyMatcher.match(dummy_normalized, candidates)
            if match_result.is_matched and match_result.matched_candidate:
                matched_cand = match_result.matched_candidate
                self.repository.mark_inbound_message_matched(
                    workspace_id=workspace_id,
                    inbound_message_id=inbound_id,
                )
                self.repository.insert_outreach_link(
                    workspace_id=workspace_id,
                    mailbox_id=mailbox_id,
                    inbound_message_id=inbound_id,
                    outbound_message_id=matched_cand.outbound_message_id,
                    campaign_id=matched_cand.campaign_id,
                    enrollment_id=matched_cand.enrollment_id,
                    evidence_type=matched_cand.evidence_type,
                    confidence=matched_cand.confidence,
                    status="CONFIRMED",
                    matched_at=now,
                )

                is_qualifying = dummy_normalized.classification not in (
                    InboundClassification.OUT_OF_OFFICE.value,
                    InboundClassification.AUTOMATED.value,
                )
                if is_qualifying:
                    self.repository.record_reply_outcome_and_stop_campaign(
                        workspace_id=workspace_id,
                        enrollment_id=matched_cand.enrollment_id,
                        campaign_id=matched_cand.campaign_id,
                        inbound_message_id=inbound_id,
                        provider_message_id=dummy_normalized.provider_message_id,
                        occurred_at=dummy_normalized.received_at,
                    )
                newly_matched_count += 1

        if newly_matched_count > 0:
            self.session.commit()
            logger.info(
                "Reconciled %d previously unresolved messages for mailbox %s",
                newly_matched_count,
                mailbox_id,
            )

        return newly_matched_count

    def recover_stale_leases(self, *, batch_size: int = 50) -> int:
        """Sweep and recover expired worker leases on mailbox_sync_states."""
        now = datetime.now(UTC)
        recovered = self.repository.recover_stale_sync_leases(now=now, batch_size=batch_size)
        if recovered > 0:
            self.session.commit()
            logger.info("Recovered %d stale sync leases", recovered)
        return recovered
