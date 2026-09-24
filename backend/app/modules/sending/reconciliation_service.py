from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.crypto import decrypt_credentials
from app.core.errors import AppError
from app.core.metrics import record_reconciliation
from app.modules.mailboxes.providers.base import ProviderCapability
from app.modules.mailboxes.providers.registry import ProviderRegistry
from app.modules.sending.repository import (
    SendingRepository,
    _safe_set_role,
    _safe_set_workspace,
)

logger = logging.getLogger(__name__)


class ReconciliationService:
    """Reconciles messages in UNKNOWN_OUTCOME status.

    Per MESSAGE_STATE_MACHINE.md and PROVIDER_ARCHITECTURE.md:
    1. Messages that landed in UNKNOWN_OUTCOME (due to provider timeouts, network
       disconnects during submission, or recovered worker crashes) must never be
       blindly retried.
    2. Where the provider supports message lookup by RFC Message-ID
       (e.g. Gmail via rfc822msgid query, Microsoft Graph via internetMessageId filter),
       we query the provider to determine if the message was accepted remotely.
    3. If verified sent: transition message to SENT and attempt to ACCEPTED with
       reconciliation evidence.
    4. If not found or unsupported by the protocol (e.g. standard SMTP): the message
       remains safely held in UNKNOWN_OUTCOME with auditable reconciliation metadata,
       preventing duplicate delivery.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self.repository = SendingRepository(session)

    def reconcile_unknown_messages(
        self,
        *,
        workspace_id: UUID | None = None,
        limit: int = 50,
        now: datetime | None = None,
    ) -> dict[str, int]:
        now = now or datetime.now(UTC)

        ws_clause = "AND m.workspace_id = :ws" if workspace_id else ""
        query_params: dict[str, Any] = {"limit": limit}
        if workspace_id:
            query_params["ws"] = str(workspace_id)

        # Discovery spans every workspace, so it runs as app_scheduler, whose
        # read-only discovery policies on messages, mailboxes and
        # message_attempts (migrations 0011/0013) allow that; app_worker_send is
        # scoped to one workspace and would see no rows. No FOR UPDATE here:
        # it is invalid across this LEFT JOIN, and every write below is done per
        # row as app_worker_send inside that row's workspace, guarded by
        # status and version, so concurrent runs cannot both apply a result.
        _safe_set_role(self.session, "app_scheduler")
        _safe_set_workspace(self.session, workspace_id)

        stmt = text(
            f"""
            SELECT m.id AS message_id, m.workspace_id, m.mailbox_id, m.rfc_message_id,
                   m.version, mb.provider, mb.current_connection_generation,
                   a.id AS attempt_id, a.reconciliation_metadata
            FROM public.messages m
            JOIN public.mailboxes mb
              ON mb.workspace_id = m.workspace_id AND mb.id = m.mailbox_id
            LEFT JOIN public.message_attempts a
              ON a.workspace_id = m.workspace_id
             AND a.message_id = m.id
             AND a.evidence_state = 'UNKNOWN'
            WHERE m.status = 'UNKNOWN_OUTCOME'
              {ws_clause}
            ORDER BY m.updated_at ASC
            LIMIT :limit
            """
        )

        rows = self.session.execute(stmt, query_params).mappings().all()
        if not rows:
            return {"reconciled_sent": 0, "pending": 0, "unsupported": 0}

        counts = {"reconciled_sent": 0, "pending": 0, "unsupported": 0}

        for row in rows:
            row_ws = UUID(str(row["workspace_id"]))
            row_mid = UUID(str(row["message_id"]))
            row_mbid = UUID(str(row["mailbox_id"]))
            attempt_id = UUID(str(row["attempt_id"])) if row["attempt_id"] else None
            rfc_message_id = row["rfc_message_id"]
            expected_version = int(row["version"])
            provider_name = str(row["provider"])
            cred_gen = int(row["current_connection_generation"])

            _safe_set_role(self.session, "app_worker_send")
            _safe_set_workspace(self.session, row_ws)

            provider = ProviderRegistry.get(provider_name)
            if (
                ProviderCapability.LOOKUP_MESSAGE not in provider.capabilities
                or not rfc_message_id
            ):
                # Protocol does not support RFC Message-ID lookup (e.g. SMTP)
                counts["unsupported"] += 1
                record_reconciliation(provider_name, "unsupported")
                if attempt_id:
                    existing_meta = row["reconciliation_metadata"] or {}
                    if isinstance(existing_meta, str):
                        try:
                            existing_meta = json.loads(existing_meta)
                        except Exception:
                            existing_meta = {}
                    updated_meta = dict(existing_meta)
                    updated_meta["last_checked_at"] = now.isoformat()
                    updated_meta["lookup_result"] = "unsupported_by_protocol"

                    self.session.execute(
                        text(
                            """
                            UPDATE public.message_attempts
                            SET reconciliation_metadata = :meta
                            WHERE workspace_id = :ws AND id = :attempt_id
                            """
                        ),
                        {
                            "ws": str(row_ws),
                            "attempt_id": str(attempt_id),
                            "meta": json.dumps(updated_meta),
                        },
                    )
                self.repository.record_audit_event(
                    workspace_id=row_ws,
                    action="message.reconciliation_unsupported",
                    target_id=row_mid,
                    after_state={"provider": provider_name},
                    reason="protocol_lacks_message_lookup",
                )
                self.session.commit()
                continue

            # Load credentials
            conn = self.repository.get_mailbox_connection(
                workspace_id=row_ws,
                mailbox_id=row_mbid,
                generation=cred_gen,
            )
            if not conn or not conn["credential_ciphertext"]:
                logger.warning(
                    "Cannot reconcile message %s: mailbox credentials unavailable",
                    row_mid,
                )
                counts["pending"] += 1
                record_reconciliation(provider_name, "pending")
                self.session.commit()
                continue

            try:
                creds = decrypt_credentials(
                    conn["credential_ciphertext"],
                    conn["nonce"],
                    row_ws,
                    row_mbid,
                    provider_name,
                    conn["encryption_key_id"],
                )
                credential: dict[str, Any] = dict(creds)
                if conn.get("protected_config"):
                    credential.update(conn["protected_config"])
            except AppError:
                logger.warning(
                    "Cannot reconcile message %s: failed to decrypt credentials",
                    row_mid,
                )
                counts["pending"] += 1
                record_reconciliation(provider_name, "pending")
                self.session.commit()
                continue

            # Execute lookup via provider adapter
            try:
                lookup_res = provider.lookup_message(credential, rfc_message_id)
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "Provider lookup_message error during reconciliation of %s: %s",
                    row_mid,
                    exc,
                )
                lookup_res = None

            if lookup_res is not None and lookup_res.status == "ACCEPTED":
                # Message was confirmed sent by provider!
                accepted_at = lookup_res.accepted_at or now
                provider_msg_id = lookup_res.provider_message_id
                provider_thread_id = lookup_res.provider_thread_id

                reconcile_meta = {
                    "reconciled_at": now.isoformat(),
                    "result": "accepted",
                    "provider_message_id": provider_msg_id,
                }

                if attempt_id:
                    self.session.execute(
                        text(
                            """
                            UPDATE public.message_attempts
                            SET evidence_state = 'ACCEPTED',
                                completed_at = :accepted_at,
                                provider_message_ref = :provider_msg_id,
                                provider_thread_ref = :provider_thread_id,
                                provider_request_id = :provider_request_id,
                                reconciliation_metadata = :meta
                            WHERE workspace_id = :ws AND id = :attempt_id
                            """
                        ),
                        {
                            "ws": str(row_ws),
                            "attempt_id": str(attempt_id),
                            "accepted_at": accepted_at,
                            "provider_msg_id": provider_msg_id,
                            "provider_thread_id": provider_thread_id,
                            "provider_request_id": rfc_message_id,
                            "meta": json.dumps(reconcile_meta),
                        },
                    )

                self.session.execute(
                    text(
                        """
                        UPDATE public.messages
                        SET status = 'SENT',
                            accepted_at = :accepted_at,
                            provider_message_id =
                                COALESCE(:provider_msg_id, provider_message_id),
                            version = version + 1
                        WHERE workspace_id = :ws
                          AND id = :mid
                          AND status = 'UNKNOWN_OUTCOME'
                          AND version = :expected_version
                        """
                    ),
                    {
                        "ws": str(row_ws),
                        "mid": str(row_mid),
                        "accepted_at": accepted_at,
                        "provider_msg_id": provider_msg_id,
                        "expected_version": expected_version,
                    },
                )

                self.repository.record_audit_event(
                    workspace_id=row_ws,
                    action="message.reconciled_accepted",
                    target_id=row_mid,
                    after_state={
                        "status": "SENT",
                        "provider_message_id": provider_msg_id,
                    },
                    reason=None,
                )
                counts["reconciled_sent"] += 1
                record_reconciliation(provider_name, "accepted")
                logger.info(
                    "Reconciled message %s to SENT via provider lookup",
                    row_mid,
                )
            else:
                # Not found remotely (yet) or lookup inconclusive: keep UNKNOWN_OUTCOME
                if attempt_id:
                    existing_meta = row["reconciliation_metadata"] or {}
                    if isinstance(existing_meta, str):
                        try:
                            existing_meta = json.loads(existing_meta)
                        except Exception:
                            existing_meta = {}
                    updated_meta = dict(existing_meta)
                    check_count = int(updated_meta.get("check_count", 0)) + 1
                    updated_meta["last_checked_at"] = now.isoformat()
                    updated_meta["lookup_result"] = "not_found"
                    updated_meta["check_count"] = check_count

                    self.session.execute(
                        text(
                            """
                            UPDATE public.message_attempts
                            SET reconciliation_metadata = :meta
                            WHERE workspace_id = :ws AND id = :attempt_id
                            """
                        ),
                        {
                            "ws": str(row_ws),
                            "attempt_id": str(attempt_id),
                            "meta": json.dumps(updated_meta),
                        },
                    )

                self.repository.record_audit_event(
                    workspace_id=row_ws,
                    action="message.reconciliation_checked",
                    target_id=row_mid,
                    after_state={"lookup_result": "not_found"},
                    reason="not_found_on_provider",
                )
                counts["pending"] += 1
                record_reconciliation(provider_name, "pending")

            self.session.commit()

        return counts
