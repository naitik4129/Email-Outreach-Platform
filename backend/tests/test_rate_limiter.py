from __future__ import annotations

import fakeredis
import pytest
from redis.exceptions import ConnectionError as RedisConnectionError

from app.modules.rate_limit.limiter import RedisRateLimiter
from app.modules.rate_limit.schemas import (
    RateLimitDenied,
    RateLimitGenerationStale,
    RateLimitUnavailable,
    RatePolicySpec,
    RateReservationRequest,
    RateScopeKind,
)


@pytest.fixture
def client() -> fakeredis.FakeStrictRedis:
    c = fakeredis.FakeStrictRedis(decode_responses=True)
    c.set("rl:generation", "1")
    c.set("rl:status", "READY")
    return c


@pytest.fixture
def limiter(client: fakeredis.FakeStrictRedis) -> RedisRateLimiter:
    return RedisRateLimiter(client=client)


def _mailbox_policy(
    scope_key: str = "mb1",
    *,
    limit_value: int = 1,
    window_seconds: int = 10,
    **kwargs: object,
) -> RatePolicySpec:
    return RatePolicySpec(
        kind=RateScopeKind.MAILBOX,
        source_id="policy-1",
        scope_key=scope_key,
        unit="MESSAGE",
        window_seconds=window_seconds,
        limit_value=limit_value,
        **kwargs,  # type: ignore[arg-type]
    )


def test_one_token_two_reservations_only_one_succeeds(
    limiter: RedisRateLimiter,
) -> None:
    policy = _mailbox_policy(limit_value=1)
    req1 = RateReservationRequest(message_id="m1", quantity=1, policies=(policy,))
    req2 = RateReservationRequest(message_id="m2", quantity=1, policies=(policy,))

    granted = limiter.reserve(req1, expected_generation=1)
    assert granted.reservation_id

    with pytest.raises(RateLimitDenied) as exc_info:
        limiter.reserve(req2, expected_generation=1)
    assert exc_info.value.denial.reason == "CAPACITY_DENIED"
    assert exc_info.value.denial.failing_kind == RateScopeKind.MAILBOX


def test_rolling_window_mode_for_long_windows(limiter: RedisRateLimiter) -> None:
    policy = _mailbox_policy(scope_key="camp1", limit_value=2, window_seconds=3600)
    for mid in ("a", "b"):
        limiter.reserve(
            RateReservationRequest(message_id=mid, quantity=1, policies=(policy,)),
            expected_generation=1,
        )
    with pytest.raises(RateLimitDenied):
        limiter.reserve(
            RateReservationRequest(message_id="c", quantity=1, policies=(policy,)),
            expected_generation=1,
        )


def test_key_isolation_across_scope_keys(limiter: RedisRateLimiter) -> None:
    """Two different mailboxes must never share capacity."""
    policy_a = _mailbox_policy(scope_key="mailbox-a", limit_value=1)
    policy_b = _mailbox_policy(scope_key="mailbox-b", limit_value=1)

    limiter.reserve(
        RateReservationRequest(message_id="m1", quantity=1, policies=(policy_a,)),
        expected_generation=1,
    )
    # mailbox-b must still have its own full capacity, unaffected by mailbox-a.
    granted = limiter.reserve(
        RateReservationRequest(message_id="m2", quantity=1, policies=(policy_b,)),
        expected_generation=1,
    )
    assert granted.reservation_id


def test_key_isolation_across_kinds(limiter: RedisRateLimiter) -> None:
    """Same scope_key string under different kinds must not collide."""
    mailbox_policy = RatePolicySpec(
        kind=RateScopeKind.MAILBOX,
        source_id="p1",
        scope_key="SAME",
        unit="MESSAGE",
        window_seconds=10,
        limit_value=1,
    )
    campaign_policy = RatePolicySpec(
        kind=RateScopeKind.CAMPAIGN,
        source_id="p2",
        scope_key="SAME",
        unit="MESSAGE",
        window_seconds=10,
        limit_value=1,
    )
    limiter.reserve(
        RateReservationRequest(message_id="m1", quantity=1, policies=(mailbox_policy,)),
        expected_generation=1,
    )
    granted = limiter.reserve(
        RateReservationRequest(
            message_id="m2", quantity=1, policies=(campaign_policy,)
        ),
        expected_generation=1,
    )
    assert granted.reservation_id


def test_release_restores_capacity(limiter: RedisRateLimiter) -> None:
    policy = _mailbox_policy(limit_value=1)
    reservation = limiter.reserve(
        RateReservationRequest(message_id="m1", quantity=1, policies=(policy,)),
        expected_generation=1,
    )
    limiter.release(reservation.reservation_id)
    granted = limiter.reserve(
        RateReservationRequest(message_id="m2", quantity=1, policies=(policy,)),
        expected_generation=1,
    )
    assert granted.reservation_id


def test_min_spacing_enforced(limiter: RedisRateLimiter) -> None:
    policy = _mailbox_policy(limit_value=100, min_spacing_seconds=5)
    limiter.reserve(
        RateReservationRequest(message_id="m1", quantity=1, policies=(policy,)),
        expected_generation=1,
    )
    with pytest.raises(RateLimitDenied) as exc_info:
        limiter.reserve(
            RateReservationRequest(message_id="m2", quantity=1, policies=(policy,)),
            expected_generation=1,
        )
    assert exc_info.value.denial.retry_after_seconds == pytest.approx(5.0, abs=0.1)


def test_generation_mismatch_fails_closed(limiter: RedisRateLimiter) -> None:
    policy = _mailbox_policy()
    req = RateReservationRequest(message_id="m1", quantity=1, policies=(policy,))
    with pytest.raises(RateLimitGenerationStale):
        limiter.reserve(req, expected_generation=999)


def test_not_ready_fails_closed(
    client: fakeredis.FakeStrictRedis, limiter: RedisRateLimiter
) -> None:
    client.set("rl:status", "RECOVERING")
    policy = _mailbox_policy()
    req = RateReservationRequest(message_id="m1", quantity=1, policies=(policy,))
    with pytest.raises(RateLimitGenerationStale):
        limiter.reserve(req, expected_generation=1)


def test_missing_generation_key_fails_closed(client: fakeredis.FakeStrictRedis) -> None:
    """A missing key must never be treated as an unused full budget."""
    client.delete("rl:generation")
    limiter = RedisRateLimiter(client=client)
    policy = _mailbox_policy()
    req = RateReservationRequest(message_id="m1", quantity=1, policies=(policy,))
    with pytest.raises(RateLimitGenerationStale):
        limiter.reserve(req, expected_generation=1)


def test_empty_policies_still_checks_generation_and_status(
    client: fakeredis.FakeStrictRedis, limiter: RedisRateLimiter
) -> None:
    """No applicable policy anywhere is not the same claim as 'the limiter
    is healthy' -- an unconfigured deployment must still fail closed while
    RECOVERING."""
    granted = limiter.reserve(
        RateReservationRequest(message_id="m1", quantity=1, policies=()),
        expected_generation=1,
    )
    assert granted.scopes == ()

    client.set("rl:status", "RECOVERING")
    with pytest.raises(RateLimitGenerationStale):
        limiter.reserve(
            RateReservationRequest(message_id="m2", quantity=1, policies=()),
            expected_generation=1,
        )


class _BrokenRedis:
    """Minimal stand-in that raises on every call, simulating a Redis
    connection failure."""

    def register_script(self, source: str) -> object:
        def _raise(*args: object, **kwargs: object) -> None:
            raise RedisConnectionError("simulated connection failure")

        return _raise

    def get(self, *args: object, **kwargs: object) -> None:
        raise RedisConnectionError("simulated connection failure")


def test_redis_unavailable_raises_unavailable_not_denied() -> None:
    limiter = RedisRateLimiter(client=_BrokenRedis())  # type: ignore[arg-type]
    policy = _mailbox_policy()
    req = RateReservationRequest(message_id="m1", quantity=1, policies=(policy,))
    with pytest.raises(RateLimitUnavailable):
        limiter.reserve(req, expected_generation=1)
    with pytest.raises(RateLimitUnavailable):
        limiter.current_generation()
    with pytest.raises(RateLimitUnavailable):
        limiter.is_ready()


def test_release_of_unknown_reservation_is_a_safe_noop(
    limiter: RedisRateLimiter,
) -> None:
    # Must not raise even though this reservation id was never granted.
    limiter.release("does-not-exist")
