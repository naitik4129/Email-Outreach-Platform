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


def test_unexpected_error_still_carries_cors_headers() -> None:
    """An unhandled exception must still pass back through CORSMiddleware.

    Regression test for a real incident: a plain @app.exception_handler
    (Exception) is pulled out of Starlette's ExceptionMiddleware and run by
    ServerErrorMiddleware instead (see fastapi.applications.FastAPI.
    build_middleware_stack), which sits OUTSIDE every app.add_middleware(...)
    middleware including CORSMiddleware. A response built there never gets
    Access-Control-Allow-Origin, so the browser reports a misleading "blocked
    by CORS policy" error instead of surfacing the actual 500 -- exactly what
    happened when an unrelated bug (a DB permission error) crashed a request
    from the frontend. See app/core/errors.py and app/core/middleware.py
    (unhandled_exception_middleware) for the fix.
    """
    app: FastAPI = create_app()

    @app.get("/probe")
    def probe() -> None:
        raise RuntimeError("secret internals")

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get(
        "/probe",
        headers={"Origin": "http://localhost:3000"},
    )

    assert response.status_code == 500
    assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"



def _probe_client(exc: Exception) -> TestClient:
    app: FastAPI = create_app()

    @app.get("/probe")
    def probe() -> None:
        raise exc

    return TestClient(app, raise_server_exceptions=False)


def _pool_timeout() -> Exception:
    from sqlalchemy.exc import TimeoutError as PoolTimeoutError

    return PoolTimeoutError("QueuePool limit of size 5 overflow 5 reached")


def _operational_error() -> Exception:
    from sqlalchemy.exc import OperationalError

    return OperationalError(
        "SELECT 1", {}, Exception("MaxClientsInSessionMode: max clients reached")
    )


def _psycopg_operational_error() -> Exception:
    import psycopg

    return psycopg.OperationalError("server closed the connection unexpectedly")


def test_transient_database_failures_are_503_with_retry_after_and_cors() -> None:
    for make in (_pool_timeout, _operational_error, _psycopg_operational_error):
        response = _probe_client(make()).get(
            "/probe", headers={"X-Request-ID": "rid", "Origin": "http://localhost:3000"}
        )

        assert response.status_code == 503, make.__name__
        assert response.headers["retry-after"] == "2"
        # Must still pass through CORS, or the browser reports a CORS error
        # instead of the retryable 503.
        assert (
            response.headers.get("access-control-allow-origin")
            == "http://localhost:3000"
        )
        assert response.json() == {
            "error": {
                "code": "service_unavailable",
                "message": "The service is busy. Please retry.",
                "request_id": "rid",
                "details": None,
            }
        }


def test_database_defects_stay_500() -> None:
    from sqlalchemy.exc import IntegrityError, ProgrammingError

    for exc in (
        ProgrammingError("SELECT f()", {}, Exception("function f() does not exist")),
        IntegrityError("INSERT", {}, Exception("duplicate key")),
    ):
        response = _probe_client(exc).get("/probe")

        assert response.status_code == 500
        assert response.json()["error"]["code"] == "internal_error"


def test_transient_database_failure_log_names_the_cause() -> None:
    import logging

    # create_app() reconfigures the root logger's handlers, which drops pytest's
    # caplog handler, so attach a collector to the module logger directly.
    class _Collect(logging.Handler):
        def __init__(self) -> None:
            super().__init__(logging.WARNING)
            self.messages: list[str] = []

        def emit(self, record: logging.LogRecord) -> None:
            self.messages.append(record.getMessage())

    collector = _Collect()
    module_logger = logging.getLogger("app.core.middleware")
    module_logger.addHandler(collector)
    try:
        _probe_client(_operational_error()).get("/probe")
    finally:
        module_logger.removeHandler(collector)

    assert any(
        "Database temporarily unavailable" in m and "MaxClientsInSessionMode" in m
        for m in collector.messages
    )
