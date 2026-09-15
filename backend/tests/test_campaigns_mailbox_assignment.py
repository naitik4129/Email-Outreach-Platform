from __future__ import annotations

import uuid

import pytest

from app.core.errors import AppError
from app.modules.campaigns.mailbox_assignment import assign_mailbox_for_recipient


def test_round_robin_wraps_across_ordinals() -> None:
    mailboxes = [uuid.uuid4() for _ in range(3)]
    assigned = [
        assign_mailbox_for_recipient(mailboxes, ordinal) for ordinal in range(1, 10)
    ]
    assert assigned == [
        mailboxes[0],
        mailboxes[1],
        mailboxes[2],
        mailboxes[0],
        mailboxes[1],
        mailboxes[2],
        mailboxes[0],
        mailboxes[1],
        mailboxes[2],
    ]


def test_single_mailbox_always_selected() -> None:
    mailbox = uuid.uuid4()
    for ordinal in (1, 2, 5, 100):
        assert assign_mailbox_for_recipient([mailbox], ordinal) == mailbox


def test_assignment_is_deterministic_for_same_ordinal() -> None:
    mailboxes = [uuid.uuid4() for _ in range(4)]
    first = assign_mailbox_for_recipient(mailboxes, 7)
    second = assign_mailbox_for_recipient(mailboxes, 7)
    assert first == second


def test_empty_mailbox_list_raises() -> None:
    with pytest.raises(AppError) as exc_info:
        assign_mailbox_for_recipient([], 1)
    assert exc_info.value.status_code == 500
