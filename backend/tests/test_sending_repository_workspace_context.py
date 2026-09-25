from __future__ import annotations

import inspect
from datetime import UTC, datetime
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID, uuid4

import pytest

from app.modules.sending.repository import SendingRepository

# Every SendingRepository method that runs as app_worker_send inside a
# workspace. The send commits its authorization transaction before calling the
# provider, and app.workspace_id is transaction-local, so each of these must set
# the workspace itself: under RLS an unset workspace hides every row (the
# mailbox credentials read as "missing") and rejects every insert (audit_events).
# The SQLite-backed suite cannot see this because it skips both statements.
_WORKSPACE_SCOPED_METHODS = [
    "get_mailbox_connection",
    "insert_mailbox_connection",
    "update_mailbox_connection_state",
    "update_mailbox_blocked_until",
    "skip_message",
    "insert_prepared_attempt",
    "next_attempt_ordinal",
    "insert_capacity_debit",
    "transition_message_to_sending",
    "finalize_attempt_result",
    "record_audit_event",
]


def _dummy(annotation: str) -> Any:
    if "UUID" in annotation:
        return uuid4()
    if "bool" in annotation:
        return True
    if "int" in annotation:
        return 1
    if "bytes" in annotation:
        return b"x"
    if "datetime" in annotation:
        return datetime.now(UTC)
    if "dict" in annotation:
        return {}
    if "list" in annotation or "Sequence" in annotation:
        return []
    if "str" in annotation:
        return "x"
    return MagicMock()


class _RecordingSession:
    """Pretends to be a PostgreSQL session and records the SQL it is given."""

    def __init__(self) -> None:
        self.statements: list[str] = []

    def get_bind(self) -> Any:
        bind = MagicMock()
        bind.dialect.name = "postgresql"
        return bind

    def execute(self, statement: Any, params: Any = None) -> Any:
        self.statements.append(str(statement))
        return MagicMock()


@pytest.mark.parametrize("method_name", _WORKSPACE_SCOPED_METHODS)
def test_worker_method_sets_workspace_right_after_switching_role(
    method_name: str,
) -> None:
    session = _RecordingSession()
    repository = SendingRepository(session)  # type: ignore[arg-type]
    method = getattr(repository, method_name)

    kwargs = {
        name: _dummy(str(parameter.annotation))
        for name, parameter in inspect.signature(method).parameters.items()
        if parameter.default is inspect.Parameter.empty
    }
    workspace_id = uuid4()
    kwargs["workspace_id"] = workspace_id
    assert isinstance(workspace_id, UUID)

    try:
        method(**kwargs)
    except Exception:  # noqa: BLE001 - the mocked result shape is irrelevant here
        pass

    role_index = next(
        (
            index
            for index, sql in enumerate(session.statements)
            if "SET LOCAL ROLE app_worker_send" in sql
        ),
        None,
    )
    assert role_index is not None, f"{method_name} never switched to app_worker_send"
    assert len(session.statements) > role_index + 1, method_name
    assert "app.workspace_id" in session.statements[role_index + 1], (
        f"{method_name} ran as app_worker_send without setting app.workspace_id; "
        "after the authorization commit RLS would return no rows / reject inserts"
    )
