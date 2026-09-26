"""Global model-call rate limit (ADR-0012).

Per-workspace daily caps live in PostgreSQL (`personalization_usage_daily`,
reserved atomically by the repository). This is only the global requests-per-
minute ceiling. It is separate from send rate limiting and never consumes send
capacity. It fails closed: if Redis is unavailable no model call is made.
"""

from __future__ import annotations

import time
from typing import Protocol, cast

import redis
from redis.exceptions import RedisError


class RpmLimiter(Protocol):
    def allow(self) -> bool: ...


class AllowAllLimiter:
    """Test double / disabled limiter."""

    def allow(self) -> bool:
        return True


class RedisRpmLimiter:
    """Fixed one-minute window counter. Coarse on purpose: it bounds the burst to
    at most 2x the limit across a window edge, which is fine for a cost guard."""

    def __init__(
        self,
        *,
        redis_url: str,
        limit_per_minute: int,
        client: redis.Redis | None = None,
    ) -> None:
        self._limit = limit_per_minute
        self._client = client or redis.Redis.from_url(redis_url, decode_responses=True)

    def allow(self) -> bool:
        key = f"personalization:rpm:{int(time.time() // 60)}"
        try:
            # redis-py types every command as a sync/async union.
            count = cast(int, self._client.incr(key))
            if count == 1:
                self._client.expire(key, 120)
            return count <= self._limit
        except RedisError:
            return False
