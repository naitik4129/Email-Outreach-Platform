from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import redis
from redis.exceptions import RedisError

from app.core.config import Settings
from app.modules.rate_limit import key_builder
from app.modules.rate_limit.schemas import (
    RateDenial,
    RateLimitDenied,
    RateLimitGenerationStale,
    RateLimitUnavailable,
    RateReservation,
    RateReservationRequest,
    RateScopeKind,
    ScopeReservationResult,
)

logger = logging.getLogger(__name__)

_LUA_DIR = Path(__file__).resolve().parent / "lua"

# RATE_LIMITING.md: "Redis token bucket for short-term pacing/concurrency and
# timestamped rolling-window debits for long-window quantities." No exact
# threshold is specified there; 60 seconds is this implementation's explicit
# convention (documented here as the single source of truth): windows at or
# below it use a continuously-refilling token bucket (smooth, no boundary
# burst -- appropriate for pacing/concurrency/min-spacing), windows above it
# use a timestamped rolling window via a sorted set (appropriate for
# hourly/daily caps, per the doc's own naming of that mechanism).
SHORT_WINDOW_THRESHOLD_SECONDS = 60

# How long a granted-but-not-yet-authorized reservation survives in Redis
# before self-expiring. Must comfortably exceed the worker's own
# authorization_deadline (message_attempts.authorization_deadline) so a
# healthy worker never races its own reservation's expiry.
DEFAULT_RESERVATION_TTL_SECONDS = 120


def _mode_for_window(window_seconds: int) -> str:
    return (
        "TOKEN_BUCKET"
        if window_seconds <= SHORT_WINDOW_THRESHOLD_SECONDS
        else "ROLLING_WINDOW"
    )


class RedisRateLimiter:
    """Atomic, fail-closed, multi-scope distributed rate limiter.

    Every method that talks to Redis converts connection/timeout/protocol
    errors into `RateLimitUnavailable` -- callers must never interpret a
    Redis failure as "capacity granted". See docs/architecture/RATE_LIMITING.md.

    Single primary/shard deployment only (see reserve.lua's module comment):
    this implementation does not declare every touched key via Redis
    Cluster's KEYS-hashing convention (spacing/reservation keys are derived
    inside the script from the bucket key), matching RATE_LIMITING.md's
    "Proposed initial limiter uses one primary/shard for the shared
    multi-scope keys. Do not deploy a clustered cross-slot script and assume
    atomicity."
    """

    def __init__(
        self,
        client: redis.Redis | None = None,
        settings: Settings | None = None,
    ) -> None:
        settings = settings or Settings.current()
        self._client = client or redis.Redis.from_url(
            settings.redis_url, decode_responses=True
        )
        self._reserve_script = self._client.register_script(
            (_LUA_DIR / "reserve.lua").read_text(encoding="utf-8")
        )
        self._release_script = self._client.register_script(
            (_LUA_DIR / "release.lua").read_text(encoding="utf-8")
        )

    def current_generation(self) -> int | None:
        try:
            raw = self._client.get(key_builder.generation_key())
        except RedisError as exc:
            raise RateLimitUnavailable("Redis unavailable reading generation") from exc
        # redis-py's stubs type every response method as a sync/async union
        # (ResponseT); this client is always used synchronously.
        return int(raw) if raw is not None else None  # type: ignore[arg-type]

    def is_ready(self) -> bool:
        try:
            status = self._client.get(key_builder.status_key())
        except RedisError as exc:
            raise RateLimitUnavailable("Redis unavailable reading status") from exc
        return status == "READY"

    def reserve(
        self,
        request: RateReservationRequest,
        *,
        expected_generation: int,
    ) -> RateReservation:
        """Atomically reserve capacity across every policy in
        `request.policies`. Always checks generation/READY status, even when
        `request.policies` is empty -- an unconfigured deployment (no
        rate_scopes/tenant_rate_policies rows anywhere) still must not send
        while the rate controller has never bootstrapped; "no numeric caps
        configured" is not the same claim as "the limiter is healthy".

        Raises `RateLimitGenerationStale` (not ready / generation mismatch),
        `RateLimitDenied` (healthy but capacity unavailable now), or
        `RateLimitUnavailable` (Redis itself failed).
        """
        now_ms = int(time.time() * 1000)
        reservation_id = str(uuid.uuid4())
        scopes_payload = [
            {
                "kind": policy.kind.value,
                "scope_key": policy.scope_key,
                "unit": policy.unit,
                "window_seconds": policy.window_seconds,
                "limit_value": policy.limit_value,
                "cooldown_seconds": policy.cooldown_seconds,
                "min_spacing_seconds": policy.min_spacing_seconds,
                "mode": _mode_for_window(policy.window_seconds),
                "quantity": request.quantity,
            }
            for policy in request.policies
        ]
        keys = [key_builder.generation_key(), key_builder.status_key()] + [
            key_builder.bucket_key(p.kind, p.scope_key, p.unit, p.window_seconds)
            for p in request.policies
        ]
        args = [
            str(now_ms),
            str(expected_generation),
            reservation_id,
            str(DEFAULT_RESERVATION_TTL_SECONDS),
            json.dumps(scopes_payload),
        ]

        try:
            raw_result = self._reserve_script(keys=keys, args=args)
        except RedisError as exc:
            raise RateLimitUnavailable("Redis unavailable during reservation") from exc

        result = json.loads(raw_result)
        if not result.get("ok"):
            reason = result.get("reason", "UNKNOWN")
            if reason in ("GENERATION_MISMATCH", "NOT_READY"):
                raise RateLimitGenerationStale(f"Rate limiter not usable: {reason}")
            failing_kind_raw = result.get("failing_kind")
            denial = RateDenial(
                reason=reason,
                failing_kind=(
                    RateScopeKind(failing_kind_raw) if failing_kind_raw else None
                ),
                failing_scope_key=result.get("failing_scope_key"),
                retry_after_seconds=(
                    result["retry_after_ms"] / 1000.0
                    if result.get("retry_after_ms")
                    else None
                ),
            )
            raise RateLimitDenied(denial)

        scopes = tuple(
            ScopeReservationResult(
                kind=policy.kind,
                source_id=policy.source_id,
                scope_key=policy.scope_key,
                unit=policy.unit,
            )
            for policy in request.policies
        )
        granted_at = datetime.fromtimestamp(result["granted_at_ms"] / 1000, tz=UTC)
        expires_at = datetime.fromtimestamp(
            (result["granted_at_ms"] + DEFAULT_RESERVATION_TTL_SECONDS * 1000) / 1000,
            tz=UTC,
        )
        return RateReservation(
            reservation_id=result["reservation_id"],
            generation=int(result["generation"]),
            granted_at=granted_at,
            expires_at=expires_at,
            scopes=scopes,
        )

    def release(self, reservation_id: str) -> None:
        """Best-effort compensating release for a reservation that never
        reached durable Postgres authorization. MUST NEVER be called after a
        provider send was actually attempted -- see release.lua. Failures
        here are logged and swallowed: a reservation that cannot be released
        simply self-expires (safe; see reserve.lua's TTL), so this is never
        allowed to block or fail the caller's own error handling.
        """
        try:
            self._release_script(
                keys=[key_builder.reservation_key(reservation_id)],
                args=[reservation_id],
            )
        except RedisError as exc:
            logger.warning(
                "Rate limit release failed (best-effort, ignored)",
                extra={"reservation_id": reservation_id, "error": str(exc)},
            )
