from __future__ import annotations

from redis import Redis

from app.core.config import Settings


def redis_client(settings: Settings) -> Redis:
    return Redis.from_url(
        settings.redis_url,
        socket_connect_timeout=settings.readiness_timeout_seconds,
        socket_timeout=settings.readiness_timeout_seconds,
        decode_responses=True,
    )


def check_redis(settings: Settings) -> dict[str, object]:
    client = redis_client(settings)
    try:
        client.ping()
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": exc.__class__.__name__}
    finally:
        client.close()

