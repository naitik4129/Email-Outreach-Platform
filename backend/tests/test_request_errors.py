from __future__ import annotations

from fastapi import FastAPI, Query, status
from fastapi.testclient import TestClient

from app.core.errors import AppError
from app.main import create_app


def test_request_id_accepts_safe_header() -> None:
    client = TestClient(create_app())

    response = client.get("/api/v1/health", headers={"X-Request-ID": "phase0-123"})

    assert response.headers["X-Request-ID"] == "phase0-123"


def test_request_id_rejects_unsafe_header() -> None:
    client = TestClient(create_app())

    response = client.get("/api/v1/health", headers={"X-Request-ID": "bad value"})

    assert response.headers["X-Request-ID"] != "bad value"


def test_validation_error_shape() -> None:
    app = create_app()

    @app.get("/probe")
    def probe(count: int = Query()) -> dict[str, int]:
        return {"count": count}

    response = TestClient(app).get("/probe", headers={"X-Request-ID": "rid"})

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert response.json()["error"]["code"] == "validation_error"
    assert response.json()["error"]["request_id"] == "rid"


def test_application_error_shape() -> None:
    app = create_app()

    @app.get("/probe")
    def probe() -> None:
        raise AppError("phase0_error", "Phase 0 failure", status_code=409)

    response = TestClient(app).get("/probe", headers={"X-Request-ID": "rid"})

    assert response.status_code == 409
    assert response.json() == {
        "error": {
            "code": "phase0_error",
            "message": "Phase 0 failure",
            "request_id": "rid",
            "details": None,
        }
    }


def test_unexpected_error_shape() -> None:
    app: FastAPI = create_app()

    @app.get("/probe")
    def probe() -> None:
        raise RuntimeError("secret internals")

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get("/probe", headers={"X-Request-ID": "rid"})

    assert response.status_code == 500
    assert response.json() == {
        "error": {
            "code": "internal_error",
            "message": "Internal server error",
            "request_id": "rid",
            "details": None,
        }
    }

