from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import create_app


def test_health_endpoint() -> None:
    client = TestClient(create_app())

    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_success(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.api.v1.health.check_database",
        lambda settings: {"ok": True},
    )
    monkeypatch.setattr("app.api.v1.health.check_redis", lambda settings: {"ok": True})
    client = TestClient(create_app())

    response = client.get("/api/v1/ready")

    assert response.status_code == 200
    assert response.json()["status"] == "ready"


def test_ready_failure(monkeypatch) -> None:
    monkeypatch.setattr(
        "app.api.v1.health.check_database",
        lambda settings: {"ok": False, "error": "OperationalError"},
    )
    monkeypatch.setattr("app.api.v1.health.check_redis", lambda settings: {"ok": True})
    client = TestClient(create_app())

    response = client.get("/api/v1/ready")

    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
