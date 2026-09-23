from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock

import fakeredis
import pytest

from app.modules.rate_limit import key_builder
from app.modules.rate_limit.controller import RateController
from app.modules.rate_limit.schemas import RateScopeKind


@pytest.fixture
def redis_client() -> fakeredis.FakeStrictRedis:
    return fakeredis.FakeStrictRedis(decode_responses=True)


def _controller(
    redis_client: fakeredis.FakeStrictRedis,
) -> tuple[RateController, MagicMock]:
    session = MagicMock()
    controller = RateController(session, redis_client=redis_client)
    controller.repository = MagicMock()
    controller.repository.get_status.return_value = None
    controller.repository.list_recent_capacity_debit_scopes.return_value = []
    controller.repository.list_rate_scopes.return_value = []
    controller.repository.list_open_tenant_policies.return_value = []
    controller.repository.mark_ready.return_value = True
    return controller, controller.repository


def test_bootstrap_flips_generation_and_status_ready(
    redis_client: fakeredis.FakeStrictRedis,
) -> None:
    controller, repository = _controller(redis_client)

    controller.bootstrap()

    assert redis_client.get(key_builder.generation_key()) == "1"
    assert redis_client.get(key_builder.status_key()) == "READY"
    repository.enter_recovering.assert_called_once_with(next_generation=1)
    repository.mark_ready.assert_called_once()


def test_bootstrap_increments_generation_on_repeat(
    redis_client: fakeredis.FakeStrictRedis,
) -> None:
    controller, repository = _controller(redis_client)
    repository.get_status.return_value = {"generation": 5, "status": "READY"}

    controller.bootstrap()

    repository.enter_recovering.assert_called_once_with(next_generation=6)
    assert redis_client.get(key_builder.generation_key()) == "6"


def test_bootstrap_flushes_stale_redis_state(
    redis_client: fakeredis.FakeStrictRedis,
) -> None:
    redis_client.set("rl:b:MAILBOX:old:MESSAGE:10", "stale")
    controller, _ = _controller(redis_client)

    controller.bootstrap()

    assert redis_client.get("rl:b:MAILBOX:old:MESSAGE:10") is None


def test_bootstrap_does_not_flip_ready_if_mark_ready_loses_race(
    redis_client: fakeredis.FakeStrictRedis,
) -> None:
    """Another controller instance advanced the generation first -- this
    instance's rebuild is stale and must not expose READY under a
    generation nothing durable agrees with."""
    controller, repository = _controller(redis_client)
    repository.mark_ready.return_value = False

    controller.bootstrap()

    assert redis_client.get(key_builder.status_key()) == "RECOVERING"


def test_bootstrap_rebuilds_rolling_window_from_durable_debits(
    redis_client: fakeredis.FakeStrictRedis,
) -> None:
    controller, repository = _controller(redis_client)
    now = datetime.now(UTC)
    repository.list_rate_scopes.return_value = [
        {
            "id": "scope-1",
            "kind": "PROVIDER",
            "external_scope_key": "GMAIL",
            "unit": "MESSAGE",
            "window_seconds": 3600,
            "limit_value": 100,
        }
    ]
    repository.list_recent_capacity_debit_scopes.return_value = [
        {
            "rate_scope_id": "scope-1",
            "tenant_rate_policy_id": None,
            "unit": "MESSAGE",
            "quantity": 1,
            "authorized_at": now,
            "window_kind": "ROLLING",
            "reservation_id": "res-1",
        }
    ]

    controller.bootstrap()

    bkey = key_builder.bucket_key(RateScopeKind.PROVIDER, "GMAIL", "MESSAGE", 3600)
    assert redis_client.zscore(bkey, "res-1") is not None


def test_bootstrap_does_not_reconstruct_short_window_token_buckets(
    redis_client: fakeredis.FakeStrictRedis,
) -> None:
    """Short (token-bucket) windows are deliberately NOT rebuilt from
    historical debits -- they start empty and refill continuously."""
    controller, repository = _controller(redis_client)
    now = datetime.now(UTC)
    repository.list_rate_scopes.return_value = [
        {
            "id": "scope-1",
            "kind": "MAILBOX",
            "external_scope_key": "mb-1",
            "unit": "MESSAGE",
            "window_seconds": 10,
            "limit_value": 5,
        }
    ]
    repository.list_recent_capacity_debit_scopes.return_value = [
        {
            "rate_scope_id": "scope-1",
            "tenant_rate_policy_id": None,
            "unit": "MESSAGE",
            "quantity": 1,
            "authorized_at": now,
            "window_kind": "ROLLING",
            "reservation_id": "res-1",
        }
    ]

    controller.bootstrap()

    bkey = key_builder.bucket_key(RateScopeKind.MAILBOX, "mb-1", "MESSAGE", 10)
    assert redis_client.exists(bkey) == 0


def test_reconcile_tick_detects_run_id_change_and_rebootstraps(
    redis_client: fakeredis.FakeStrictRedis,
) -> None:
    # fakeredis does not implement INFO (real Redis does); mock
    # _read_run_id directly so this test exercises the detection logic
    # itself rather than fakeredis's INFO support.
    controller, repository = _controller(redis_client)
    redis_client.set(key_builder.generation_key(), "1")
    redis_client.set(key_builder.status_key(), "READY")
    controller._read_run_id = MagicMock(return_value="new-run-id")  # type: ignore[method-assign]

    new_run_id = controller.reconcile_loop_tick(last_known_run_id="different-run-id")

    # bootstrap() should have run again (repository.enter_recovering called).
    repository.enter_recovering.assert_called_once()
    assert new_run_id == "new-run-id"


def test_reconcile_tick_same_run_id_does_not_rebootstrap(
    redis_client: fakeredis.FakeStrictRedis,
) -> None:
    controller, repository = _controller(redis_client)
    controller._read_run_id = MagicMock(return_value="stable-run-id")  # type: ignore[method-assign]

    controller.reconcile_loop_tick(last_known_run_id="stable-run-id")

    repository.enter_recovering.assert_not_called()


def test_reconcile_tick_redis_unreachable_keeps_last_known_run_id(
    redis_client: fakeredis.FakeStrictRedis,
) -> None:
    """A real INFO failure (or any RedisError) must not crash the tick or
    be misread as a run_id change -- it degrades to 'no new information'."""
    controller, repository = _controller(redis_client)

    result = controller.reconcile_loop_tick(last_known_run_id="whatever")

    repository.enter_recovering.assert_not_called()
    assert result == "whatever"


def test_reconcile_tick_first_call_never_bootstraps(
    redis_client: fakeredis.FakeStrictRedis,
) -> None:
    """last_known_run_id=None means 'no baseline yet' -- must not treat that
    as a change."""
    controller, repository = _controller(redis_client)

    controller.reconcile_loop_tick(last_known_run_id=None)

    repository.enter_recovering.assert_not_called()
