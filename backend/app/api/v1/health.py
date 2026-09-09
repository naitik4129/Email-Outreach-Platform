from __future__ import annotations

from fastapi import APIRouter, Response, status

from app.core.config import Settings
from app.db.health import check_database
from app.services.redis_health import check_redis

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/ready")
def ready(response: Response) -> dict[str, object]:
    settings = Settings.current()
    checks = {
        "database": check_database(settings),
        "redis": check_redis(settings),
    }
    ready_status = all(item["ok"] for item in checks.values())
    if not ready_status:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {
        "status": "ready" if ready_status else "not_ready",
        "checks": checks,
    }

