# ruff: noqa: E501
"""Database failures behind the erasure commands become messages a user can act on."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy.exc import DBAPIError

from app.modules.erasure.service import _translate


def _error(*, primary: str = "", sqlstate: str | None = None, detail: str | None = None):
    orig = SimpleNamespace(
        sqlstate=sqlstate,
        diag=SimpleNamespace(message_primary=primary, message_detail=detail, sqlstate=sqlstate),
    )
    return DBAPIError("SELECT 1", {}, orig)  # type: ignore[arg-type]


def test_a_command_refusal_keeps_its_own_message() -> None:
    err = _translate(
        _error(primary="erasure:recent_sends", detail="This campaign sent email in the last 25 hours.")
    )
    assert err is not None
    assert (err.code, err.status_code) == ("recent_sends", 409)
    assert "25 hours" in err.message


def test_a_missing_database_function_says_the_update_is_pending() -> None:
    err = _translate(_error(primary="function public.app_delete_campaign(uuid, uuid) does not exist", sqlstate="42883"))
    assert err is not None
    assert (err.code, err.status_code) == ("not_available", 503)
    assert "database update" in err.message
    assert "app_delete_campaign" not in err.message  # nothing internal leaks


@pytest.mark.parametrize("sqlstate", ["57014", "55P03", "40P01", "40001"])
def test_timeouts_and_conflicts_ask_the_user_to_retry(sqlstate: str) -> None:
    err = _translate(_error(primary="canceling statement", sqlstate=sqlstate))
    assert err is not None
    assert (err.code, err.status_code) == ("busy", 409)
    assert "try again" in err.message.lower()


def test_a_permission_failure_is_explained_without_details() -> None:
    err = _translate(_error(primary='permission denied for table "messages"', sqlstate="42501"))
    assert err is not None and err.code == "database_permission"
    assert "messages" not in err.message


def test_anything_else_is_left_alone() -> None:
    assert _translate(_error(primary="something odd", sqlstate="XX000")) is None
