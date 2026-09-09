from __future__ import annotations

from typing import Any

from workers.celery_app import celery_app


def _validate_smoke_payload(payload: dict[str, Any] | None) -> dict[str, Any]:
    if payload is None:
        return {}
    if not isinstance(payload, dict):
        raise TypeError("Smoke payload must be a JSON object")
    allowed = {"correlation_id"}
    unexpected = set(payload) - allowed
    if unexpected:
        raise ValueError("Smoke payload contains unsupported fields")
    correlation_id = payload.get("correlation_id")
    if correlation_id is not None and not isinstance(correlation_id, str):
        raise ValueError("correlation_id must be a string")
    return payload


@celery_app.task(name="infrastructure.smoke", queue="maintenance")
def infrastructure_smoke(payload: dict[str, Any] | None = None) -> dict[str, Any]:
    safe_payload = _validate_smoke_payload(payload)
    return {
        "status": "ok",
        "task": "infrastructure.smoke",
        "correlation_id": safe_payload.get("correlation_id"),
    }
