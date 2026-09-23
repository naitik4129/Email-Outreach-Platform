from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

import redis
from redis.exceptions import RedisError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.modules.rate_limit import key_builder
from app.modules.rate_limit.limiter import SHORT_WINDOW_THRESHOLD_SECONDS
from app.modules.rate_limit.repository import RateControlRepository
from app.modules.rate_limit.schemas import RateScopeKind

logger = logging.getLogger(__name__)

# How far back to look when rebuilding rolling-window state from durable
# capacity_debit_scopes rows during recovery. Must cover the longest
# configured window_seconds across all rate_scopes/tenant_rate_policies;
# 25 hours comfortably covers the documented CALENDAR_DAY (86400s) case plus
# DST/clock-skew margin. If a longer window is ever configured, this must be
# widened accordingly -- flagged here rather than silently truncating
# recovered state.
RECONSTRUCTION_LOOKBACK = timedelta(hours=25)

# A canary key whose value changes only across a genuine Redis
# process/topology change (restart, failover, flush), used by
# reconcile_loop to detect state loss between polls. Redis's own INFO
# run_id changes on every process restart; this implementation uses it
# rather than inventing a separate heartbeat mechanism. Flagged in the plan
# as a proposed-not-documented detection signal, pending confirmation.
_CANARY_RUN_ID_FIELD = "run_id"


class RateController:
    """Owns the `rate_control` READY/RECOVERING lifecycle and Redis bucket
    reconstruction. Runs as its own runtime group (see WORKERS.md role
    separation), using only the `app_rate_controller` DB role -- never
    `app_worker_send`. The send worker never writes `rate_control` itself;
    it only reads status/generation (RatePolicyRepository / RedisRateLimiter).
    """

    def __init__(
        self,
        session: Session,
        redis_client: redis.Redis | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.session = session
        settings = settings or Settings.current()
        self._redis = redis_client or redis.Redis.from_url(
            settings.redis_url, decode_responses=True
        )
        self.repository = RateControlRepository(session)

    def bootstrap(self) -> None:
        """Cold-start / recovery entry point. Marks the durable singleton
        RECOVERING, discards any old Redis reservation/bucket state (it may
        be stale or entirely lost), rebuilds rolling-window sorted sets from
        durable `capacity_debit_scopes` evidence, then flips to READY only
        once every required scope is loaded and the watermark is stable --
        per RATE_LIMITING.md: "Before READY, verify all required scopes
        loaded and recovery watermark stable; then atomically expose new
        generation to workers."
        """
        current = self.repository.get_status()
        next_generation = (current["generation"] + 1) if current else 1
        self.repository.enter_recovering(next_generation=next_generation)
        self.session.commit()

        logger.info(
            "Rate controller entering RECOVERING", extra={"generation": next_generation}
        )

        try:
            self._flush_redis_namespace()
            self._set_generation_and_status(next_generation, "RECOVERING")
            self._rebuild_rolling_windows()
        except RedisError as exc:
            logger.error("Rate controller bootstrap failed against Redis: %s", exc)
            raise

        watermark = datetime.now(UTC)
        became_ready = self.repository.mark_ready(
            generation=next_generation, recovery_watermark=watermark
        )
        self.session.commit()
        if not became_ready:
            # Another controller instance already advanced past this
            # generation; this instance's rebuild is stale. Do not flip
            # Redis to READY under a generation nothing durable agrees with.
            logger.warning(
                "rate_control generation advanced during bootstrap; not flipping READY",
                extra={"generation": next_generation},
            )
            return

        self._redis.set(key_builder.status_key(), "READY")
        logger.info("Rate controller READY", extra={"generation": next_generation})

    def reconcile_loop_tick(self, last_known_run_id: str | None) -> str | None:
        """One iteration of failure detection. Call periodically (e.g. every
        few seconds) from a long-lived process/beat schedule, threading the
        returned run_id back in as `last_known_run_id` on the next call --
        this method does not keep state itself (a fresh `RateController` is
        constructed per tick, one per short-lived session, so state cannot
        live on `self`). Re-enters RECOVERING automatically if Redis appears
        to have restarted/failed over/flushed since the last tick. Returns
        the run_id observed this tick (or `last_known_run_id` unchanged if
        Redis could not be reached).
        """
        try:
            run_id = self._read_run_id()
        except RedisError:
            logger.warning("Rate controller cannot reach Redis during reconcile tick")
            return last_known_run_id

        if last_known_run_id is not None and run_id != last_known_run_id:
            logger.warning(
                "Redis run_id changed since last tick -- Redis state may be lost; "
                "re-entering RECOVERING",
                extra={"previous_run_id": last_known_run_id, "run_id": run_id},
            )
            self.bootstrap()
            return run_id

        return run_id

    def _read_run_id(self) -> str:
        # redis-py's stubs type every response method as a sync/async union
        # (ResponseT); this client is always used synchronously.
        info = self._redis.info(section="server")
        return str(info.get(_CANARY_RUN_ID_FIELD, ""))  # type: ignore[union-attr]

    def _flush_redis_namespace(self) -> None:
        """Remove every key this limiter owns. Scans rather than FLUSHDB so
        a shared Redis instance used for other purposes is untouched."""
        cursor = 0
        pattern = "rl:*"
        while True:
            cursor, keys = self._redis.scan(  # type: ignore[misc]
                cursor=cursor, match=pattern, count=500
            )
            if keys:
                self._redis.delete(*keys)
            if cursor == 0:
                break

    def _set_generation_and_status(self, generation: int, status: str) -> None:
        self._redis.set(key_builder.generation_key(), str(generation))
        self._redis.set(key_builder.status_key(), status)

    def _rebuild_rolling_windows(self) -> None:
        """Re-seed rolling-window sorted sets from durable evidence so
        capacity already consumed before this restart is not forgotten
        (never under-count -- fail closed toward less remaining capacity,
        not more)."""
        since = datetime.now(UTC) - RECONSTRUCTION_LOOKBACK
        debit_scopes = self.repository.list_recent_capacity_debit_scopes(since=since)

        rate_scopes_by_id = {
            row["id"]: row for row in self.repository.list_rate_scopes()
        }
        tenant_policies_by_id = {
            row["id"]: row for row in self.repository.list_open_tenant_policies()
        }

        for row in debit_scopes:
            if row["rate_scope_id"] is not None:
                source = rate_scopes_by_id.get(row["rate_scope_id"])
                if source is None:
                    continue
                kind = RateScopeKind(source["kind"])
                scope_key = source["external_scope_key"]
                window_seconds = source["window_seconds"]
            elif row["tenant_rate_policy_id"] is not None:
                source = tenant_policies_by_id.get(row["tenant_rate_policy_id"])
                if source is None:
                    continue
                kind = RateScopeKind(source["kind"])
                scope_key = str(
                    source["workspace_id"]
                    if kind == RateScopeKind.WORKSPACE
                    else source["campaign_id"]
                    if kind == RateScopeKind.CAMPAIGN
                    else source["mailbox_id"]
                )
                window_seconds = source["window_seconds"]
            else:
                continue

            # Only long windows are represented as a rolling-window sorted
            # set; short windows are continuously-refilling token buckets
            # and are deliberately NOT reconstructed from historical debits
            # (RATE_LIMITING.md: "Start short token buckets empty and refill
            # at configured rate" -- reconstructing exact token counts from
            # discrete historical events is not meaningful for a continuous
            # refill model).
            if window_seconds <= SHORT_WINDOW_THRESHOLD_SECONDS:
                continue

            bkey = key_builder.bucket_key(kind, scope_key, row["unit"], window_seconds)
            score = row["authorized_at"].timestamp() * 1000
            self._redis.zadd(bkey, {row["reservation_id"]: score})
            self._redis.expire(bkey, window_seconds * 2)
