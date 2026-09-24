from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any, cast
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from app.core.metrics import (
    record_stale_execution_recovered,
    record_worker_crash_recovery,
)
from app.modules.sending.repository import (
    SendingRepository,
    _safe_set_role,
    _safe_set_workspace,
)

logger = logging.getLogger(__name__)


class RecoveryService:
    """Detects and safely recovers stale message executions where a worker
    crashed, terminated, or hung while holding SENDING ownership.

    Per MESSAGE_STATE_MACHINE.md and PROVIDER_ARCHITECTURE.md:
    1. A message in SENDING whose active PREPARED attempt has exceeded its
       authorization_deadline is considered abandoned.
    2. We CANNOT assume the message was not sent (the worker may have died
       immediately after dispatching the HTTP/SMTP request).
    3. Therefore, the attempt transitions to evidence_state='UNKNOWN' and the
       message to status='UNKNOWN_OUTCOME'.
    4. NEVER blindly retry or set to FAILED here. The reconciliation service
       will inspect the provider using RFC Message ID lookup where supported.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self.repository = SendingRepository(session)

    def recover_stale_executions(
        self,
        *,
        workspace_id: UUID | None = None,
        limit: int = 50,
        now: datetime | None = None,
    ) -> int:
        now = now or datetime.now(UTC)

        ws_clause = "AND a.workspace_id = :ws" if workspace_id else ""
        query_params: dict[str, Any] = {"now": now, "limit": limit}
        if workspace_id:
            query_params["ws"] = str(workspace_id)

        # 1. Discover abandoned PREPARED attempts with expired lease deadlines.
        # This sweep spans every workspace, so it runs as app_scheduler, whose
        # read-only discovery policies (migrations 0011/0013) allow that;
        # app_worker_send is scoped to one workspace and would see no rows.
        # Every write below happens per row as app_worker_send inside that
        # row's workspace, guarded by evidence_state/status/version, so no
        # row lock is needed at discovery time (and FOR UPDATE would also
        # require an UPDATE policy that app_scheduler only has per workspace).
        _safe_set_role(self.session, "app_scheduler")
        _safe_set_workspace(self.session, workspace_id)

        candidates_stmt = text(
            f"""
            SELECT a.id AS attempt_id, a.workspace_id, a.message_id, a.mailbox_id,
                   m.version, a.authorization_deadline
            FROM public.message_attempts a
            JOIN public.messages m
              ON m.workspace_id = a.workspace_id AND m.id = a.message_id
            WHERE m.status = 'SENDING'
              AND a.evidence_state = 'PREPARED'
              AND a.authorization_deadline <= :now
              {ws_clause}
            ORDER BY a.authorization_deadline ASC
            LIMIT :limit
            """
        )

        rows = self.session.execute(candidates_stmt, query_params).mappings().all()
        if not rows:
            return 0

        recovered_count = 0
        for row in rows:
            row_ws = UUID(str(row["workspace_id"]))
            attempt_id = UUID(str(row["attempt_id"]))
            message_id = UUID(str(row["message_id"]))
            expected_version = int(row["version"])

            _safe_set_role(self.session, "app_worker_send")
            _safe_set_workspace(self.session, row_ws)

            # Transition attempt to UNKNOWN evidence state
            res_attempt = cast(
                CursorResult[Any],
                self.session.execute(
                    text(
                        """
                        UPDATE public.message_attempts
                        SET evidence_state = 'UNKNOWN',
                            completed_at = NULL,
                            error_category = 'NETWORK_ERROR',
                            error_code = 'execution_lease_expired'
                        WHERE workspace_id = :ws
                          AND id = :attempt_id
                          AND evidence_state = 'PREPARED'
                        """
                    ),
                    {"ws": str(row_ws), "attempt_id": str(attempt_id)},
                ),
            )
            if res_attempt.rowcount == 0:
                continue

            # Transition message to UNKNOWN_OUTCOME and bump version
            res_msg = cast(
                CursorResult[Any],
                self.session.execute(
                    text(
                        """
                        UPDATE public.messages
                        SET status = 'UNKNOWN_OUTCOME',
                            terminal_reason = NULL,
                            version = version + 1
                        WHERE workspace_id = :ws
                          AND id = :mid
                          AND status = 'SENDING'
                          AND version = :expected_version
                        """
                    ),
                    {
                        "ws": str(row_ws),
                        "mid": str(message_id),
                        "expected_version": expected_version,
                    },
                ),
            )
            if res_msg.rowcount == 0:
                continue

            self.repository.record_audit_event(
                workspace_id=row_ws,
                action="message.stale_execution_recovered",
                target_id=message_id,
                after_state={
                    "status": "UNKNOWN_OUTCOME",
                    "attempt_id": str(attempt_id),
                    "authorization_deadline": str(row["authorization_deadline"]),
                },
                reason="execution_lease_expired",
            )
            recovered_count += 1

        self.session.commit()

        if recovered_count > 0:
            record_stale_execution_recovered(recovered_count)
            record_worker_crash_recovery(recovered_count)
            logger.warning(
                "Recovered %d stale in-flight execution(s) to UNKNOWN_OUTCOME",
                recovered_count,
            )

        return recovered_count
